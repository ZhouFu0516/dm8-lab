# I1 四项最小补丁：离线复核交接

日期：2026-11-25。共同基线：`968d468c73592a3cfae87f00b6930ead03473e5a`。

本轮只实施已对齐的四项补丁及必要行为验证。工作目录为
`/srv/dm8-lab/claude-review-i1-20261125`，分支为 `review/i1-20261125`。
原工作区 `/srv/dm8-lab/dev` 保留原状；后续实机操作不能假设该目录已更新。

## 四项结果

| 项目 | 本轮修改与依据 | 离线结论 |
| --- | --- | --- |
| 返回协议 | `guest_runtime.sql_result()` 返回统一 status、真实 exitcode、stdout/stderr。非零退出码或明确 SQL 错误为 FAIL；DIsql 超时为 BLOCKED、unknown=True。`invoke_guest()` 先解析结构化结果，再处理客机退出码；未知结果走 QgaUnknown，不自动重试。失败报告保留已取得的 guest_result。预检和 COUNT marker 解析仍执行。 | PASS |
| 客机代码交付 | `build_guest_request()` 携带实际 `guest_runtime.py` 和 `disql_results.py` 源码，在客机 Python 进程内存中加载固定依赖，无须预装项目包。删除 guest_runtime 中未使用的 COUNT collector 导入。 | PASS |
| 传输超时 | `transfer_manifest()` 将 TimeoutExpired 转为 QgaUnknown；main 记录 transfer 阶段 BLOCKED、UNKNOWN/no retry 并生成 I1 报告，不调用后续哈希检查或 R1。原 64 KiB 分块和 900 秒默认值保留；吞吐与合适时限尚未实测。 | PASS |
| MAPPED FILE 引号 | Bash 使用变量保存单/双引号正则，正确剥去合法路径引号；保留 realpath 与目标目录边界检查。只执行从实际生成脚本提取出的 safe_mapping 函数，以本地临时文件验证，不运行恢复脚本。 | PASS |

## 测试方式与实际结果

修改前在共同基线实际复跑：全套 65 项，64 项通过、1 项失败；失败为既有 dry-run 标题不匹配。
本轮将产品输出改成 `R1 restore config (not executed)`，保留原断言。

修改后执行：

```bash
PYTHONPATH=src python3 -B -m unittest discover -s tests -v
# Ran 69 tests; OK. 31 core + 7 backup + 14 integration + 17 restore.

PYTHONPATH=src python3 -B -m unittest \
  tests.test_mvp_backup tests.test_mvp_restore tests.test_mvp_integration -v
# Ran 38 tests; OK. 7 backup + 17 restore + 14 integration.

PYTHONPATH=src python3 -B -m dm8_mvp run \
  --config config/mvp-b1.example.json --dry-run
# exitcode 0; 仅生成计划。

set -o pipefail
PYTHONPATH=src python3 -B -m dm8_mvp restore \
  --config config/mvp-r1.example.json --dry-run | bash -n
# exitcode 0; 未执行生成的恢复命令。
```

完整测试实际输出见同目录 `I1_MINIMAL_PATCH_TEST_OUTPUT.txt`。
本地完整 dry-run 和单独日志位于 `outputs/i1-four-fixes-gflj14/`，不提交 outputs。
生成的 PRIMARY 请求也实际通过 `bash -n`，外层 guest-exec JSON 参数约 26 KB；没有发送给 QGA。

关键行为测试：

- `test_sql_protocol_contract_in_isolated_python`：真实生成的载荷在 `python3 -I -S -B`、无 PYTHONPATH、仓库外临时 cwd 下执行。先确认项目包不可导入，再只 Mock 凭据读取及 DIsql subprocess；真实经过 guest_main 输出、invoke_guest 解析。覆盖成功、非零退出码、零退出码但含 SQL 错误、超时。
- 既有 `test_execute_normal_path_mock_reaches_restore_and_comparison` 已加强：不再 Mock invoke_guest、源基线采集和比较器。源库前后四次 SQL 请求运行真实自包含载荷；预检/COUNT marker 和两次 ROW_COUNT 比较均实际执行。备份、文件传输和 R1 执行由 Mock 代替。
- `test_source_after_sql_error_or_timeout_stops_with_report`：备份后 SQL 非零为 FAIL，超时为 BLOCKED；均停止传输及恢复并生成报告，不将未知状态改为 INCOMPLETE。
- `test_transfer_timeout_is_blocked_without_retry_or_restore`：实际 transfer_manifest 遇到 Mock TimeoutExpired，仅尝试一次，报告 BLOCKED，不调用目标哈希或 R1。
- `test_generated_bash_mapping_guard_matches_python_on_path_fixtures`：单引号、双引号和无引号合法路径通过；target 外、越界 `..`、相似目录前缀、越界 mirror_path、缺失引号的样例拒绝；这些样例的 Bash/Python 判定一致。

测试日志中的 FAIL/INCOMPLETE JSON 是预期失败用例的输出，不是真实数据库实验结果。

## 未改变的保护与范围

已比较 AST，guest_runtime.probe、restore.validate_config、_safe_path、validate_mapping_text 与基线一致。
QgaRunner、ROW_COUNT comparator、两份 MVP 配置和原有 I1/R1 run_id 报告关联不变。
本轮没有启动 VM、调用真实 QGA、读取真实凭据文件、连接数据库、执行 BACKUP/RESTORE 或修改实验资产。

## 仍保留的事项

- 先前记录的重复 build_primary_script、统一 CLI 未转发 preflight-only、manifest 内联解析、实例名/端口双来源仍是记录项，本轮未扩展修复。
- 恢复实例沿用的 INSTANCE_NAME 与源库可能相同；首次实际报告需用恢复 VM、端口、数据目录和唯一 db_name 标识恢复对象。
- 干净子进程通过证明载荷携带所需 Python 源码；客机实际 Python、runuser、DIsql/DMRMAN、凭据、权限、挂载空间、工具输出以及传输吞吐仍为 NOT EXECUTED。
- 只声明六表 ROW_COUNT 级比较；源库前后 COUNT 稳定不证明严格事务快照或全库内容一致。

本轮四项离线验收：PASS。真实 R1 全自动验收仍为 INCOMPLETE，真实 I1 E2E 为 NOT EXECUTED。
实机保持暂停，等待 Claude 对该补丁逐项复核及后续明确的真实实验授权。
