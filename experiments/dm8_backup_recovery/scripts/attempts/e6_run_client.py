# 失败原因: LABCLIENT 无 DUMP 权限 -2855
#!/usr/bin/env python3
import csv, hashlib, os, re, subprocess, time

TABLES = ["LOCATIONS", "DEPARTMENTS", "JOBS", "EMPLOYEES", "JOB_HISTORY", "SALARIES"]
OUT = "/data/e6/export"
os.makedirs(OUT, exist_ok=True)
with open("/etc/dm8-lab/labclient", encoding="utf-8") as f:
    pw = f.read().strip()

def sql_counts(path):
    sql = f"conn LABCLIENT/{pw}@DM8LAB\n" + "".join(
        f"select count(*) from HR_TEST.{t};\n" for t in TABLES
    ) + "exit;\n"
    p = subprocess.run(["/opt/dmdbms/bin/disql", "/nolog"], input=sql, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    safe = p.stdout.replace(pw, "[REDACTED]")
    counts = re.findall(r"\n\s*1\s+(\d+)\s*\n", safe)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["table_name", "count"])
        for table, count in zip(TABLES, counts): w.writerow([table, count])
        f.write("\n# disql_rc=" + str(p.returncode) + "\n")
        f.write("# raw_output follows in separate log\n")
    with open(path + ".log", "w", encoding="utf-8") as f:
        f.write(safe)
    if len(counts) != len(TABLES):
        print("count_parse_error: expected 6 numeric results, got " + str(len(counts)))
        print(safe)
        return 1
    return p.returncode

ts = time.strftime("%Y%m%d-%H%M%S")
before = OUT + f"/e6-source-counts-{ts}-before.csv"
start = time.time(); rc_before = sql_counts(before)
dmp = OUT + "/e6_hr.dmp"
if os.path.exists(dmp):
    raise FileExistsError("refusing to overwrite existing E6 dump")
export_start = time.time()
proc = subprocess.run(["/opt/dmdbms/bin/dexp", "USERID=LABCLIENT@DM8LAB",
                       "DIRECTORY=" + OUT, "FILE=e6_hr.dmp", "LOG=e6_hr.log",
                       "SCHEMAS=HR_TEST"], input=pw+"\n", text=True,
                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
export_elapsed = time.time() - export_start
with open(OUT+"/e6_dexp_stdout.log", "w", encoding="utf-8") as f:
    f.write(proc.stdout.replace(pw, "[REDACTED]"))
after = OUT + f"/e6-source-counts-{ts}-after.csv"
rc_after = sql_counts(after)
elapsed = time.time() - start
sha = hashlib.sha256(open(dmp, "rb").read()).hexdigest() if os.path.exists(dmp) else "MISSING"
size = os.path.getsize(dmp) if os.path.exists(dmp) else 0
print(f"ts={ts} before_rc={rc_before} dexp_rc={proc.returncode} after_rc={rc_after} total_elapsed_s={elapsed:.3f} export_elapsed_s={export_elapsed:.3f} dmp_size={size} sha256={sha}")
print("dexp_tail=" + "\\n".join(proc.stdout.replace(pw, "[REDACTED]").splitlines()[-10:]))
raise SystemExit(0 if proc.returncode == 0 and rc_before == 0 and rc_after == 0 else 1)
