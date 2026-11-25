# R1 收敛报告

状态：本报告基于已取得证据的收敛审计；本轮未执行新的 RESTORE、RECOVER、UPDATE DB_MAGIC、dminit、磁盘变更或实例启停。

## A. 已证实事实

### R3：物理恢复可行性（人工辅助）

`dm8-restore-c` 的 `/dev/vde`、`/data/mvp40/DM8RT01_R3` 为本次 R3 目标。R3 已真实完成 CHECK、DMRMAN 映射、RESTORE、RECOVER、UPDATE DB_MAGIC，并由人工命令启动和验证：

- CHECK：`/data/mvp40/mvp-logs-dm8rt01-r3/check.log`
- 映射：`/data/mvp40/restore-paths-dm8rt01-r3.txt`
- RESTORE：`/data/mvp40/mvp-logs-dm8rt01-r3/restore.log`
- RECOVER：`/data/mvp40/mvp-logs-dm8rt01-r3/recover.log`
- UPDATE DB_MAGIC：`/data/mvp40/mvp-logs-dm8rt01-r3/update-magic.log`
- 人工启动/验证日志：`/data/mvp40/mvp-r1-actual-verify.log`、`/data/mvp40/mvp-r1-dmserver.log`

最终人工验证为 `OPEN/NORMAL`，六表 COUNT 为 48、160、112、600000、1800000、1599680，总计 4,000,000。该结果证明物理恢复技术链路可行；没有同期 E1 源基线，不能判定严格一致性 PASS。

### R5：执行器阶段结果（自动阶段 + 人工补验证）

R5 目标为 `/dev/vdf`、`/data/mvp60/DM8RT01_R5`。Python 执行器自动完成并记录：CHECK、dminit、官方 `DUMP ... DATABASE '<target dm.ini>' MAPPED FILE`、RESTORE、RECOVER、UPDATE DB_MAGIC、dmserver 启动。

证据目录：`/data/mvp60/mvp-logs-dm8rt01-r5/`。

R5 自动验证阶段曾因两个代码问题未完成：

1. 启动前未强制校验恢复后 `dm.ini` 的端口；RESTORE 后端口曾回到 5236，导致端口冲突。
2. 使用了错误的 OS 认证连接串 `CONNECT / AS SYSDBA`，而当前实例需要 `CONNECT /@LOCALHOST:<port> AS SYSDBA`。

之后使用人工命令修正配置并完成 OPEN/NORMAL 和六表 COUNT，日志为 `/data/mvp60/mvp-r1-r5-verify-final.log`。因此 R5 不是“统一入口无人工干预完整 PASS”。

### 当前完整自动执行记录

`NOT EXECUTED`：当前没有一次统一入口、无人工干预、自动生成最终报告且通过启动验证与 COUNT 解析的完整 R1 记录。已有 R5 是自动恢复阶段成功、验证阶段失败后人工补做。

## B. 当前自动化完成度

| 阶段 | 状态 | 说明 |
|---|---|---|
| QGA guest-exec/guest-exec-status | 已实现并测试 | 返回非零退出码、超时、缺失退出码分别处理 |
| CHECK | 已实现并由 R5 验证 | DMRMAN `USE_AP=2`，LEVEL 1 |
| dminit | 已实现并由 R5 验证 | 运行时生成复杂度合规口令 |
| 映射文件 | 已实现并由 R5 验证 | 使用官方 `DATABASE '<target dm.ini>'` 生成方式 |
| RESTORE/RECOVER/DB_MAGIC | 已实现并由 R5 验证 | 阶段失败停止，未自动重试未知状态 |
| 启动前隔离检查 | 已修复，离线验证 | 检查 PORT、INSTANCE_NAME、MAL/ARCH 和监听冲突 |
| OPEN/NORMAL 与 COUNT | 代码已修复，尚缺统一入口复测 | R3/R5 有人工验证证据 |
| dm8_verify 集成 | 部分完成 | 当前执行器解析六表观测值；尚未将 RESTORED 基线交给 `dm8_verify` comparator 做源/恢复比较 |
| 结构化报告 | 已实现代码，尚无 R1 PASS 报告 | 报告区分 `ROW_COUNT_OBSERVED` 与 `comparison=NOT EXECUTED` |

## C. 代码修复

修改文件：

- `src/dm8_mvp/runner.py`：严格检查客体最终退出码；通信错误、超时和缺失退出码均归为未知状态。
- `src/dm8_mvp/restore.py`：新增路径/端口/目标隔离校验；启动前和 RESTORE 后再次核查 `dm.ini`；检查端口冲突；修正 OS 认证连接串；校验 SQL 错误和六表 COUNT；生成结构化报告；保留阶段失败停止。
- `src/dm8_mvp/__main__.py`：提供 `python -m dm8_mvp restore --config ...` 入口。
- `config/mvp-r1.example.json`：R1 专用配置示例。
- `tests/test_mvp_restore.py`：路径保护、dry-run、QGA 非零/超时/缺失退出码、COUNT 解析测试。

离线测试结果：`40 tests`，全部通过。dry-run 通过 `bash -n`，未执行客体写操作。

## D. 资源台账

| 文件 | 客体设备/挂载 | 虚拟容量 | 宿主机实际占用 | 归属 |
|---|---|---:|---:|---|
| `dm8-restore-c-mvp.qcow2` | 早期错误 raw 设备尝试 | 192 KiB | 196 KiB | 失败尝试 |
| `dm8-restore-c-mvp2.qcow2` | vdd `/data/mvp` | 20 GiB | 16.6 MiB（镜像层） | 早期失败目标及目录 |
| `dm8-restore-c-mvp40.qcow2` | vde `/data/mvp40` | 40 GiB | 355 MiB（镜像层） | R3 及失败尝试 |
| `dm8-restore-c-mvp60.qcow2` | vdf `/data/mvp60` | 60 GiB | 683 MiB（镜像层） | R4/R5，R5 证据 |

客体当前无 `dmserver` 进程，无 15242 监听；SSH 22 仍在。宿主 `/srv/dm8-lab` 可用约 223 GiB。E4 位于 vdb `/data/restore-c...`，E7 位于 vdc `/data/e7`，均未修改。

## E. 未完成事项与建议

不建议继续重复物理恢复。R3 已证明技术链路，R5 已证明自动恢复阶段。若需要严格验收，只需在后续一次经确认的统一入口复测中验证：启动前配置检查、专用端口连接、OPEN/NORMAL、COUNT 解析、报告生成及 `dm8_verify` RESTORED 基线集成。该复测应作为最终 E2E/R1 自动化验收的一部分，而非继续创建新磁盘调试。

下一步可转入 B1 离线代码开发：PRIMARY/归档预检、联机全量备份命令构造、CHECK 日志采集、源基线接口和失败停止。真实集群启动与 BACKUP 仍需单独授权。
