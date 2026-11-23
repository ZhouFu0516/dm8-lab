# P1 集群受控启动与环境恢复报告

采集时间：2026-11-23（Asia/Shanghai）  
范围：仅启动实验客体并进行只读环境检查；未启动 `dm8-monitor`、`dm8-template`，未执行切换、接管或恢复。

## 宿主机与虚拟机

- 宿主机 uptime：约 1 小时 34 分；内存可用约 177 GiB。
- `/srv/dm8-lab`：ext4，295G 总量、237G 可用（20% 使用）。
- `dm8-isolated`：active。
- `dm8-primary`、`dm8-standby1`、`dm8-standby2`：running。
- `dm8-client`、`dm8-monitor`、`dm8-template`：shut off。
- QEMU Guest Agent 可用；三节点地址分别为 `192.168.241.11`、`.12`、`.13`。

## 客体与 DM8

三台客体均为麒麟 Linux V10。主库已确认以下服务 active：

- `DmServiceDM8RT01.service`
- `DmWatcherServiceDM8RT01.service`
- `mdmonitor.service`

主库监听 DM8 `5236`、watcher `5436`；数据目录为 `/data/dm8/DM8LAB`，安装目录为 `/opt/dmdbms`。`dm.ini` 中实例标识为 `DM8RT01`，归档目录存在归档日志文件，归档配置与 watcher 配置文件存在。

本次未输出配置中的任何密码、密钥或敏感日志内容。

## 在线连接结果

客体端 `disql`、`dexp`、`dimp`、`dmrman` 工具存在；工具帮助显示 DM8 工具版本信息。尝试使用未经授权的猜测凭据连接被拒绝（无效用户名/密码），随后停止凭据尝试。未获得合规只读凭据，因此未执行 SQL，也未确认数据库版本、实例 SQL 状态、`HR_TEST` 模式、表清单或 `COUNT(*)`。

## 遗留问题

1. 需要通过安全渠道注入只读数据库凭据（不写入仓库、不放入命令行）。
2. 获得凭据后，使用 P2 `collect-db --level ROW_COUNT` 采集真实 `COUNT(*)`，再进行 `DATABASE → DATABASE` 重复采集稳定性检查。
3. 正式物理/逻辑恢复必须使用独立隔离目标；当前未对主库或备库执行恢复。
