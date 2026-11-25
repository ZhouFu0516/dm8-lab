"""Guest operations bundled with their parser by I1 through QgaRunner.

Only sql/probe operations are used by --preflight-only. No guest package install.
"""
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import subprocess
from dm8_verify.collectors.disql_results import check_sql_errors, parse_primary_preflight


def scrub(text, password):
    text = text.replace(password, '[REDACTED]') if password else text
    return re.sub(r'(?i)(CONNECT\s+[^/\s]+/)(?:"[^"]*"|[^@\r\n]*)@', r'\1[REDACTED]@', text)


def sql_result(request):
    credential = Path(request['credential_file'])
    info = credential.stat()
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or credential.is_symlink():
        raise ValueError('credential file must be a root-owned regular 0600 file')
    password = credential.read_text().strip()
    if not re.fullmatch(r'[A-Za-z0-9_!#$%&*+,.=?^~-]+', password):
        raise ValueError('credential format unsupported by the fixed DIsql stdin adapter')
    # Suppress variable expansion and echoed CONNECT text. Never include secret in argv.
    lines = ['SET ECHO OFF', 'SET VERIFY OFF', 'SET DEFINE OFF', 'SET HEADING OFF',
             'SET FEEDBACK OFF', 'SET PAGESIZE 0', 'SET LINESIZE 32767', "SET SQLPROMPT ''",
             'CONNECT %s/%s@%s:%s' % (request['username'], password, request['db_host'], request['db_port'])]
    command = ['runuser', '-u', 'dmdba', '--', '/opt/dmdbms/bin/disql', '/nolog']
    try:
        process = subprocess.run(command, input='\n'.join(lines + request['queries'] + ['EXIT SUCCESS', '']),
                                 capture_output=True, text=True, timeout=request.get('sql_timeout', 90),
                                 env=dict(os.environ, LD_LIBRARY_PATH='/opt/dmdbms/bin'))
    except subprocess.TimeoutExpired:
        # A timed-out BACKUP may still be running in the server; never retry.
        return dict(status='BLOCKED', exitcode=None, unknown=True, stdout='', stderr='',
                    reason='DIsql timeout; database command outcome unknown')
    output = scrub(process.stdout, password)
    errors = scrub(process.stderr, password)
    result = dict(status='PASS' if process.returncode == 0 else 'FAIL',
                  exitcode=process.returncode, stdout=output, stderr=errors)
    if process.returncode != 0:
        result['reason'] = 'DIsql exited with status %s' % process.returncode
    try:
        check_sql_errors(output + '\n' + errors)
    except ValueError as exc:
        result.update(status='FAIL', reason=str(exc))
    return result


def nearest_existing(path):
    while not path.exists():
        path = path.parent
    return path


def probe(request):
    mount = Path(request['mount'])
    if mount.resolve() != mount or not mount.is_dir():
        raise ValueError('mount path absent or symlinked')
    command = ['findmnt', '-J', '-T', str(mount), '-o', 'SOURCE,TARGET,FSTYPE,UUID']
    p = subprocess.run(command, capture_output=True, text=True, check=True, timeout=10)
    fs = json.loads(p.stdout)['filesystems'][0]
    if (fs['target'] != str(mount) or fs['source'] != request['device'] or
            fs['fstype'] != 'ext4' or fs.get('uuid') != request['uuid']):
        raise ValueError('mount/device/UUID identity differs from approved fixed environment')
    for value in request['new_paths']:
        path = Path(value)
        if path == mount or mount not in path.parents or path.resolve() != path:
            raise ValueError('write path escapes mount or crosses a symlink')
        for protected_path in request.get('protected', []):
            protected_item=Path(protected_path)
            if path == protected_item or path in protected_item.parents or protected_item in path.parents:
                raise ValueError('planned write path overlaps a protected asset')
        if path.exists() or path.is_symlink():
            raise ValueError('new path already exists: ' + str(path))
        ancestor = nearest_existing(path.parent)
        if os.stat(ancestor).st_dev != os.stat(mount).st_dev:
            raise ValueError('write path is on an unexpected filesystem')
        if request.get('check_dmdba_write'):
            p = subprocess.run(['runuser', '-u', 'dmdba', '--', 'test', '-w', str(ancestor)])
            if p.returncode:
                raise ValueError('dmdba cannot write planned parent: ' + str(ancestor))
    vfs = os.statvfs(mount)
    available = vfs.f_bavail * vfs.f_frsize
    if available < request['required_bytes']:
        raise ValueError('insufficient free space: %s < %s' % (available, request['required_bytes']))
    if request.get('port'):
        p = subprocess.run(['ss', '-ltnH'], capture_output=True, text=True, check=True, timeout=10)
        for row in p.stdout.splitlines():
            columns = row.split()
            if len(columns) > 3 and columns[3].rsplit(':', 1)[-1] == str(request['port']):
                raise ValueError('restore port already listening')
    protected = {p: os.path.lexists(p) for p in request.get('protected', [])}
    return dict(status='PASS', filesystem=fs, available_bytes=available,
                required_bytes=request['required_bytes'], protected_exists=protected,
                new_paths=request['new_paths'])


