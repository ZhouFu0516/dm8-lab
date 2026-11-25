# R1 独立审阅后的定向离线修复

本记录对应 2026-11-25 独立审阅后的最小修复。本轮未启动 VM、数据库或 QGA 写操作，未执行真实 RESTORE、RECOVER、dminit 或 BACKUP。

修复范围：

- 固定恢复客体为 `dm8-restore-c`；目标必须是 MVP 挂载根下的独立子目录，mapping/logdir 与目标位于同一授权挂载。
- 对路径穿越、受保护目录、控制字符和 Shell 特殊字符进行拒绝；Shell 变量使用可靠引用。
- 增加 MAPPED FILE 路径文本校验，覆盖 `data_path`、`mirror_path` 等路径字段，并规范化检查越界。
- COUNT 输出改为按显式表名标记解析，不再依赖历史固定行数。
- 缺少完整验证时不得报告 PASS；CLI 返回码与报告状态一致。
- 为 virsh/QGA 本地调用增加超时，状态未知仍禁止自动重试非幂等操作。

离线验证：`PYTHONPATH=src python3 -m unittest discover -s tests -v`，46 项通过；dry-run 输出通过 `bash -n`。这些结果不构成 R1 真实自动恢复 PASS。历史 R3/R5 结论保持不变，真实 B1 仍需另行授权。
