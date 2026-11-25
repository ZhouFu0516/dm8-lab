# P2.2 DM8 在线验证报告

状态：`DATABASE_BASELINE_AND_REPEAT_COUNT_PASS`
采集时间：2026-11-23（Asia/Shanghai）

## 已验证

- 三个 DM8 实验客体已受控启动，主库地址 `192.168.241.11:5236`。
- `dm8-client`（VM5）已启动，地址 `192.168.241.15`；交接凭据文件存在且为 `0600`、root 所有，服务配置和 `disql` 均存在。
- `DM8LAB` 服务名配置指向三节点 DM8 地址。
- 麒麟 Linux V10、DM 服务、watcher、`mdmonitor` 服务正常；主库归档目录存在日志文件。
- P2 比较器已支持 `ROW_COUNT`、`DATA_ONLY`、`FULL`；COUNT-only 适配器只执行 `SELECT COUNT(*)`，并拒绝把 SQL 错误或缺失分段当作零行。
- `ROW_COUNT` 比较输出字段为 `table_name,before_count,after_count,difference,status`，并明确只证明逐表行数，不证明内容一致性。

## 采集结果与证据边界

首次尝试脚本对 Python stdin 命令换行做了双重转义，disql 未收到预期多行命令；此前报告的认证失败结论证据不足，现予更正。修正后 VM5 到三节点 5236/TCP 均可达，LABCLIENT 经 `DM8LAB` 成功执行只读 SQL。实例查询返回 `DM8RT01|OPEN|PRIMARY`。

使用 VM5 上 disql `/nolog`，由客体内 Python 进程读取现存 LABCLIENT 文件并通过 stdin 提交 `CONNECT LABCLIENT/<runtime-secret>@DM8LAB` 与只读 SQL；口令不进入 argv 或输出。两次独立 `COUNT(*)` 采集成功，表清单来自 `ALL_TABLES`，HR_TEST 当前六张表：

| 表 | 第一次 COUNT(*) | 重复 COUNT(*) | 差异 |
|---|---:|---:|---:|
| LOCATIONS | 48 | 48 | 0 |
| DEPARTMENTS | 160 | 160 | 0 |
| JOBS | 112 | 112 | 0 |
| EMPLOYEES | 600000 | 600000 | 0 |
| JOB_HISTORY | 1800000 | 1800000 | 0 |
| SALARIES | 1599680 | 1599680 | 0 |
| 合计 | 4000000 | 4000000 | 0 |

采集时间（UTC）：`2026-11-23T05:07:28.461098+00:00` 和 `2026-11-23T05:07:28.494574+00:00`；每次耗时约 0.033 秒、0.035 秒。实例查询确认 `DM8RT01 / OPEN / PRIMARY`。数据库版本字符串本轮未采集（查询输出未能确认），不据工具版本推断数据库版本。摘要、聚合统计及列/约束/索引元数据未采集，ROW_COUNT 结果不代表完整内容一致性。

证据文件位于 `outputs/p2-online/database-before/`、`database-repeat/` 和 `count-comparison/`。比较结果为 `DATABASE → DATABASE / ROW_COUNT / PASS`，无错误、缺表或计数差异。

## 准备好的执行入口

重复采集 diff 命令结果已保存。未来在隔离恢复目标采集 RESTORED 基线后执行：

```bash
PYTHONPATH=src python3 -m dm8_verify.cli compare \
  --before outputs/p2-online/database-before/baseline.json \
  --after outputs/p2-online/database-after/baseline.json \
  --level ROW_COUNT \
  --output outputs/p2-online/count-comparison
```

## 数据库写入与恢复边界

本轮仅执行只读查询，未执行数据库写入、导入、备份或恢复；未覆盖任何 qcow2、主库或备库数据。交接备份文件存在且 SHA-256 与交接值匹配：`b93dd7549d199a79326d54586cdf461808438fb51ac32a917ea8d369ea909ed3`。物理恢复（`dmrman`）和逻辑导出/导入（`dexp`/`dimp`）仅完成工具帮助核查，正式实验需独立恢复目标。