def prepare_input(request):
    path = Path(request['path'])
    parent = path.parent
    if path.exists() or path.is_symlink():
        raise ValueError('restore input path already exists')
    if parent.resolve() != parent or parent.is_symlink():
        raise ValueError('restore input parent traverses symlink')
    if parent.exists() and (not parent.is_dir() or os.stat(parent).st_dev != os.stat(request['mount']).st_dev):
        raise ValueError('restore input root is not a directory on the approved volume')
    owner=pwd.getpwnam('dmdba')
    if not parent.exists():
        parent.mkdir(mode=0o700)
        os.chown(parent,owner.pw_uid,owner.pw_gid)
    if parent.is_symlink() or parent.resolve() != parent:
        raise ValueError('restore input root changed during creation')
    # Exclusive creation: never reuse or truncate an existing run directory.
    path.mkdir(mode=0o700)
    os.chown(path,owner.pw_uid,owner.pw_gid)
    if path.is_symlink() or path.resolve() != path:
        raise ValueError('restore input path changed during creation')
    return dict(status='PASS', path=str(path), root=str(parent))


def primary(request):
    # Role/archive results are parsed before any mkdir or BACKUP.
    pre = sql_result(dict(request, queries=request['preflight_queries']))
    if pre['status'] != 'PASS':
        return dict(status=pre['status'], preflight=pre)
    parse_primary_preflight(pre['stdout'])
    volume_probe=probe(request)
    log = Path(request['logdir'])
    backup = Path(request['backupset'])
    if log.exists() or backup.exists():
        raise ValueError('primary target/log directory exists')
    log.mkdir(mode=0o700, parents=True)  # create isolated log root; never reuse the run directory
    (log / 'preflight.log').write_text(pre['stdout'] + pre['stderr'])
    subprocess.run(['runuser', '-u', 'dmdba', '--', 'mkdir', '-p', '--', str(backup.parent)], check=True)
    result = sql_result(dict(request, queries=["BACKUP DATABASE FULL BACKUPSET '%s';" % backup]))
    (log / 'backup.log').write_text(result.get('stdout', '') + result.get('stderr', ''))
    if result.get('unknown'):
        return dict(status='BLOCKED', unknown=True, reason=result['reason'], backup=result)
    if result['status'] != 'PASS':
        return dict(status='FAIL', reason=result.get('reason'), backup=result)
    check_sql_errors(result['stdout'] + result['stderr'])
    if result['exitcode'] != 0 or not re.search(r'executed successfully', result['stdout'], re.I):
        return dict(status='FAIL', backup=result)
    command = ['runuser', '-u', 'dmdba', '--', '/opt/dmdbms/bin/dmrman', 'USE_AP=2',
               "CTLSTMT=CHECK BACKUPSET '%s' LEVEL 1" % backup]
    try:
        check = subprocess.run(command, capture_output=True, text=True, timeout=request.get('sql_timeout', 90))
    except subprocess.TimeoutExpired:
        return dict(status='BLOCKED', backup=result, check=dict(unknown=True, reason='CHECK timeout'))
    output = check.stdout + check.stderr
    (log / 'check.log').write_text(output)
    check_sql_errors(output)
    if check.returncode != 0 or not re.search(r'check backupset successfully', output, re.I):
        return dict(status='FAIL', backup=result, check=dict(exitcode=check.returncode, output=output))
    files = sorted(p for p in backup.iterdir() if p.suffix.lower() in ('.bak', '.meta'))
    if not any(p.suffix.lower() == '.bak' for p in files) or not any(p.suffix.lower() == '.meta' for p in files):
        raise ValueError('backup file set incomplete')
    manifest, hashes = [], []
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ValueError('backup file not a regular file')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        manifest.append('%s %s' % (path.name, path.stat().st_size))
        hashes.append('%s  %s' % (digest.hexdigest(), path))
    (log / 'manifest.txt').write_text('\n'.join(manifest) + '\n')
    (log / 'sha256.txt').write_text('\n'.join(hashes) + '\n')
    return dict(status='PASS', preflight=pre, backup=result,
                check=dict(exitcode=check.returncode, output=output),
                manifest='\n'.join(manifest), hashes='\n'.join(hashes))


def guest_main(request):
    os.umask(0o077)
    try:
        if request['operation'] == 'sql':
            result = sql_result(request)
        elif request['operation'] == 'probe':
            result = probe(request)
        elif request['operation'] == 'primary':
            result = primary(request)
        elif request['operation'] == 'prepare-input':
            result = prepare_input(request)
        else:
            raise ValueError('unknown operation')
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        # Exception text may contain tool argv. Keep diagnostics to non-secret gate messages.
        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(json.dumps(dict(status='BLOCKED', reason=reason, exception_type=type(exc).__name__)))
        raise SystemExit(10)

