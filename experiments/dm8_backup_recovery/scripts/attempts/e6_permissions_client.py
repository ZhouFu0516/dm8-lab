# 失败原因: LABCLIENT 无 DUMP 权限 -2855
#!/usr/bin/env python3
import subprocess
pw = open("/etc/dm8-lab/labclient", encoding="utf-8").read().strip()
sql = (
    f"conn LABCLIENT/{pw}@DM8LAB\n"
    "select USER from dual;\n"
    "select * from session_roles;\n"
    "select owner,table_name,grantee,privilege from all_tab_privs where grantee='LABCLIENT' and owner='HR_TEST';\n"
    "exit;\n"
)
p = subprocess.run(["/opt/dmdbms/bin/disql", "/nolog"], input=sql, text=True,
                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
print("rc=" + str(p.returncode))
print(p.stdout.replace(pw, "[REDACTED]"))
raise SystemExit(p.returncode)
