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

更正：此前连接测试脚本的 stdin 换行被双重转义，导致 disql 没有收到正确的多行命令，因此此前报告中的“认证拒绝”证据无效。修正后 VM5 客户端（`192.168.241.15`）通过 LABCLIENT/DM8LAB 成功执行只读 SQL；三节点 5236/TCP 可达，查询确认当前连接实例为 `DM8RT01`、状态 `OPEN`、模式 `PRIMARY`。VM5 凭据文件存在且权限 `0600`，本次未回显其内容。

HR_TEST 当前表清单由 `ALL_TABLES` 返回六表，逐表 `COUNT(*)` 与重复采集结果一致；详情见 P2 在线验证报告。本轮没有采集可用数据库版本字符串。

交接备份 `/srv/dm8-lab/backups/acceptance/dm8-acceptance.tar` 存在，SHA-256 与交接记录一致。

## 遗留问题

1. 需要通过安全渠道注入只读数据库凭据（不写入仓库、不放入命令行）。
2. 获得凭据后，使用 P2 `collect-db --level ROW_COUNT` 采集真实 `COUNT(*)`，再进行 `DATABASE → DATABASE` 重复采集稳定性检查。
3. 正式物理/逻辑恢复必须使用独立隔离目标；当前未对主库或备库执行恢复。
