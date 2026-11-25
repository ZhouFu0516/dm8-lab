#!/usr/bin/env python3
import csv, hashlib, os, re, subprocess, time

TABLES = ["LOCATIONS", "DEPARTMENTS", "JOBS", "EMPLOYEES", "JOB_HISTORY", "SALARIES"]
OUT = "/data/e6/export-sysdba"
os.makedirs(OUT, exist_ok=True)
with open("/etc/dm8-lab/sysdba", encoding="utf-8") as f:
    password = f.read().strip()

def run_sql(out_csv):
    sql = f"conn SYSDBA/{password}@192.168.241.11:5236\nselect 1 from dual;\n" + "".join(
        f"select count(*) from HR_TEST.{t};\n" for t in TABLES
    ) + "select INSTANCE_NAME,STATUS$,MODE$ from v$instance;\nexit;\n"
    p = subprocess.run(["/opt/dmdbms/bin/disql", "/nolog"], input=sql, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    safe = p.stdout.replace(password, "[REDACTED]")
    with open(out_csv + ".log", "w", encoding="utf-8") as f: f.write(safe)
    vals = re.findall(r"\n\s*1\s+(\d+)\s*\n", safe)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["table_name", "count"])
        for t, v in zip(TABLES, vals[-6:]): w.writerow([t, v])
    return p.returncode, safe, len(vals)

ts = time.strftime("%Y%m%d-%H%M%S")
before = OUT + f"/e6-source-counts-{ts}-before.csv"
rc, safe, nvals = run_sql(before)
print("probe_rc=" + str(rc) + " count_values=" + str(nvals))
if rc != 0 or "DM8RT01" not in safe or "PRIMARY" not in safe or nvals < 6:
    print(safe)
    raise SystemExit(2)
dmp = OUT + "/e6_hr.dmp"
if os.path.exists(dmp): raise SystemExit("refusing existing dump")
start = time.time()
p = subprocess.run(["/opt/dmdbms/bin/dexp", "USERID=SYSDBA@192.168.241.11:5236",
                    "DIRECTORY=" + OUT, "FILE=e6_hr.dmp", "LOG=e6_hr.log",
                    "SCHEMAS=HR_TEST"], input=password + "\n", text=True,
                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
elapsed = time.time() - start
safe_exp = p.stdout.replace(password, "[REDACTED]")
with open(OUT + "/e6_dexp_stdout.log", "w", encoding="utf-8") as f: f.write(safe_exp)
after = OUT + f"/e6-source-counts-{ts}-after.csv"
rc2, safe2, _ = run_sql(after)
size = os.path.getsize(dmp) if os.path.exists(dmp) else 0
sha = hashlib.sha256(open(dmp, "rb").read()).hexdigest() if os.path.exists(dmp) else "MISSING"
print(f"ts={ts} dexp_rc={p.returncode} after_rc={rc2} elapsed_s={elapsed:.3f} dmp_size={size} sha256={sha}")
print("dexp_tail=" + "\\n".join(safe_exp.splitlines()[-20:]))
raise SystemExit(0 if p.returncode == 0 and rc2 == 0 else 1)
