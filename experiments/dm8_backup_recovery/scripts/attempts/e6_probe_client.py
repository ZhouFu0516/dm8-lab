# 失败原因: LABCLIENT 无 DUMP 权限 -2855
#!/usr/bin/env python3
"""Read runtime LABCLIENT credential inside dm8-client and run read-only probe."""
import subprocess

with open("/etc/dm8-lab/labclient", encoding="utf-8") as f:
    password = f.read().strip()
sql = (
    f"conn LABCLIENT/{password}@DM8LAB\n"
    "select 1 as OK from dual;\n"
    "select INSTANCE_NAME,STATUS$,MODE$ from v$instance;\n"
    "exit;\n"
)
result = subprocess.run(
    ["/opt/dmdbms/bin/disql", "/nolog"], input=sql, text=True,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15,
)
print("disql_rc=" + str(result.returncode))
print(result.stdout.replace(password, "[REDACTED]"))
raise SystemExit(result.returncode)
