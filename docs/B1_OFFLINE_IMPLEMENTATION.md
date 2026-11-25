# B1 离线最小实现

本模块只覆盖固定实验环境的正常路径，不执行真实 PRIMARY BACKUP。

```bash
PYTHONPATH=src python3 -m dm8_mvp backup \
  --config config/mvp-b1.example.json --dry-run
```

dry-run 输出三部分：

1. 只读 PRIMARY/OPEN/归档预检 SQL；
2. 官方联机全量物理备份语句：`BACKUP DATABASE FULL BACKUPSET '<目录>'`；
3. `dmrman USE_AP=2` 的 `CHECK BACKUPSET` 脚本。

`parse_preflight_output` 要求实例为 `OPEN|PRIMARY` 且归档开启；`parse_check_output` 要求 CHECK 成功标记。`build_r1_input` 将 B1 备份目录接入现有 R1 配置，不重复实现恢复流程。

当前密码仅保留为运行时注入占位符，dry-run 不包含真实凭据。真实连接、PRIMARY BACKUP、传输和 E2E 恢复均需另行授权。
