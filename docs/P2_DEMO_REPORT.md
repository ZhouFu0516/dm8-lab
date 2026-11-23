# P2 离线 Demo 报告

执行时间：2026-11-23（本次开发会话）

## 范围

本报告记录真实 `/srv/dm8-lab/fixtures/hr/data` 源文件的 `SOURCE` 采集和离线比较。没有启动 VM，没有连接 DM8，也没有执行恢复或导入导出；因此结果不能称为真实数据库恢复成功。

## 实际源数据基线

| 表 | 行数 |
|---|---:|
| LOCATIONS | 48 |
| DEPARTMENTS | 160 |
| JOBS | 112 |
| EMPLOYEES | 600000 |
| JOB_HISTORY | 1800000 |
| SALARIES | 1599680 |
| 合计 | 4000000 |

manifest 校验状态为 `PASS`，逐个输入文件 SHA-256 与 `/srv/dm8-lab/fixtures/hr/source_manifest.json` 一致。基线文件位于 `outputs/p2-demo/source-before/baseline.json` 和 `outputs/p2-demo/source-after/baseline.json`。

一次完整源采集的实测指标：约 93.838 秒，峰值 RSS 约 211.7 MiB（`getrusage`，Linux KiB 转换），采用逐行处理和临时 SQLite 主键去重。最终完整 Demo 两次采集总耗时 199.687 秒。

## Demo 结果

- 正常 SOURCE → SOURCE 比较：`PASS`，六表行数、主键摘要、数据摘要、源 DDL 结构和约束均一致。
- 异常比较：`FAIL`。构造了 EMPLOYEES 行数变化、数据摘要变化和主键摘要变化，比较器报告 3 项差异。
- 证据不足比较：`INCOMPLETE`。移除 EMPLOYEES 数据摘要后，未错误返回 PASS。
- 在线状态：`ONLINE_NOT_VERIFIED`。没有可用 DM8 连接，本轮只测试适配层脚本生成和 disql 标记解析。

结果文件（运行产物在 `.gitignore` 中排除，执行 Demo 会重新生成）：

- `outputs/p2-demo/normal-comparison.{json,md,csv}`
- `outputs/p2-demo/anomaly-comparison.{json,md,csv}`
- `outputs/p2-demo/incomplete-comparison.{json,md,csv}`
- `outputs/p2-demo/demo-summary.json`

## 自动测试

命令：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

结果：26 项通过，0 项失败。覆盖正常采集、重复摘要、输入顺序变化、字段/行数/主键/聚合变化、结构和约束变化、重复主键、缺失文件、非法配置、缺失证据、跨来源比较、版本不兼容、disql 输出解析、RESTORED 来源标记及 CLI 退出码。

## 已知限制

- SOURCE 结构来自项目 DDL 配置；DM8 系统目录查询未在真实版本上核实。
- 索引元数据在 SOURCE 中记录为未单独声明，在 DM8 在线模式保持 `NOT_COLLECTED`。
- 当前没有 `DATABASE` 或 `RESTORED` 实测基线。
- 在线 disql 连接、DM8 编码差异、实际 catalog 字段和恢复日志仍待 P1 环境恢复后验证。
