# 本次现有备份集：独立 CHECK 方案（未执行）

状态：**READY FOR CONTROLLED CHECK REVIEW**。
真实 CHECK：**NOT EXECUTED**。本文件不是执行记录。

## 精确对象与命令

VM：`dm8-primary`。运行用户：`dmdba`。
备份目录：

```text
/data/dm8/DM8LAB/bak/i1/i1-20261125T091526.063950Z-9ea1668f
```

目录和3个备份文件已只读确认存在、非符号链接且dmdba可读。
2个.bak及1个.meta的文件大小与首次记录一致；文件存在不能代替CHECK。

**取得单独授权后，在PRIMARY客机执行一次的命令：**

```bash
runuser -u dmdba -- /opt/dmdbms/bin/dmrman USE_AP=2 \
  "CTLSTMT=CHECK BACKUPSET '/data/dm8/DM8LAB/bak/i1/i1-20261125T091526.063950Z-9ea1668f' LEVEL 1"
```

继续沿用本环境已验证的USE_AP=2方式。不要在宿主机运行本命令；
不连接数据库执行 SQL，不调用primary()，不调用I1 --execute。
不需要读取数据库密码，不指定RESTORE、RECOVER、UPDATE或OVERWRITE。

## 调用与日志保存

- 授权后在宿主机新建独立 `outputs/check-only-<唯一标识>/`，不覆盖历史日志。
- 通过现有QGA调用机制提交上述唯一CHECK命令一次，保存实际请求和响应。
  每次virsh RPC明确设置timeout；后续只轮询同一guest-exec PID，
  取得exited和最终exitcode，并正确解码Base64 stdout/stderr。
- 分别保存 `qga-events.json`、`check.stdout.log`、`check.stderr.log`、
  `check-result.json`；命令输出只保存在宿主机新目录，不写入备份集目录。
- 总等待上限900秒，单次QGA RPC上限15秒。超时或通信未知标记BLOCKED，
  保留已知PID与证据，不重新提交CHECK、不启动后续传输/恢复。
- 不调整磁盘、权限、数据库配置或服务，不修改/清理当前备份集及E4/E7/R4/R5。

## 判定与证据

PASS须同时有：guest命令实际结束且DMRMAN退出码为0、输出无明确DMRMAN错误、
出现针对CHECK的成功结果（现有解析预期 `check backupset successfully`）。
若真实输出格式不同，保留原文并停止核对，不能仅匹配任意successfully或仅凭退出码0判PASS。

退出码非零、明确错误或缺少成功证据为FAIL；没有取得最终执行结果为BLOCKED。
不得直接推断该备份集可恢复或整个I1成功。

独立报告字段至少包含：workflow=CHECK_ONLY、唯一check_run_id、
checked_backup_run_id=i1-20261125T091526.063950Z-9ea1668f、backupset绝对路径、
实际命令、QGA PID、exited、exitcode、stdout/stderr证据路径、状态及失败原因。

CHECK通过仅为本次现有备份集补充校验证据。
首次I1原始FAIL报告不变，CHECK仍不是首次I1自动执行的一部分，
不得拼接为首次自动E2E PASS。任何后续传输、恢复或新一轮BACKUP/I1需新的明确任务。
