"""Parse explicitly marked DIsql results, including SQL> and row numbers."""
import re


def check_sql_errors(output):
    if re.search(r'DISQL-\d+|DM\[-?\d+\]|\[-\d+\]|syntax error|invalid table|error in line', output, re.I):
        raise ValueError('DIsql reported a SQL error')


def result_lines(output):
    check_sql_errors(output)
    for raw in output.splitlines():
        line = re.sub(r'^(?:SQL>\s*)+', '', raw.strip()).strip()
        if not line or re.match(r'^(used time:|login used time|disql V|Server\[)', line, re.I):
            continue
        yield line


def marked_values(output, marker):
    """Exactly one named section; other marked results cannot supply its value."""
    lines = list(result_lines(output))
    if lines.count(marker) != 1:
        raise ValueError('missing or duplicate result marker: ' + marker)
    values = []
    for line in lines[lines.index(marker) + 1:]:
        if line.startswith('__'):
            break
        # DIsql may emit a row index, or the query may have disabled it.
        values.append(re.sub(r'^\d+\s+', '', line))
    if len(values) != 1:
        raise ValueError('missing or ambiguous query result: ' + marker)
    return values[0]


def parse_primary_preflight(output, expected_instance='DM8RT01'):
    fields = [x.strip().upper() for x in marked_values(output, '__B1_INSTANCE__').split('|')]
    if fields != [expected_instance.upper(), 'OPEN', 'PRIMARY']:
        raise ValueError('source is not the expected OPEN PRIMARY instance')
    archive = marked_values(output, '__B1_ARCHIVE__').upper()
    if archive not in ('Y', 'YES', '1', 'ARCHIVE'):
        raise ValueError('archive mode is not enabled')
    return dict(instance=fields[0], status=fields[1], mode=fields[2], archive=archive)


def parse_instance_status(output, marker='__RESTORED_INSTANCE__'):
    fields = [x.strip().upper() for x in marked_values(output, marker).split('|')]
    if len(fields) != 3:
        raise ValueError('malformed instance status result')
    return dict(instance=fields[0], status=fields[1], mode=fields[2])
