# I1 最小集成说明

默认只生成流程，不执行任何 VM、QGA、BACKUP 或 RESTORE 操作：

```bash
PYTHONPATH=src python3 -m dm8_mvp run \
  --config config/mvp-b1.example.json --dry-run
```

输出包含：

1. PRIMARY 客体内受控凭据文件读取、`DM8RT01/OPEN/PRIMARY` 和归档预检；
2. `BACKUP DATABASE FULL BACKUPSET` 及严格 CHECK；
3. E4 使用过的 QGA `guest-file-read`/`guest-file-write` 传输计划；
4. 传输后将实际恢复目录接入现有 R1 配置。

显式 `--execute` 才会进入 PRIMARY QGA 执行、manifest 解析、文件传输和 R1 调用路径。当前不运行该选项。

若配置 `source_db_config`，I1 会调用现有 `dm8_verify.collect_dm8_counts` 在备份前后采集源库 ROW_COUNT，并用既有 comparator 做 ROW_COUNT 级比较。源库与恢复库的实际端到端比较仍需授权的 DM8 实验确认。

运行前人工确认：PRIMARY 角色和归档状态、备份目录空间、restore-c 目标目录/磁盘隔离、E4/E7 资产不在目标范围内，以及传输后目标目录可用空间。
