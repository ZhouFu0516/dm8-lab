# I1 真实 E2E 前定向修复

本轮仅进行代码、Mock、dry-run 和 shell 静态验证，未启动 VM、调用真实 QGA、连接数据库或执行 BACKUP/RESTORE。

已修复：

- R1 只有显式 `--execute` 才执行；I1 `--execute` 明确向 R1 传递该开关。
- E4 QGA 传输默认块大小固定为 64 KiB，并由 I1 显式传参；保留实际字节数检查和目标 SHA-256 检查。
- 每次 I1 运行生成 `run_id`，备份、传输、恢复目标、映射、日志和报告目录均使用该 ID；不复用 R5 目录。
- 源库前后 ROW_COUNT 比较不通过时立即停止传输和恢复，报告 `INCOMPLETE`。
- manifest 仅收集 `.bak/.meta`，要求非空；SHA-256 使用 `pipefail`，并要求文件集合一致。
- 恢复目标父目录通过 QGA 预检创建，已存在目标立即停止。
- R1 报告按本次 `run_id` 精确读取，不按修改时间选择历史报告。
- QGA 状态未知记录为 `BLOCKED`，不自动重试。

测试：`PYTHONPATH=src python3 -m unittest discover -s tests -v`，65 项通过；I1 dry-run 和生成的 PRIMARY 脚本均通过 `bash -n`。

真实 E2E 仍为 `NOT EXECUTED`，须在执行前人工确认 PRIMARY、空间、凭据、恢复磁盘归属以及 E4/E7 隔离。
