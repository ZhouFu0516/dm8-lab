# DM8 已验证命令手册

本手册只收录本轮日志/记录中实际出现且成功或明确失败的做法；口令均省略。

## E1/E2 物理备份与 CHECK

- [原始] `BACKUP DATABASE FULL BACKUPSET '<绝对目录>'`：连接实例后执行联机全量物理备份；成功判据为 SQL executed successfully，目录含 `.bak`/分卷与 `.meta`。出处：`records/02_primary_backup.md`、`records/03_standby_backup.md`。
- [原始] `dmrman USE_AP=2 CTLSTMT="CHECK BACKUPSET '<备份集目录>'"`：以 `dmdba`、脚本文件执行，避免 DMAP 通信问题；成功返回 CHECK 成功。出处：`records/02_primary_backup.md`、`records/03_standby_backup.md`、`records/04_physical_restore.md`。
- [已知失败] 默认 DMAP 通道可报 `DM[-7169]`；`USE_AP=2` 为本环境已验证修正。路径必须逐字一致且备份分卷齐全。

## E4 物理恢复

- [原始] 先用 `DUMP BACKUPSET ... MAPPED FILE '<new-file>'` 生成映射；输出文件必须不存在，且路径全部落在恢复机独立盘。成功映射 SHA：`db2f831dfa8ffc74ea46604265d70c4f7e59edcd4fdc32149764a16bd9814854`。
- [原始] `dmrman USE_AP=2 CTLSTMT="RESTORE DATABASE '<dm.ini>' REUSE DMINI FROM BACKUPSET '<dir>' WITH BACKUPDIR '<parent>' MAPPED FILE '<mapping>'"`；语句写脚本、`runuser -u dmdba` 执行。RESTORE 成功不能只看提示，需退出码和后续 RECOVER 验证；本轮原始 RESTORE 退出码未捕获。
- [原始] `dmrman USE_AP=2 CTLSTMT="RECOVER DATABASE '<dm.ini>' FROM BACKUPSET '<dir>'"`，随后 `RECOVER DATABASE '<dm.ini>' UPDATE DB_MAGIC`；本轮均 rc=0。RESTORE/RECOVER 与排障耗时分开记录。
- [原始] 启动目标库前检查 `INSTANCE_NAME、PORT_NUM、CTL_PATH、SYSTEM_PATH、MAL_INI、ARCH_INI` 及路径隔离；本轮 `MAL_INI=0、ARCH_INI=0`。`ALTER_MODE_STATUS=2` 后 `ALTER DATABASE NORMAL; ALTER DATABASE OPEN;` 成功。
- [原始] OS 认证条件：`ENABLE_LOCAL_OSAUTH=1`，运行用户为 `dmdba` 并具备相应 OS 组；使用 `disql /nolog`，口令不进 argv/日志。

## E6 逻辑导出

- [原始] 客户端连接主库后先执行 `SELECT 1` 和实例角色检查，再采集 COUNT；`dexp USERID=SYSDBA@192.168.241.11:5236 DIRECTORY=/data/e6/export-sysdba FILE=e6_hr.dmp LOG=e6_hr.log SCHEMAS=HR_TEST` 成功，rc=0，六表导出。
- [原始] dexp 密码通过客体脚本 stdin 提示注入或 0600 PARFILE；不写入命令行、日志、Git。文件落在执行 dexp 的客户端节点。
- [已知失败] LABCLIENT 导出返回 `-2855 No dump privilege`，rc=2；不要自行 GRANT，改用已授权的只读账号或经审批的最小授权。

## 传输与校验

- [原始] 复制后两端执行 `sha256sum` 比对；有效 `e6_hr.dmp` 为 157373084 字节，SHA-256 `1b95ca136f5e8b7926e40bc03407bbfe4e06cf66868054e0046980baa57623ff`。出处：E6 日志、`results/e6-source-counts-*.csv`。
- [摘要] E7 导入尚未完成；不得以 dimp 提示或中断前写入量判定 PASS。后续应使用独立 DM8E7、`PARALLEL=1`、后台日志及真实 rc，再逐表 COUNT Diff。
