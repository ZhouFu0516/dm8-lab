# 实验脚本索引

| 脚本 | 用途 / 输入 | 输出 | 状态 | 依赖 |
|---|---|---|---|---|
| `e6_sysdba_standby.py` | standby1 远程主库采 COUNT 并执行 dexp；客体环境与导出目录 | CSV、脱敏日志、dmp | E6 已验证 | Python、DM8 client、客体内凭据 |
| `qga_copy_guest_file.py` | QGA 文件传输；源/目标 VM 与文件路径 | 复制结果/校验信息 | E6 已验证 | libvirt QGA |
| `e7_dminit_help.py` | 筛选本机 dminit 帮助；无参数 | 帮助摘要 | 辅助诊断 | Python、DM8 |
| `attempts/e6_probe_client.py` | client 连接/工具探测 | 探测日志 | 失败记录：LABCLIENT 无 DUMP 权限 -2855 | Python、DM8 client |
| `attempts/e6_inspect_client.py` | client 导出前置检查 | 检查日志 | 失败路径：LABCLIENT 无 DUMP 权限 -2855 | Python、DM8 client |
| `attempts/e6_permissions_client.py` | 只读权限检查 | 权限摘要 | 失败路径：LABCLIENT 无 DUMP 权限 -2855 | Python、DM8 client |
| `attempts/e6_run_client.py` | LABCLIENT dexp 尝试 | 导出日志/无效 dmp | 失败路径：LABCLIENT 无 DUMP 权限 -2855 | Python、DM8 client |

凭据只在客体内运行时读取，不写入脚本、argv、日志或 Git。
