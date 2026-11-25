# P2 环境交接与运维手册对齐记录

状态：只读审计记录，未进入自动化开发。  
依据：`03 环境交接`（v1.0）、`04 运维手册`（v1.2）、本轮宿主机/libvirt 审计、P2 代码及 E1-E7 实验记录。

## 1. 当前状态优先级

文档中的历史验收、P1 启动记录和外包记录不能替代实时检查。本轮实际检查显示：

- 宿主机为 Ubuntu 24.04.5、80 vCPU、187 GiB 内存；`/srv/dm8-lab` 为 `/dev/vdb1`，可用约 224 GiB。
- `dm8-isolated` 当前 active/autostart，网桥为 `br-dm8`。
- `dm8-primary`、`dm8-standby1`、`dm8-standby2`、`dm8-monitor`、`dm8-client`、`dm8-template`、`dm8-restore-c` 当前均为 `shut off`。
- 因 VM 均关机，本轮未确认数据库角色、OPEN 状态、归档状态、Monitor/Watcher 状态、客体工具路径和 QGA 实际连通性；不得从 VM 名称推断角色，也不得自行启动。
- 当前 Git 仓库为 `/srv/dm8-lab/dev`，HEAD 为 `0f4903f`（`e1-e7-archive-20261124`），工作区有未跟踪 `evidence/`，不得清理或提交。

## 2. 必须保持的运维边界

- ECS、系统盘和数据盘不得释放、重启或改配；EIP/安全组只在明确授权后处理。
- libvirt 网络自启不等于 VM 自启；VM autostart 全部 disabled。启动顺序、服务等待和冷启动验收必须人工确认。
- 达梦 Monitor 是 `dm8-monitor` 上的 `DmMonitorServiceDM8LAB`；Linux `mdmonitor.service` 不是达梦 Monitor，不能用其退出码证明集群已停止。
- 旧版 `gscript` 存在超时静默、虚拟机不存在不报错、不传递客体退出码等缺陷；`gscript2` 只在模拟 virsh 环境自测，真实客机验证前不得替换旧版。
- QGA 脚本必须保留真实换行，不能把 `\\n` 字面量送入 disql。连接失败应区分脚本构造错误、SQL 错误和认证错误，禁止猜测或重置密码。
- 凭据只从客机内权限为 0600 的文件受控读取，不回显、不进入 argv、日志、Git 或报告。

## 3. P2 验收范围对齐

- P2/P2.1：离线基线、manifest、SHA-256、比较器和退出码；31 项离线测试本轮已实际通过。
- P2.2：真实 DM8 只读联调和两次独立 `DATABASE -> DATABASE` `ROW_COUNT` PASS；范围仅为配置的 HR_TEST 六张表，不是全库，也不是恢复后验收。
- README 中“当前没有可用 DM8 客体连接、在线采集未联调”的描述早于 P2.2，不能作为当前事实；后续应以实际代码和实验记录为准。
- `dm8_verify` 应复用其只读采集、比较、manifest 和报告能力；当前没有完整的物理备份/恢复或 dexp/dimp 编排模块。

## 4. E1-E7 证据状态

| 实验 | 当前可引用结论 |
|---|---|
| E1/E2 | 历史记录证明主库/备库全量物理备份完成并通过 CHECK；精确耗时和 LSN 不完整 |
| E3 | 仅完成基础大小/粗略耗时对比，不作严格性能结论 |
| E4 | 独立恢复库 OPEN/NORMAL，六表 ROW_COUNT 全匹配；原始 RESTORE 退出码缺失 |
| E5 | 跳过，未形成正式实验定义或证据 |
| E6 | 方案 A'（standby1 + SYSDBA 远程 PRIMARY）成功；有效 dmp 已保存并有 SHA-256；LABCLIENT 4165 字节文件无效 |
| E7 | 未完成；dimp 期间 QGA 失联，未取得可靠退出码和 COUNT Diff，不得标 PASS |

E1-E7 原始记录位于 `experiments/dm8_backup_recovery/records/`；有效 E6 文件位于 `/srv/dm8-lab/artifacts/e6/e6_hr.dmp`。不得把历史外包验收或不合格 dmp 当作本轮实时验证。

## 5. 独立恢复资产隔离

`dm8-restore-c` 同时存在 E4 恢复目录、E7 专用数据盘和独立输入文件。后续任何恢复/导入前必须核对 VM XML、磁盘映射、挂载点、端口、数据库目录和目标实例，确保不覆盖 E4、生产主库或备库；状态不明时停止，不重试、不重启、不 kill。

## 6. 后续文档维护规则

后续新增事实必须注明 `VERIFIED`、`HISTORICAL` 或 `NOT VERIFIED`，并附命令、原始输出或文件路径。官方达梦文档优先于博客、口头交接和推测命令；任何正式开发前先补齐当前 VM/数据库状态和 E7 证据缺口。
