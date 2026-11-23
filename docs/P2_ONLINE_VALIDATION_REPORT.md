# P2.2 DM8 在线验证报告

状态：`BLOCKED_BY_CREDENTIALS`  
采集时间：2026-11-23（Asia/Shanghai）

## 已验证

- 三个 DM8 实验客体已受控启动，主库地址 `192.168.241.11:5236`。
- 麒麟 Linux V10、DM 服务、watcher、`mdmonitor` 服务正常；主库归档目录存在日志文件。
- P2 比较器已支持 `ROW_COUNT`、`DATA_ONLY`、`FULL`；COUNT-only 适配器只执行 `SELECT COUNT(*)`，并拒绝把 SQL 错误或缺失分段当作零行。
- `ROW_COUNT` 比较输出字段为 `table_name,before_count,after_count,difference,status`，并明确只证明逐表行数，不证明内容一致性。

## 尚未采集（不得伪造）

由于没有合规只读凭据，以下 DATABASE 证据不存在：

- `outputs/p2-online/database-before/baseline.json`
- `outputs/p2-online/database-before/table_counts.csv`
- DM8 SQL 版本、实例状态、`HR_TEST` 表存在性及六表真实 `COUNT(*)`
- 在线摘要、聚合统计、列/约束/索引元数据

因此没有生成 RESTORED 基线，也没有执行 `DATABASE → RESTORED` diff。SOURCE 离线的 4,000,000 行不能替代数据库实际行数。

## 准备好的执行入口

凭据注入后，建议执行：

```bash
export DM8_VERIFY_PASSWORD='由安全凭据注入'
PYTHONPATH=src python3 -m dm8_verify.cli collect-db \
  --db-config /secure/dm8-primary.json \
  --level ROW_COUNT \
  --output outputs/p2-online/database-before
```

恢复到隔离目标后，将 `--source-type RESTORED` 写入独立目录，再执行：

```bash
PYTHONPATH=src python3 -m dm8_verify.cli compare \
  --before outputs/p2-online/database-before/baseline.json \
  --after outputs/p2-online/database-after/baseline.json \
  --level ROW_COUNT \
  --output outputs/p2-online/count-comparison
```

## 数据库写入与恢复边界

本轮未执行任何数据库写入、导入、备份或恢复；未覆盖任何 qcow2、主库或备库数据。物理恢复（`dmrman`）和逻辑导出/导入（`dexp`/`dimp`）仅完成工具帮助核查，正式实验需独立恢复目标。
