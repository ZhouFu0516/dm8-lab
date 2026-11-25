# 失败原因: LABCLIENT 无 DUMP 权限 -2855
#!/usr/bin/env python3
import os
for p in ("/data/e6/export/e6_hr.log", "/data/e6/export/e6_dexp_stdout.log",
          *[f for f in os.listdir("/data/e6/export") if f.startswith("e6-source-counts-")]):
    path = p if p.startswith("/") else "/data/e6/export/" + p
    if os.path.isfile(path):
        print("===== " + path + " =====")
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        print("".join(lines[-40:]))
print("FILES:")
for n in os.listdir("/data/e6/export"):
    p = "/data/e6/export/" + n
    print(n, os.path.getsize(p))
