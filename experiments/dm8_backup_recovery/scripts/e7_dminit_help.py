#!/usr/bin/env python3
import subprocess
p = subprocess.run(["/opt/dmdbms/bin/dminit", "HELP"], stdout=subprocess.PIPE,
                   stderr=subprocess.STDOUT, text=True, timeout=15)
for line in p.stdout.splitlines():
    if any(k in line for k in ("SYSDBA_PWD", "DB_NAME", "INSTANCE_NAME", "PORT_NUM", "ENABLE_LOCAL_OSAUTH", "PAGE_SIZE", "EXTENT_SIZE", "LENGTH_IN_CHAR", "CASE_SENSITIVE", "CHARSET")):
        print(line)
raise SystemExit(p.returncode)
