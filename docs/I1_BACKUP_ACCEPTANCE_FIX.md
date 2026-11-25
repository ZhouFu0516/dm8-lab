# I1 BACKUP 判定与阶段记录：最小定点修复

日期：2026-11-25。源码基线：`14ff360b44913c70944b8a1205c107999053d90d`。
对应首次真实运行：`i1-20261125T091526.063950Z-9ea1668f`。

## 原因与修改

- `src/dm8_mvp/guest_runtime.py::primary()`：移除 BACKUP 必须包含
  `executed successfully` 的文案条件。保留真实 DIsql 退出码、明确 SQL 错误检查；
  在 CHECK 前确认实际有普通的 .bak/.meta 文件。只有已有 DMRMAN CHECK 退出码0、
  无明确错误且有针对 CHECK 的成功输出，才将备份有效性阶段升级为 PASS。
- SQL 正常完成后，`backup.status=PASS` 仅表示 SQL 结果；
  `stages.backup=INCOMPLETE` 表示尚无备份有效性结论，CHECK 初始为 NOT EXECUTED。
  明确分开已知未启动 CHECK、CHECK 已执行失败、CHECK 超时未知这三种情况。
- `primary()` 在 SQL 提交后的预期异常中保留已取得的 backup/check 结果和分阶段状态。
  文件集不齐或无法启动 CHECK 时，不丢失已经执行的 BACKUP 证据。
- `src/dm8_mvp/integration.py::main()`：保留 PRIMARY QGA PID、exited、客机退出码及原始输出；
  先接收实际 backup/check 阶段，再判断整体结果，不再同时补写两个 PASS。
  丢失 PRIMARY 结果时，两阶段记录 BLOCKED/未知，不假定没有执行。
  CHECK 失败、未知或缺少成功阶段证据时停止，不进入传输/恢复。
- `tests/test_mvp_backup_acceptance.py`：5项定向回归，使用本次真实 DIsql 输出。
  原正常流程 Mock 增加新的阶段字段，保留原断言；没有放宽成功条件。

## 实际验证

```bash
PYTHONPATH=src python3 -B -m unittest tests.test_mvp_backup_acceptance -v
# Ran 5 tests; OK

PYTHONPATH=src python3 -B -m unittest discover -s tests -v
# Ran 75 tests; OK
```

覆盖：真实时间输出可进入 Mock CHECK并继续正常流程；SQL错误/非零退出码拒绝CHECK和传输；
CHECK非零、明确错误、无针对性成功输出及超时均停止；
备份已执行但文件集不齐或CHECK无法启动时，BACKUP=INCOMPLETE、CHECK=NOT EXECUTED，
报告保留 SQL 输出和退出码；QGA未知不伪称未执行。

完整实际输出保存在：
`/srv/dm8-lab/claude-review-i1-20261125/outputs/i1-backup-acceptance-fix-VnhjwM/unittest-full.txt` 与 `unittest-regression.txt`。
日志中负向用例输出 FAIL/INCOMPLETE 是预期的离线结果。

另已执行 I1 dry-run、生成的 R1 脚本 bash -n 和 git diff --check，均通过。
仅离线测试会在 Mock 边界内调用 CLI函数的 --execute；没有第二次真实 I1运行。

## 现有备份与历史证据

只通过一次只读 QGA 核查现有备份目录、文件类型/大小、dmdba可读性与挂载归属，
未执行 DMRMAN CHECK。目录存在、非符号链接；2个.bak和1个.meta为普通文件，
大小分别为334180352、46080、128512字节，与首次记录一致。
dmdba对三文件的 test -r 均为0；DMRMAN可执行检查为0；
目录仍在预期/dev/vdb、ext4、UUID 28b77482-9a5b-4771-9b75-779e2c002e17。

原始目录核查及最终客机退出码0见：
`/srv/dm8-lab/claude-review-i1-20261125/outputs/i1-backup-acceptance-fix-VnhjwM/dm8-primary-readonly.json`、`backup-presence.json`。

以下历史文件未修改：
- 首次I1 JSON SHA-256：
  `8e5de9b10a254a447fe211468eda89266f8e4ca281fae9b48e10d1babac2a24e`。
- 首次backup.log副本SHA-256：
  `a50581486744f8d0a63942ac40d35367051a35339bc6c0756d2db0b12a292f2a`。

旧报告保持FAIL，不回填CHECK或改写NOT EXECUTED。
路径门禁、QGA runner/协议、自包含交付、传输实现、R1与ROW_COUNT比较器不变。

## 下一步

独立CHECK操作方案见 `I1_EXISTING_BACKUP_CHECK_PLAN.md`。

准备结论：**READY FOR CONTROLLED CHECK REVIEW**。
这仅表示修复与CHECK方案就绪；本次已有备份集有效性仍为INCOMPLETE，
独立CHECK仍为NOT EXECUTED，等待单独授权。
