# 一次受控 I1 实验的准备记录

日期：2026-11-25。真实实验已获授权；本轮仅完成只读准备，尚未执行 BACKUP/RESTORE。

- 只读预检使用版本 ba0d739。实际命令：
  `PYTHONPATH=src python3 -B -m dm8_mvp.integration --config config/mvp-b1.example.json --preflight-only`。
- 预检报告：`outputs/i1/i1-20261125T090136.244270Z-cce93a9b-i1.json`。
- 预检三阶段 PASS：PRIMARY 文件系统门禁、restore-c 文件系统门禁、源库六表采集。
  backup/check/source_after/transfer/restore/row_count_compare 全部 NOT EXECUTED。
- PRIMARY/standby1/standby2/monitor/restore-c 均运行；数据库与 Watcher 服务、Monitor 服务均 active。
  本轮没有用服务状态推断备库数据库角色。
- PRIMARY SQL 已通过真实 DM8RT01 / OPEN / PRIMARY / ARCH_MODE 预检。
  客机 Python 3.7.9 成功执行携带源码的请求；凭据不输出。
- PRIMARY /data：/dev/vdb，ext4，UUID 与配置一致；可用 12168515584 字节，
  预算要求 1677728640 字节。
- restore-c /data/mvp60：/dev/vdf，ext4，UUID 与配置一致；
  可用 41195724800 字节，预算要求 14009248640 字节（含历史恢复体积、备份估计、日志和25%余量）。
- restore-c 无运行中的 dmserver，15242 无监听；历史 R4/R5 目录存在，不复用。
  E4/E7 磁盘仍为既有 vdb/vdc，未挂载或修改它们。
- 源库六表实测 COUNT：LOCATIONS=48，DEPARTMENTS=160，JOBS=112，
  EMPLOYEES=600000，JOB_HISTORY=1800000，SALARIES=1599680，总计4000000。
  这是本轮只读观测，真实备份前后将重新采集；不将历史4000000视为硬编码验收值。

发现的实际准备阻断：PRIMARY 的 i1、i1-logs 根目录尚不存在。
原 primary() 用 log.mkdir() 无法创建日志父目录。本轮仅将其改为
log.mkdir(mode=0o700, parents=True)，保留已存在运行目录停止及卷/路径门禁。
新增本地临时目录行为测试；全套70项实际通过。尚未在客机创建任何实验目录。

本地原始记录：`/srv/dm8-lab/claude-review-i1-20261125/outputs/i1-live-readiness-xK4aWy/`。
其中 readonly_probe.py 和每台VM的 readonly.json 保存 shell 命令、
guest-exec / guest-exec-status 响应、最终客机退出码和解码输出。
不包含凭据文件内容，不提交 outputs。

拟用配置：`/srv/dm8-lab/claude-review-i1-20261125/outputs/i1-live-readiness-xK4aWy/i1-e2e.config.json`。
固定源/目标和目录根不变；每次实际运行由入口生成唯一 run_id、新恢复 db_name、
备份/接收/映射/日志路径。仅将 PRIMARY QGA 总时限设为1800秒，
单文件传输时限设为7200秒，分块仍为64KiB。
7200秒为首轮保守等待上限，不代表已经测得吞吐。

执行目录：`/srv/dm8-lab/claude-review-i1-20261125`；
原 `/srv/dm8-lab/dev` 未修改，不能在旧目录执行。

仍需实验负责人确认的条件：实验期间没有其他人员/程序对 HR_TEST 六表执行
DML、DDL、导入或压测。软件仍会比较备份前后COUNT；
若发生变化，停止传输/恢复并报告可比性不足。
六表COUNT相同不证明严格事务时点或全库内容一致。

本轮准备阶段没有执行真实 BACKUP、RESTORE、RECOVER、dminit、磁盘操作或实例启停。
