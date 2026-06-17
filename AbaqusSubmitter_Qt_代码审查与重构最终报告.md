# AbaqusSubmitter Qt 主线代码审查、判定逻辑收敛与模块重构报告

> 审查对象：用户上传的 Qt 主线 Python 源码 21 个文件  
> 审查方式：静态只读审查、AST 函数盘点、依赖关系梳理、`compileall` 与 `tabnanny` 检查  
> 总行数：9987 行  
> 本轮源码修改：否  
> Git 写操作：否  
> 打包：否  
> 真实 Abaqus 作业提交：否

---

## 1. 执行结论

当前 Qt 版本不需要推倒重来。现有项目已经形成较清晰的基本层次：

```text
Qt UI
→ 提交与队列调度
→ Workspace 准备
→ QProcess launcher
→ STA/LCK/console 运行时监控
→ 诊断分类与终态映射
→ 归档
```

但内部普通作业生命周期仍存在几类风险：

1. `.sta` 结束判断尚未验证文件内容稳定性。
2. `pre.exe` 与正式 `standard.exe/explicit.exe` 阶段共用 `solver_seen`，语义过宽。
3. 内部 `solver_process_active` 由内存扫描按 `job_name` 顺带回写，存在同名跨目录污染，并且生命周期与内存采样耦合。
4. 最终诊断未严格限定为当前提交产生的新内容，残留 `.log/.msg/.dat` 可能污染结果。
5. 手动终止分支仍以“无 `.lck`”作为过强结束依据。
6. SSD 整体归档完成后的 reserved key 释放存在元组转字符串错误。
7. `calculation_work_dir` 同时表达 SSD 根目录和实际运行目录，后续可能重复拼接 Job 名。
8. 状态字符串在多个文件重复定义，且 `正在启动` / `启动中` 不一致。

推荐总体策略：

```text
Tk 旧版的文件主导逻辑
+
.log 精确阶段识别
+
STA 文件签名稳定判断
+
可信 PID 仅作为“禁止完成”的否决项
+
当前提交诊断基线
+
Qt 幂等结束和归档保护
```

---

## 2. 当前模块依赖图

```text
main.py
├─ command.py
├─ queue_scheduler.py
├─ queue_manager.py
├─ workspace_prepare_adapter.py → workspace_prepare.py
├─ job_runtime.py → abaqus_diagnostics.py
├─ job_finalizer.py → abaqus_diagnostics.py
├─ memory_adapter.py → memory_monitor.py + process_scanner.py
├─ external_jobs.py → process_scanner.py
├─ archive.py → workspace_prepare.py + archive_move.py + queue_scheduler.py
├─ archive_move_adapter.py → archive_move.py
├─ ui_components.py
└─ ui_styles.py
```

### 内部普通作业当前调用链

```text
MainWindow.start_job()
→ WorkspacePrepareService（可选）
→ MainWindow.continue_start_job_after_workspace_ready()
→ JobRuntimeController.start_process()
→ QProcess(cmd /c abaqus ...)
→ read_process_output()
→ on_process_finished()
→ poll_sta_file()
→ emit_job_finished_once()
→ MainWindow.finalize_completed_run()
→ inspect_finished_job()
→ resolve_finalization_status()
→ archive_or_defer_finished_job()
→ active_runs.pop()
→ request_dispatch_queue()
```

### 外部作业当前调用链

```text
QueueManagerDialog.request_external_scan()
→ MainWindow.scan_external_jobs()
→ ExternalJobScanWorker.run()
→ process_scanner.scan_running_abaqus_jobs_by_psutil()
→ external_jobs.merge_external_scan_results()
→ 更新正式队列 QueueItem
```

当前外部链路仍是一次性扫描，不是持续 STA/LOG 监控。

---

## 3. 风险优先级

| 优先级 | 问题 | 影响 | 首要动作 |
|---|---|---|---|
| P0 | 归档 reserved key 释放类型错误 | 同名 SSD 目录后续一直被阻塞 | 删除 `str()` 转换 |
| P0 | `.sta` 未做文件稳定性确认 | 极端窗口下可能提前完成 | 增加 STA 签名与最小静默时间 |
| P0 | `pre_started` 也会触发 `solver_seen` | 预处理失败后可能永久等待 STA | 拆分 activity 与 solver_started |
| P0 | 生命周期 PID 依赖内存扫描且只按 job_name | 同名跨目录污染；判断节奏不稳定 | 新增 runtime PID evidence |
| P1 | 诊断文件未做当前提交基线过滤 | 旧 log/msg/dat 可能污染终态 | 记录 baseline，仅读取新内容 |
| P1 | 手动终止只看 `.lck` 消失 | 清理未结束时可能提前补位 | 加 PID 与稳定轮询 |
| P1 | `calculation_work_dir` 一字段双语义 | 目录重复拼接、冲突键漂移 | 拆 root/effective 两字段 |
| P1 | 状态常量重复且不一致 | 调度、UI、终态集合分裂 | 统一 constants.py |
| P2 | 外部扫描只做一次 | 无法持续接管外部作业 | 新增共享 timer 外部 monitor |
| P2 | 大文件职责过多 | 后续维护成本高 | 分轮次拆 main/process_scanner/queue_manager |

---

## 4. 应删除、替换和新增的判定逻辑

### 4.1 应删除或停止使用的旧逻辑

| 位置 | 现有逻辑 | 问题 | 处理 |
|---|---|---|---|
| `job_runtime.py::mark_solver_seen_from_stage()` | 任一 `abaqus_stage_started()` 即置 `solver_seen=True` | `pre.exe` 只是预处理，不等于正式 solver 已启动 | 删除该语义；改为阶段分层 |
| `job_runtime.py::runtime_completion_ready()` | `seen_sta + solver_seen + !solver_process_active + stable_no_lck>=3` | 无锁稳定不等于文件稳定 | 用 finish candidate + 文件签名替换 |
| `job_runtime.py::poll_sta_file()` terminating 分支 | `terminating and not lck.exists()` 即结束 | solver 可能仍在清理退出 | 改为无 LCK + 无可信 PID + 稳定轮询 |
| `main.py::apply_memory_scan_result()` | 根据按 job_name 聚合的内存结果写 `run['solver_process_active']` | 生命周期与内存扫描耦合；同名跨目录污染 | 删除生命周期字段写入；内存扫描只更新内存 |
| `job_finalizer.py::resolve_finalization_status()` | 普通作业可直接接受诊断文件 `status == '完成'` | 旧诊断文件可能污染新运行 | 普通作业成功只消费 runtime 确认结果 |
| `main.py::continue_start_job_after_workspace_ready()` | `queue_item.calculation_work_dir = work_dir` | 覆盖 SSD 根目录语义 | 停止覆盖；新增 effective_work_dir |
| `queue_manager.py` 本地状态常量 | 再定义一套状态字符串 | 与 constants.py 不一致 | 删除本地重复定义，统一导入 |
| `process_scanner.py` | `from .constants import *` | 依赖边界不清晰 | 改成显式导入 |

### 4.2 应新增的可靠逻辑

#### A. 新增 `runtime_evidence.py`

```python
@dataclass(frozen=True)
class RuntimeEvidence:
    lck_exists: bool
    sta_exists: bool
    sta_valid: bool
    sta_signature: tuple
    log_signature: tuple
    pre_started: bool
    solver_started: bool
    solver_kind: str
    solver_process_active: bool
    solver_pids: tuple[int, ...]
    pid_confidence: str
    diagnostic_status: str
    diagnostic_detail: str
```

新增函数：

```python
capture_diagnostic_baseline(work_dir, job_name)
read_incremental_log(run)
build_file_signature(path, tail_bytes=4096)
collect_runtime_process_evidence(work_dir, job_name)
collect_runtime_evidence(run)
```

#### B. 新增单向运行阶段

```python
CREATED
STARTING
PREPROCESSING
SOLVING
FINISH_CANDIDATE
COMPLETED
FAILED
TERMINATED
UNKNOWN
```

新增函数：

```python
evaluate_runtime_phase(run, evidence)
evaluate_terminal_transition(run, evidence, now)
```

#### C. 新的普通作业完成门槛

```text
曾经进入 SOLVING
+
当前运行产生过有效 STA
+
无 LCK
+
无可信 solver PID
+
STA 文件签名连续稳定 3 轮
+
真实静默时间至少 10 秒
+
无当前运行明确失败诊断
→ runtime_completion_confirmed=True
```

PID 规则固定为：

```text
可信 PID 存在 → 一票否决完成
没有 PID      → 不能单独证明完成
```

#### D. 当前提交诊断基线

作业开始前记录：

```python
run['diagnostic_baseline'] = {
    '.sta': (mtime_ns, size),
    '.msg': (mtime_ns, size),
    '.dat': (mtime_ns, size),
    '.log': (mtime_ns, size),
}
```

终态分类时只读取：

```text
新创建的文件
或相对于 baseline 发生变化的新增内容
```

#### E. 手动终止门槛

```text
terminating=True
+
无 LCK
+
无可信 solver PID
+
文件稳定 2～3 轮
→ TERMINATED
```

超时仍无法确认时：

```text
→ UNKNOWN
→ 保留 SSD 目录
→ 不执行成功归档
```

---

## 5. 文件合并与拆分总览

| 当前文件 | 动作 | 原因 | 目标 |
|---|---|---|---|
| `main.py` | 继续拆分 | 3169 行；职责过多 | UI 壳 + 多个 controller |
| `process_scanner.py` | 继续拆分 | 快照、匹配、外部扫描、内存聚合混合 | 4 个模块 |
| `queue_manager.py` | 继续拆分 | Dialog、候选服务、文件夹线程、持久化混合 | 4 个模块 |
| `archive.py` | 继续拆分 | 旧结果、workspace plan、归档协调混合 | 3 个模块 |
| `abaqus_diagnostics.py` | 后续拆分 | I/O、分类、STA 格式化混合 | 3 个模块，可延后 |
| `workspace_prepare_adapter.py` + `archive_move_adapter.py` | 抽取公共基类 | 串行 QThread 队列模板高度重复 | `qt_task_queue.py` |
| `models.py` | 小幅整理 | `MemoryStatusEx` 不属于队列模型 | 移到 `system_memory.py` |
| `job_runtime.py` | 保留并瘦身 | runtime 主责合理，但证据读取过重 | 新增 `runtime_evidence.py` |
| `job_finalizer.py` | 保留 | 适合纯函数测试 | 仅消费 runtime 终态 |
| `workspace_prepare.py` | 保留并吸收 plan 构造 | 纯 I/O 边界合理 | 接收 archive.py 中 workspace 函数 |
| `archive_move.py` | 保留 | 纯 I/O 边界合理 | 无需合并 |
| `command.py` | 保留 | 纯逻辑边界合理 | 无需合并 |
| `queue_scheduler.py` | 保留 | 纯逻辑边界合理 | 统一状态与目录字段 |
| `memory_monitor.py` | 保留 | UI 无关服务边界合理 | 无需合并 |
| `memory_adapter.py` | 保留并瘦身 | Qt 适配合理 | 移除生命周期回写 |
| `qt_compat.py` | 保留 | 小但职责清晰 | 无需合并 |
| `ui_components.py` | 保留 | 展示辅助边界合理 | 可吸收更多纯展示函数 |
| `ui_styles.py` | 保留 | 样式独立合理 | 无需合并 |
| `external_jobs.py` | 保留并扩展 | 外部适配边界合理 | 配合 external monitor controller |

---

## 6. 需要迁移的具体函数

### 6.1 从 `main.py` 迁出

#### 迁入 `queue_dispatch_controller.py`

```text
start_queue
stop_queue
estimate_effective_available_slots
request_dispatch_queue
_run_scheduled_dispatch_queue
dispatch_queue
_dispatch_queue_now
refresh_queue_dependencies
terminate_queue_items_by_ids
```

职责：队列补位、停止、依赖刷新、并行槽位计算。UI 通过信号或回调接收状态变化。

#### 迁入 `submission_controller.py`

```text
ask_existing_odb_action         # UI 提示可保留在 MainWindow，纯结果处理迁出
handle_existing_job_results
archive_move_conflict_message
submit_requires_restart_dependency
block_missing_restart_dependency
validate_restart_dependency_before_start
start_job
enqueue_workspace_prepare
on_workspace_prepare_succeeded
on_workspace_prepare_failed
workspace_prepare_task_is_current
log_workspace_prepare_result
continue_start_job_after_workspace_ready
resolve_oldjob_source_dir
```

建议拆法：

```text
submission_controller.py 负责流程
existing_results.py       负责文件处理
MainWindow                只保留对话框交互
```

#### 迁入 `archive_controller.py`

```text
run_is_ssd_independent_archive_candidate
run_allows_archive_move
archive_move_reserved_key_for_run
mark_archive_move_result
enqueue_archive_move
release_archive_move_context
archive_move_context_run
on_archive_move_succeeded
on_archive_move_blocked
on_archive_move_failed
archive_or_defer_finished_job
process_deferred_archives
archive_finished_job
handle_archive_result
mark_archive_result
```

其中 `archive_finished_job()` 和 `mark_archive_result()` 属于薄包装，迁移后可删除，直接调用 coordinator。

#### 迁入 `external_monitor_controller.py`

```text
collect_known_external_jobs
scan_external_jobs
clear_external_scan_worker
queue_dialog_is_visible
fail_external_scan
build_queue_item_index
append_external_scan_debug_log
finish_external_scan
```

新增：

```text
start_shared_external_monitor
stop_shared_external_monitor
poll_external_jobs
refresh_external_runtime_view
```

#### 迁入 `runtime_view_controller.py`

```text
open_work_dir
select_runtime_log_file
open_job_file
update_sta_sticky_header_visibility
calculate_runtime_panel_min_width
apply_runtime_panel_width_baseline
show_runtime_panel
refresh_job_selector
on_job_selector_changed
refresh_job_stats
refresh_selected_run_status
refresh_selected_run_meta
selected_job_key
select_run
append_job_log
refresh_pause_button_style
update_process_buttons
```

#### 保留在 `MainWindow`

```text
__init__
build_ui
apply_styles
collect_options
current_queue_settings
select_inp_file
select_oldjob_file
select_for_file
on_input_changed
update_command_preview
preview_command
submit_job
open_queue_manager
position_queue_manager
show_non_modal_message
append_history
closeEvent
```

#### 可删除的薄包装

```text
read_process_output
on_process_finished
poll_sta_file
suspend_job
resume_job
run_abaqus_control
archive_finished_job
mark_archive_result
```

前提：信号直接连接目标 service/controller，并保留必要的 UI 回调。

### 6.2 从 `process_scanner.py` 迁出

#### 新建 `process_snapshot.py`

```text
join_process_command_line
normalize_process_name
is_abaqus_process_name
log_performance
get_psutil_process_snapshot
fetch_psutil_process_rows_for_external_scan
fetch_psutil_process_rows
fetch_windows_process_rows
```

#### 新建 `abaqus_process_matcher.py`

```text
normalize_joblist_path
parse_job_name_from_command_line
find_process_abaqus_job_name
normalize_work_dir
is_active_solver_process
is_related_abaqus_process
get_command_parameter_patterns
extract_abaqus_command_parameter
get_process_and_parent_chain
is_possible_abaqus_process
get_first_chain_parameter
get_chain_work_dir
resolve_external_job_path
detect_external_job_type
```

新增：

```text
match_runtime_solver_processes(work_dir, job_name)
```

它必须按：

```text
work_dir + 精确 job 参数 + cwd/indir/outdir + 进程类型
```

匹配内部作业 PID。

#### 新建 `external_job_scanner.py`

```text
get_job_lock_info
_diagnostics_status_is_completed
_diagnostics_status_is_terminated
_diagnostics_status_is_failed
classify_external_job_runtime
scan_running_abaqus_jobs_by_psutil
```

#### 新建 `memory_process_usage.py`

```text
get_abaqus_job_memory_usage
get_cached_abaqus_job_memory_usage
```

内存估算可继续按 `job_name` 聚合，但不得再直接驱动内部生命周期终态。

### 6.3 从 `queue_manager.py` 迁出

#### 新建 `queue_persistence.py`

```text
atomic_write_json
QueueManagerDialog.load_saved_paths
QueueManagerDialog.save_saved_paths
```

#### 新建 `folder_scan_adapter.py`

```text
FolderScanWorker
QueueManagerDialog.scan_folder
QueueManagerDialog.start_folder_scan
QueueManagerDialog.set_folder_scan_busy
QueueManagerDialog.handle_folder_scan_finished
QueueManagerDialog.handle_folder_scan_failed
QueueManagerDialog.handle_folder_scan_done
```

#### 新建 `queue_candidate_service.py`

```text
add_candidate
new_candidate_add_stats
add_skip_detail
candidate_skip_reason
add_candidate_batch
has_result_files
detect_restart
validate_candidate
candidate_conflict_item
revalidate_candidates
format_count_distribution
expected_oldjob_path_for_item
restart_dependency_source_items
restart_dependency_resolved
build_restart_dependency_options
ensure_restart_dependencies_for_candidates
build_confirm_candidates_summary
```

其中 `prompt_restart_dependency()` 仍留在 Dialog，因为它是 UI 对话框。

#### 新建 `queue_table_presenter.py`

```text
apply_table_item_alignment
item_row_key
table_row_key
table_row_key_map
selected_table_row_keys
current_table_row_key
resize_table_to_count
update_table_cell
restore_table_view_state
sync_table_rows
candidate_row_values
formal_row_values
format_runtime_memory
```

### 6.4 从 `archive.py` 迁出

#### 新建 `existing_results.py`

```text
get_existing_odb_file
get_existing_lck_file
_unique_backup_path
_unique_sibling_path
backup_existing_result_files
delete_existing_result_files
```

#### 合并到 `workspace_prepare.py`

```text
prepare_calculation_workspace
build_workspace_info
apply_workspace_prepare_result
build_workspace_prepare_plan
```

#### 新建 `archive_coordinator.py`

```text
archive_finished_job_files
ArchiveCoordinator
```

`archive.py` 最终可以删除，或保留为向后兼容导出层。

### 6.5 `abaqus_diagnostics.py` 后续拆分

#### 新建 `diagnostic_io.py`

```text
clear_diagnostic_file_cache
decode_abaqus_text
read_file_tail
read_file_tail_cached
read_file_head
format_backup_time_tag
parse_datetime_from_abaqus_text
get_existing_job_backup_time_tag
```

#### 新建 `diagnostic_classifier.py`

```text
extract_key_diagnostic_line
classify_job_text
update_abaqus_stage_from_text
abaqus_stage_started
inspect_job_files
inspect_job_files_throttled
```

#### 新建 `sta_formatter.py`

```text
parse_sta_progress
is_sta_progress_line
append_sta_separator_once
build_sta_table_header
get_display_width
format_abaqus_standard_title
format_sta_output_for_log
```

这一拆分可以放在生命周期稳定之后执行。

### 6.6 抽取公共 Qt 串行任务队列

`workspace_prepare_adapter.py` 与 `archive_move_adapter.py` 的服务骨架重复。新增：

```text
qt_task_queue.py
└─ SerialQtTaskQueue
```

抽取公共逻辑：

```text
_pending_tasks
_active_task
_active_thread
_active_worker
_closing
enqueue
is_busy
shutdown
_start_next_if_idle
_on_thread_finished
```

业务 adapter 仅保留：

```text
Task dataclass
Worker.run()
业务特有 signals
结果适配
```

---

## 7. 推荐目标目录结构

```text
abaqus_submitter_qt/
├─ main.py
├─ constants.py
├─ status_codes.py                 # 可选：状态继续增长时再拆
├─ models.py
├─ command.py
├─ qt_compat.py
├─ qt_task_queue.py
│
├─ runtime/
│  ├─ job_runtime.py
│  ├─ runtime_evidence.py
│  └─ job_finalizer.py
│
├─ diagnostics/
│  ├─ diagnostic_io.py
│  ├─ diagnostic_classifier.py
│  └─ sta_formatter.py
│
├─ process/
│  ├─ process_snapshot.py
│  ├─ abaqus_process_matcher.py
│  ├─ external_job_scanner.py
│  └─ memory_process_usage.py
│
├─ queue/
│  ├─ queue_manager.py
│  ├─ queue_scheduler.py
│  ├─ queue_candidate_service.py
│  ├─ queue_table_presenter.py
│  ├─ queue_persistence.py
│  └─ folder_scan_adapter.py
│
├─ archive/
│  ├─ existing_results.py
│  ├─ workspace_prepare.py
│  ├─ workspace_prepare_adapter.py
│  ├─ archive_move.py
│  ├─ archive_move_adapter.py
│  └─ archive_coordinator.py
│
├─ external/
│  ├─ external_jobs.py
│  └─ external_monitor_controller.py
│
├─ memory/
│  ├─ memory_monitor.py
│  ├─ memory_adapter.py
│  └─ system_memory.py
│
└─ ui/
   ├─ ui_components.py
   ├─ ui_styles.py
   ├─ runtime_view_controller.py
   └─ queue_dispatch_controller.py
```

不要一次完成整个目录迁移。先按下节分轮次执行。

---

## 8. 分轮次实施计划

### 第 1 轮：修明确 bug，不改变模块结构

1. 修复 `release_archive_move_context()` 中 reserved key 元组转字符串错误。
2. 完成判断增加 STA 文件签名稳定轮询和至少 10 秒静默时间。
3. 终止判断改成无 LCK + 无可信 PID + 稳定轮询。
4. `inspect_finished_job()` 使用当前提交 baseline，避免旧诊断污染。
5. 停止覆盖 `queue_item.calculation_work_dir`，新增 `effective_work_dir`。
6. 统一状态常量，删除 `queue_manager.py` 局部重复状态定义。

### 第 2 轮：收敛内部生命周期

1. 新增 `runtime_evidence.py`。
2. 增量读取 `<job>.log`。
3. 区分 `activity_seen`、`pre_started`、`solver_started`。
4. 新增 `PREPROCESSING` 和 `FINISH_CANDIDATE` 状态。
5. 内部 PID 判断从内存扫描解耦，改用 `(work_dir, job_name)` 的可信匹配。
6. 普通作业成功只允许 runtime 明确确认；finalizer 不再猜测成功。

### 第 3 轮：完善外部作业接管

1. 扫描用户选择根目录本身。
2. 可选递归扫描下级目录；规范化路径去重。
3. 新增一个共享 QTimer 持续刷新所有外部活动作业。
4. 复用 diagnostics、runtime evidence 和 STA 格式化。
5. PID 存在显示运行中；PID 不存在但 LCK 存在显示残留锁文件或疑似异常中断；均不存在时结合当前诊断判断完成、失败或未知。
6. 外部 Job 加入右侧 selector。

### 第 4 轮：缩减 main.py

先迁出：

```text
archive_controller.py
external_monitor_controller.py
queue_dispatch_controller.py
runtime_view_controller.py
```

这四组边界最清晰，风险较低。

### 第 5 轮：拆 process_scanner.py 和 queue_manager.py

1. 拆进程快照、匹配、外部扫描、内存统计。
2. 拆候选服务、表格 presenter、文件夹扫描和配置持久化。
3. 抽取公共 `qt_task_queue.py`。

### 第 6 轮：清理与测试

1. 删除薄包装和未使用常量。
2. 所有状态使用统一常量。
3. 禁止 wildcard import。
4. 补齐 stub、集成测试和服务器短模型测试。

---

## 9. 必须补充的测试矩阵

### 内部作业

| 场景 | 预期 |
|---|---|
| launcher 运行，无任何文件 | STARTING，不完成 |
| launcher 退出，无任何 solver 信号 | 宽限期内等待；超时 UNKNOWN/FAILED_START |
| 仅 `Run pre.exe`，无 standard/explicit、无 STA | 预处理等待；超时 UNKNOWN/FAILED_PREPROCESS |
| `.log` 出现 `Run standard.exe`，STA 尚未生成 | SOLVING，不完成 |
| STA 已出现，LCK 存在 | SOLVING，不完成 |
| STA 已出现，PID 活跃但无 LCK | SOLVING，不完成 |
| STA 仍变化，PID/LCK 暂时均无 | FINISH_CANDIDATE 计数重置，不完成 |
| STA 稳定、无 LCK、无可信 PID、静默超过阈值 | COMPLETED |
| 旧 log 中有完成文本，新作业未启动 | 不得误判 COMPLETED |
| terminate 后 LCK 消失但 PID 活跃 | 继续等待 |
| 重复 poll / 重复 signal | finalize、归档、补位均最多一次 |

### 外部作业

| 场景 | 预期 |
|---|---|
| 直接选择运行目录 | 能识别根目录中的作业 |
| 选择上级目录并启用递归 | 能识别下级目录作业 |
| PID 存在 | 运行中 |
| PID 不存在、LCK 存在 | 疑似异常中断 / 残留锁文件 |
| PID/LCK 均不存在，诊断明确成功 | 已完成 |
| PID/LCK 均不存在，诊断明确失败 | 运行失败 |
| PID/LCK 均不存在，无法确认 | 状态未知 |
| 不同目录同名作业 | 分开显示，不互相污染 |

### 归档

| 场景 | 预期 |
|---|---|
| SSD 作业成功完成 | 整体归档一次 |
| reserved key 释放 | 后续同名作业可再次提交 |
| LCK 残留 | 归档阻塞，不移动目录 |
| Restart 依赖未结束 | 延迟归档 |
| 跨盘移动失败 | 保留错误信息，人工检查，不重复移动 |

---

## 10. 静态检查命令

每轮修改后执行：

```powershell
python -m compileall .baqus_submitter_qt
python -m tabnanny .baqus_submitter_qt
git diff --check
git status --short
git diff --stat
```

禁止自动执行：

```powershell
git add
git commit
git restore
git checkout
git reset
git clean
pyinstaller
```

---

## 11. 每个文件的函数清单与去向

### `abaqus_diagnostics.py`

**当前职责：** Abaqus 文本文件读取、缓存、时间提取、诊断分类、阶段识别与 STA 表格格式化。

**本地依赖：** `.constants`

**优化方向：** **可暂时保留；后续按职责拆分。** 文件规模中等，但读取缓存、分类和 STA 格式化已经是三个不同领域。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| `clear_diagnostic_file_cache()` | 33–45 | 清理某个作业或全部诊断文件尾部缓存。 |
| `decode_abaqus_text()` | 48–56 | 按 GBK/UTF-8/mbcs 顺序解码 Abaqus 文本。 |
| `read_file_tail()` | 59–70 | 读取文件尾部，避免大文件阻塞 UI。 |
| `read_file_tail_cached()` | 73–91 | 仅在文件签名变化时重新读取尾部。 |
| `read_file_head()` | 94–101 | 读取文件头部。 |
| `format_backup_time_tag()` | 104–106 | 将时间戳格式化为备份文件名使用的时间标签。 |
| `parse_datetime_from_abaqus_text()` | 109–198 | 从 log/dat/msg/sta 中提取作业时间。 |
| `get_existing_job_backup_time_tag()` | 201–226 | 获取旧作业备份时间标签。 |
| `extract_key_diagnostic_line()` | 229–251 | 从 Abaqus 输出中提取最关键的一行诊断信息。 |
| `classify_job_text()` | 254–275 | 按失败优先、终止次之、完成最后的顺序分类诊断文本。 |
| `update_abaqus_stage_from_text()` | 278–302 | 从文本识别 pre/package/standard/explicit 启动阶段。 |
| `abaqus_stage_started()` | 305–313 | 判断是否出现过任一 Abaqus 阶段。 |
| `inspect_job_files()` | 316–336 | 聚合 sta/msg/dat/log 尾部并分类。 |
| `inspect_job_files_throttled()` | 339–348 | 节流调用诊断文件聚合。 |
| `parse_sta_progress()` | 351–369 | 解析 STA 最新进度。 |
| `is_sta_progress_line()` | 372–379 | 判断一行是否为 Abaqus .sta 中的增量进度行。 |
| `append_sta_separator_once()` | 382–386 | 在日志中只插入一次 STA 输出区分隔线。 |
| `build_sta_table_header()` | 389–394 | 构造数据、计划或展示内容。 |
| `get_display_width()` | 397–399 | 估算等宽日志中含中文文本的显示宽度。 |
| `format_abaqus_standard_title()` | 402–411 | 将 Abaqus 标题右侧日期时间对齐到日志分隔线右边界。 |
| `format_sta_output_for_log()` | 414–508 | 将 STA 增量文本格式化为右侧日志。 |

### `archive.py`

**当前职责：** 现有结果处理、SSD 工作目录计划桥接、普通文件归档和 Restart 依赖延迟归档协调。

**本地依赖：** `.archive_move`, `.command`, `.models`, `.queue_scheduler`, `.workspace_prepare`

**优化方向：** **继续拆分，优先级中。** 将旧结果处理、Workspace 计划构造、归档协调分离。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| `get_existing_odb_file()` | 43–46 | Return the existing ODB path for a job, if Abaqus would overwrite it. |
| `get_existing_lck_file()` | 49–52 | Return the existing LCK path for a job, if one is present. |
| `_unique_backup_path()` | 55–61 | 实现 `_unique_backup_path` 对应的业务逻辑。 |
| `_unique_sibling_path()` | 64–70 | 实现 `_unique_sibling_path` 对应的业务逻辑。 |
| `backup_existing_result_files()` | 73–96 | Back up the existing ODB and matching STA before a new submit. |
| `delete_existing_result_files()` | 99–109 | Delete the existing ODB and matching STA before a new submit. |
| `is_ssd_independent_work_dir()` | 112–128 | 实现 `is_ssd_independent_work_dir` 对应的业务逻辑。 |
| `build_archive_move_plan()` | 131–141 | 构造数据、计划或展示内容。 |
| `prepare_calculation_workspace()` | 144–172 | 同步准备 SSD 工作目录。 |
| `build_workspace_info()` | 175–193 | 构造数据、计划或展示内容。 |
| `apply_workspace_prepare_result()` | 196–210 | 应用异步结果或转换结果。 |
| `build_workspace_prepare_plan()` | 213–246 | 构造 SSD 工作目录准备计划。 |
| `archive_finished_job_files()` | 249–301 | 移动指定扩展名的结果文件到归档目录。 |
| **class `ArchiveCoordinator`** | 304–413 | 数据结构或服务类。 |
| ↳ `ArchiveCoordinator.__init__()` | 305–311 | 实现 `__init__` 对应的业务逻辑。 |
| ↳ `ArchiveCoordinator.should_defer_archive()` | 313–323 | 实现 `should_defer_archive` 对应的业务逻辑。 |
| ↳ `ArchiveCoordinator.archive_or_defer_run()` | 325–351 | 归档相关处理。 |
| ↳ `ArchiveCoordinator.archive_run()` | 353–385 | 归档相关处理。 |
| ↳ `ArchiveCoordinator.process_deferred_archives()` | 387–406 | 实现 `process_deferred_archives` 对应的业务逻辑。 |
| ↳ `ArchiveCoordinator.mark_archive_result()` | 408–413 | 实现 `mark_archive_result` 对应的业务逻辑。 |

### `archive_move.py`

**当前职责：** SSD 独立计算目录整体移动的纯文件 I/O、安全校验与 LCK 阻塞。

**本地依赖：** 无

**优化方向：** 保留纯 I/O 模块。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| **class `ArchiveMovePlan`** | 13–16 | 数据结构或服务类。 |
| **class `ArchiveMoveResult`** | 20–22 | 数据结构或服务类。 |
| **class `ArchiveMoveBlockedError`** | 25–26 | Raised when a calculation directory is not safe to move yet. |
| `normalized_path()` | 29–32 | 实现 `normalized_path` 对应的业务逻辑。 |
| `_is_relative_to()` | 35–40 | 实现 `_is_relative_to` 对应的业务逻辑。 |
| `_resolved()` | 43–44 | 实现 `_resolved` 对应的业务逻辑。 |
| `unique_archive_destination()` | 47–68 | 实现 `unique_archive_destination` 对应的业务逻辑。 |
| `validate_archive_move_plan()` | 71–92 | 校验源目录和归档根目录关系。 |
| `validate_archive_destination()` | 95–103 | 执行输入或状态校验。 |
| `get_archive_blocking_lck_file()` | 106–108 | 读取或推导数据。 |
| `execute_archive_move()` | 111–133 | 校验无 LCK 后移动整个 SSD 计算目录。 |

### `archive_move_adapter.py`

**当前职责：** 将 SSD 整体归档任务串行放入 Qt 工作线程。

**本地依赖：** `.archive_move`, `.qt_compat`

**优化方向：** **不要直接并入业务文件。** 与 `workspace_prepare_adapter.py` 抽取公共串行 Qt 任务队列基类。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| **class `ArchiveMoveTask`** | 17–22 | 数据结构或服务类。 |
| **class `ArchiveMoveWorker`** | 25–48 | Move one completed calculation directory outside the UI thread. |
| ↳ `ArchiveMoveWorker.__init__()` | 33–35 | 实现 `__init__` 对应的业务逻辑。 |
| ↳ `ArchiveMoveWorker.run()` | 38–48 | 实现 `run` 对应的业务逻辑。 |
| **class `ArchiveMoveService`** | 51–131 | Serial QThread service for archive directory moves. |
| ↳ `ArchiveMoveService.__init__()` | 59–65 | 实现 `__init__` 对应的业务逻辑。 |
| ↳ `ArchiveMoveService.enqueue()` | 67–72 | 实现 `enqueue` 对应的业务逻辑。 |
| ↳ `ArchiveMoveService.is_busy()` | 74–75 | 实现 `is_busy` 对应的业务逻辑。 |
| ↳ `ArchiveMoveService.shutdown()` | 77–79 | 实现 `shutdown` 对应的业务逻辑。 |
| ↳ `ArchiveMoveService._start_next_if_idle()` | 81–103 | 实现 `_start_next_if_idle` 对应的业务逻辑。 |
| ↳ `ArchiveMoveService._on_worker_succeeded()` | 106–108 | 实现 `_on_worker_succeeded` 对应的业务逻辑。 |
| ↳ `ArchiveMoveService._on_worker_blocked()` | 111–113 | 实现 `_on_worker_blocked` 对应的业务逻辑。 |
| ↳ `ArchiveMoveService._on_worker_failed()` | 116–118 | 实现 `_on_worker_failed` 对应的业务逻辑。 |
| ↳ `ArchiveMoveService._on_thread_finished()` | 121–131 | 实现 `_on_thread_finished` 对应的业务逻辑。 |

### `command.py`

**当前职责：** 提交选项、命令构造、作业名/内存/Restart 校验以及 QueueItem 转换。

**本地依赖：** `.constants`, `.models`

**优化方向：** 保留纯逻辑模块。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| **class `SubmitOptions`** | 18–28 | 数据结构或服务类。 |
| `derive_job_name()` | 31–35 | Return the default Abaqus job name for an INP file. |
| `derive_oldjob_name()` | 38–45 | Return the oldjob argument from an ODB path or raw name. |
| `inp_has_restart_keyword()` | 48–57 | Return whether the INP header contains a Restart keyword. |
| `validate_job_name()` | 60–66 | Validate an Abaqus job name. |
| `validate_cpus()` | 69–77 | Validate the CPU field and return the parsed value. |
| `memory_argument()` | 80–88 | Build the Abaqus memory argument. |
| `validate_memory_argument()` | 91–99 | Validate an Abaqus memory argument. |
| `build_abaqus_command()` | 102–132 | Build the Abaqus command line from UI options. |
| `validate_options()` | 135–159 | Validate a submit request. |
| `parse_memory_text()` | 162–173 | 实现 `parse_memory_text` 对应的业务逻辑。 |
| `queue_item_to_options()` | 176–192 | 实现 `queue_item_to_options` 对应的业务逻辑。 |
| `build_direct_submit_queue_item()` | 195–224 | 构造数据、计划或展示内容。 |

### `constants.py`

**当前职责：** 全局常量、状态字符串、进程识别规则、轮询间隔和 UI 基础参数。

**本地依赖：** 无

**优化方向：** 保留为中心常量文件；先统一所有状态字符串。若后续继续增长，再拆 `status_codes.py` 和 runtime 常量。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| `get_physical_cpu_count()` | 6–40 | Return the physical CPU core count, falling back to logical CPUs. |
| `calculate_default_joblist_parallel()` | 129–140 | Return default queue parallel count from 1.5x logical threads and per-job cores. |

### `external_jobs.py`

**当前职责：** 外部 Abaqus 作业扫描 Worker，以及扫描结果与正式队列 QueueItem 的合并。

**本地依赖：** `.models`, `.process_scanner`, `.qt_compat`, `.queue_scheduler`

**优化方向：** 保留作为外部扫描适配层；未来新增共享 timer 监控控制器。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| **class `ExternalJobScanWorker`** | 13–34 | Run the external Abaqus process scan outside the UI thread. |
| ↳ `ExternalJobScanWorker.__init__()` | 19–22 | 实现 `__init__` 对应的业务逻辑。 |
| ↳ `ExternalJobScanWorker.run()` | 24–34 | 实现 `run` 对应的业务逻辑。 |
| `collect_known_external_jobs()` | 37–59 | 实现 `collect_known_external_jobs` 对应的业务逻辑。 |
| `build_queue_item_index()` | 62–71 | 构造数据、计划或展示内容。 |
| `merge_external_scan_results()` | 74–199 | 实现 `merge_external_scan_results` 对应的业务逻辑。 |

### `job_finalizer.py`

**当前职责：** 纯函数终态映射：将 runtime、diagnostics、console、Datacheck 信息映射为展示状态。

**本地依赖：** `.abaqus_diagnostics`, `.constants`

**优化方向：** 保留独立纯函数模块。删除普通作业的猜测式成功路径，只消费 runtime 终态。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| **class `FinalizationInput`** | 19–30 | 数据结构或服务类。 |
| **class `FinalizationDecision`** | 34–36 | 数据结构或服务类。 |
| `run_is_datacheck()` | 39–43 | 实现 `run_is_datacheck` 对应的业务逻辑。 |
| `resolve_final_status_from_console()` | 46–67 | 诊断文件无明确结论时，使用控制台缓存辅助分类。 |
| `resolve_datacheck_final_status()` | 70–97 | Datacheck 专用终态映射。 |
| `resolve_finalization_status()` | 100–170 | 普通作业与 Datacheck 的最终展示状态映射。 |

### `job_runtime.py`

**当前职责：** 内部作业 QProcess 生命周期、控制台输出、STA 轮询、LCK 检查和暂停/终止控制。

**本地依赖：** `.abaqus_diagnostics`, `.constants`, `.qt_compat`

**优化方向：** 保留独立模块，但将证据收集和状态评估拆出 `runtime_evidence.py`。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| `run_is_datacheck()` | 36–38 | 实现 `run_is_datacheck` 对应的业务逻辑。 |
| `mark_solver_seen()` | 41–46 | 记录 solver_seen 及首次原因。 |
| `mark_solver_seen_from_stage()` | 49–51 | 任一阶段出现时把 solver_seen 置真；当前语义过宽。 |
| `solver_start_grace_elapsed()` | 54–62 | 判断 launcher 退出后的 solver 启动宽限期是否已过。 |
| `mark_solver_start_timeout()` | 65–67 | 记录后台求解器未启动超时。 |
| `solver_process_active()` | 70–71 | 实现 `solver_process_active` 对应的业务逻辑。 |
| `reset_stable_no_lck_polls()` | 74–75 | 实现 `reset_stable_no_lck_polls` 对应的业务逻辑。 |
| `increment_stable_no_lck_polls()` | 78–79 | 实现 `increment_stable_no_lck_polls` 对应的业务逻辑。 |
| `runtime_completion_ready()` | 82–90 | 判断内部作业是否满足当前完成门槛。 |
| `mark_runtime_completion_confirmed()` | 93–95 | 实现 `mark_runtime_completion_confirmed` 对应的业务逻辑。 |
| `is_visual_studio_init_noise_line()` | 98–102 | 实现 `is_visual_studio_init_noise_line` 对应的业务逻辑。 |
| `is_star_separator_line()` | 105–107 | 实现 `is_star_separator_line` 对应的业务逻辑。 |
| `filter_launcher_output_noise()` | 110–145 | 过滤 Visual Studio 初始化噪声。 |
| `cache_console_output()` | 148–154 | 保留有限长度的 launcher 控制台缓存。 |
| `mark_console_failure_from_text()` | 157–166 | 仅标记控制台失败，不直接结束。 |
| **class `JobRuntimeController`** | 169–484 | 数据结构或服务类。 |
| ↳ `JobRuntimeController.__init__()` | 176–184 | 实现 `__init__` 对应的业务逻辑。 |
| ↳ `JobRuntimeController.register_run()` | 186–191 | 实现 `register_run` 对应的业务逻辑。 |
| ↳ `JobRuntimeController.unregister_run()` | 193–200 | 实现 `unregister_run` 对应的业务逻辑。 |
| ↳ `JobRuntimeController.start_process()` | 202–255 | 创建 QProcess 和每作业 STA timer。 |
| ↳ `JobRuntimeController.read_process_output()` | 257–293 | 读取 launcher 输出、更新阶段并标记失败。 |
| ↳ `JobRuntimeController.on_process_finished()` | 295–313 | 记录 launcher 退出，但继续监控后台求解器。 |
| ↳ `JobRuntimeController.emit_job_finished_once()` | 315–321 | 通过 finish_emitted 保证终态信号最多发射一次。 |
| ↳ `JobRuntimeController.poll_sta_file()` | 323–432 | 内部作业生命周期核心：检查 LCK、STA、阶段和完成门槛。 |
| ↳ `JobRuntimeController.terminate_job()` | 434–446 | 终止相关处理。 |
| ↳ `JobRuntimeController.suspend_job()` | 448–455 | 实现 `suspend_job` 对应的业务逻辑。 |
| ↳ `JobRuntimeController.resume_job()` | 457–464 | 实现 `resume_job` 对应的业务逻辑。 |
| ↳ `JobRuntimeController.run_abaqus_control()` | 466–484 | 调用 abaqus terminate/suspend/resume。 |

### `main.py`

**当前职责：** Qt 主窗口；当前同时承担 UI、提交、调度、内存回写、外部扫描、归档和运行时展示协调。

**本地依赖：** `.abaqus_diagnostics`, `.archive`, `.archive_move_adapter`, `.command`, `.constants`, `.external_jobs`, `.job_finalizer`, `.job_runtime`, `.memory_adapter`, `.memory_monitor`, `.models`, `.qt_compat`, `.queue_manager`, `.queue_scheduler`, `.ui_components`, `.ui_styles`, `.workspace_prepare_adapter`

**优化方向：** **继续拆分，优先级高。** 目标是让 `MainWindow` 只保留 UI 组装和信号连接。迁移建议见“函数迁移矩阵”。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| **class `MainWindow`** | 126–3154 | Qt version of the main submitter layout. |
| ↳ `MainWindow.__init__()` | 129–217 | 实现 `__init__` 对应的业务逻辑。 |
| ↳ `MainWindow.build_ui()` | 221–682 | 构造数据、计划或展示内容。 |
| ↳ `MainWindow.apply_styles()` | 684–685 | 应用异步结果或转换结果。 |
| ↳ `MainWindow.collect_options()` | 689–701 | 实现 `collect_options` 对应的业务逻辑。 |
| ↳ `MainWindow.current_queue_settings()` | 703–717 | 实现 `current_queue_settings` 对应的业务逻辑。 |
| ↳ `MainWindow.find_queue_item_by_job()` | 719–743 | 按工作目录与 Job 名称查找正式队列记录。 |
| ↳ `MainWindow.select_inp_file()` | 747–755 | 选择或切换当前对象。 |
| ↳ `MainWindow.select_oldjob_file()` | 757–765 | 选择或切换当前对象。 |
| ↳ `MainWindow.select_for_file()` | 767–775 | 选择或切换当前对象。 |
| ↳ `MainWindow.on_input_changed()` | 777–778 | Qt 事件或异步结果回调。 |
| ↳ `MainWindow.update_command_preview()` | 780–785 | 实现 `update_command_preview` 对应的业务逻辑。 |
| ↳ `MainWindow.preview_command()` | 787–794 | 实现 `preview_command` 对应的业务逻辑。 |
| ↳ `MainWindow.submit_job()` | 796–842 | 实现 `submit_job` 对应的业务逻辑。 |
| ↳ `MainWindow.open_queue_manager()` | 844–868 | 实现 `open_queue_manager` 对应的业务逻辑。 |
| ↳ `MainWindow.refresh_visible_queue_manager()` | 870–879 | 队列管理窗口可见时刷新正式队列表格。 |
| ↳ `MainWindow.position_queue_manager()` | 881–894 | 实现 `position_queue_manager` 对应的业务逻辑。 |
| ↳ `MainWindow.terminate_queue_items_by_ids()` | 896–954 | 终止队列管理器中选中的运行中作业。 |
| ↳ `MainWindow.collect_known_external_jobs()` | 956–960 | 实现 `collect_known_external_jobs` 对应的业务逻辑。 |
| ↳ `MainWindow.scan_external_jobs()` | 962–1017 | 扫描数据源。 |
| ↳ `MainWindow.clear_external_scan_worker()` | 1019–1021 | 实现 `clear_external_scan_worker` 对应的业务逻辑。 |
| ↳ `MainWindow.queue_dialog_is_visible()` | 1024–1030 | 实现 `queue_dialog_is_visible` 对应的业务逻辑。 |
| ↳ `MainWindow.show_non_modal_message()` | 1032–1064 | 显示不会锁住主界面的提示窗口。 |
| ↳ `MainWindow.fail_external_scan()` | 1067–1087 | 实现 `fail_external_scan` 对应的业务逻辑。 |
| ↳ `MainWindow.build_queue_item_index()` | 1089–1092 | 构造数据、计划或展示内容。 |
| ↳ `MainWindow.append_external_scan_debug_log()` | 1094–1117 | 实现 `append_external_scan_debug_log` 对应的业务逻辑。 |
| ↳ `MainWindow.finish_external_scan()` | 1120–1184 | 实现 `finish_external_scan` 对应的业务逻辑。 |
| ↳ `MainWindow.start_queue()` | 1186–1203 | 实现 `start_queue` 对应的业务逻辑。 |
| ↳ `MainWindow.stop_queue()` | 1205–1245 | 终止整个队列，并将已运行 Job 标记为正在终止。 |
| ↳ `MainWindow.estimate_effective_available_slots()` | 1247–1282 | 实现 `estimate_effective_available_slots` 对应的业务逻辑。 |
| ↳ `MainWindow.request_dispatch_queue()` | 1284–1290 | 实现 `request_dispatch_queue` 对应的业务逻辑。 |
| ↳ `MainWindow._run_scheduled_dispatch_queue()` | 1292–1297 | 实现 `_run_scheduled_dispatch_queue` 对应的业务逻辑。 |
| ↳ `MainWindow.dispatch_queue()` | 1299–1312 | 实现 `dispatch_queue` 对应的业务逻辑。 |
| ↳ `MainWindow._dispatch_queue_now()` | 1314–1385 | 实现 `_dispatch_queue_now` 对应的业务逻辑。 |
| ↳ `MainWindow.ask_existing_odb_action()` | 1387–1414 | Ask how to handle an existing ODB before submitting. |
| ↳ `MainWindow.handle_existing_job_results()` | 1416–1485 | Apply overwrite/backup handling for existing Abaqus result files. |
| ↳ `MainWindow.archive_move_conflict_message()` | 1487–1498 | 归档相关处理。 |
| ↳ `MainWindow.submit_requires_restart_dependency()` | 1500–1503 | 实现 `submit_requires_restart_dependency` 对应的业务逻辑。 |
| ↳ `MainWindow.block_missing_restart_dependency()` | 1505–1521 | 实现 `block_missing_restart_dependency` 对应的业务逻辑。 |
| ↳ `MainWindow.validate_restart_dependency_before_start()` | 1523–1557 | 执行输入或状态校验。 |
| ↳ `MainWindow.start_job()` | 1559–1629 | 实现 `start_job` 对应的业务逻辑。 |
| ↳ `MainWindow.enqueue_workspace_prepare()` | 1631–1672 | 实现 `enqueue_workspace_prepare` 对应的业务逻辑。 |
| ↳ `MainWindow.on_workspace_prepare_succeeded()` | 1674–1701 | Qt 事件或异步结果回调。 |
| ↳ `MainWindow.on_workspace_prepare_failed()` | 1703–1727 | Qt 事件或异步结果回调。 |
| ↳ `MainWindow.workspace_prepare_task_is_current()` | 1729–1736 | 实现 `workspace_prepare_task_is_current` 对应的业务逻辑。 |
| ↳ `MainWindow.log_workspace_prepare_result()` | 1738–1749 | 实现 `log_workspace_prepare_result` 对应的业务逻辑。 |
| ↳ `MainWindow.continue_start_job_after_workspace_ready()` | 1751–1922 | 实现 `continue_start_job_after_workspace_ready` 对应的业务逻辑。 |
| ↳ `MainWindow.read_process_output()` | 1924–1928 | 读取 launcher 输出、更新阶段并标记失败。 |
| ↳ `MainWindow.on_process_error()` | 1930–1933 | Qt 事件或异步结果回调。 |
| ↳ `MainWindow.on_runtime_job_updated()` | 1935–1939 | Qt 事件或异步结果回调。 |
| ↳ `MainWindow.apply_memory_scan_result()` | 1941–2058 | 应用异步结果或转换结果。 |
| ↳ `MainWindow.on_memory_scan_failed()` | 2060–2061 | Qt 事件或异步结果回调。 |
| ↳ `MainWindow.on_memory_slot_estimate_changed()` | 2063–2073 | 保存最新动态槽位估算。 |
| ↳ `MainWindow.on_process_finished()` | 2075–2080 | 记录 launcher 退出，但继续监控后台求解器。 |
| ↳ `MainWindow.finalize_completed_run()` | 2082–2146 | 实现 `finalize_completed_run` 对应的业务逻辑。 |
| ↳ `MainWindow.notify_job_finished()` | 2148–2183 | 作业结束提醒。 |
| ↳ `MainWindow.poll_sta_file()` | 2185–2190 | 内部作业生命周期核心：检查 LCK、STA、阶段和完成门槛。 |
| ↳ `MainWindow.inspect_finished_job()` | 2192–2203 | 实现 `inspect_finished_job` 对应的业务逻辑。 |
| ↳ `MainWindow.refresh_queue_dependencies()` | 2205–2219 | 更新 Restart 前置依赖状态。 |
| ↳ `MainWindow.resolve_oldjob_source_dir()` | 2221–2262 | 实现 `resolve_oldjob_source_dir` 对应的业务逻辑。 |
| ↳ `MainWindow.run_is_ssd_independent_archive_candidate()` | 2264–2276 | 实现 `run_is_ssd_independent_archive_candidate` 对应的业务逻辑。 |
| ↳ `MainWindow.run_allows_archive_move()` | 2278–2281 | 实现 `run_allows_archive_move` 对应的业务逻辑。 |
| ↳ `MainWindow.archive_move_reserved_key_for_run()` | 2283–2287 | 归档相关处理。 |
| ↳ `MainWindow.mark_archive_move_result()` | 2289–2295 | 实现 `mark_archive_move_result` 对应的业务逻辑。 |
| ↳ `MainWindow.enqueue_archive_move()` | 2297–2345 | 实现 `enqueue_archive_move` 对应的业务逻辑。 |
| ↳ `MainWindow.release_archive_move_context()` | 2347–2353 | 实现 `release_archive_move_context` 对应的业务逻辑。 |
| ↳ `MainWindow.archive_move_context_run()` | 2355–2360 | 归档相关处理。 |
| ↳ `MainWindow.on_archive_move_succeeded()` | 2362–2371 | Qt 事件或异步结果回调。 |
| ↳ `MainWindow.on_archive_move_blocked()` | 2373–2381 | Qt 事件或异步结果回调。 |
| ↳ `MainWindow.on_archive_move_failed()` | 2383–2395 | Qt 事件或异步结果回调。 |
| ↳ `MainWindow.archive_or_defer_finished_job()` | 2397–2439 | 归档相关处理。 |
| ↳ `MainWindow.process_deferred_archives()` | 2441–2458 | 实现 `process_deferred_archives` 对应的业务逻辑。 |
| ↳ `MainWindow.archive_finished_job()` | 2460–2468 | 归档相关处理。 |
| ↳ `MainWindow.handle_archive_result()` | 2470–2479 | 实现 `handle_archive_result` 对应的业务逻辑。 |
| ↳ `MainWindow.mark_archive_result()` | 2481–2489 | 实现 `mark_archive_result` 对应的业务逻辑。 |
| ↳ `MainWindow.toggle_pause_resume()` | 2491–2528 | 暂停 / 恢复当前选中作业。 |
| ↳ `MainWindow.terminate_job()` | 2530–2566 | 手动终止当前 Job。 |
| ↳ `MainWindow.suspend_job()` | 2568–2569 | 实现 `suspend_job` 对应的业务逻辑。 |
| ↳ `MainWindow.resume_job()` | 2571–2572 | 实现 `resume_job` 对应的业务逻辑。 |
| ↳ `MainWindow.run_abaqus_control()` | 2574–2578 | 调用 abaqus terminate/suspend/resume。 |
| ↳ `MainWindow.open_work_dir()` | 2580–2587 | 实现 `open_work_dir` 对应的业务逻辑。 |
| ↳ `MainWindow.select_runtime_log_file()` | 2589–2592 | 选择或切换当前对象。 |
| ↳ `MainWindow.open_job_file()` | 2594–2604 | 实现 `open_job_file` 对应的业务逻辑。 |
| ↳ `MainWindow.update_sta_sticky_header_visibility()` | 2606–2623 | 实现 `update_sta_sticky_header_visibility` 对应的业务逻辑。 |
| ↳ `MainWindow.calculate_runtime_panel_min_width()` | 2626–2668 | Calculate the minimum runtime panel width. |
| ↳ `MainWindow.apply_runtime_panel_width_baseline()` | 2670–2698 | 应用运行详情区宽度下限。 |
| ↳ `MainWindow.show_runtime_panel()` | 2700–2709 | 显示右侧运行区，并根据日志框字体应用宽度下限。 |
| ↳ `MainWindow.refresh_job_selector()` | 2711–2742 | 刷新右侧 Job 选择器，并尽量保留当前选择。 |
| ↳ `MainWindow.on_job_selector_changed()` | 2744–2755 | 切换右侧当前显示的作业。 |
| ↳ `MainWindow.refresh_job_stats()` | 2757–2780 | 更新右侧顶部：运行中 / 完成 / 异常统计。 |
| ↳ `MainWindow.refresh_selected_run_status()` | 2782–2808 | 刷新当前 Job 的标题、状态和概要信息。 |
| ↳ `MainWindow.refresh_selected_run_meta()` | 2810–2907 | 刷新当前 Job 的概要信息区。 |
| ↳ `MainWindow.selected_job_key()` | 2909–2916 | 实现 `selected_job_key` 对应的业务逻辑。 |
| ↳ `MainWindow.select_run()` | 2918–2967 | 选择或切换当前对象。 |
| ↳ `MainWindow.update_queue_status_label()` | 2969–2980 | 实现 `update_queue_status_label` 对应的业务逻辑。 |
| ↳ `MainWindow.update_abaqus_status()` | 2982–2987 | 实现 `update_abaqus_status` 对应的业务逻辑。 |
| ↳ `MainWindow.append_history()` | 2989–3022 | 追加运行记录：时间戳为蓝色，正文使用默认深灰色。 |
| ↳ `MainWindow.trim_log_cache_text()` | 3025–3035 | Keep only the newest lines in the in-memory per-job log cache. |
| ↳ `MainWindow.append_job_log()` | 3037–3095 | 按原始文本追加日志。 |
| ↳ `MainWindow.refresh_pause_button_style()` | 3097–3116 | 根据运行状态切换暂停 / 恢复按钮的文字和颜色。 |
| ↳ `MainWindow.update_process_buttons()` | 3118–3139 | 根据当前 Job 状态更新右侧操作按钮。 |
| ↳ `MainWindow.closeEvent()` | 3141–3154 | 关闭窗口前停止后台内存监测。 |
| `main()` | 3157–3165 | Run the Qt frontend. |

### `memory_adapter.py`

**当前职责：** 内存扫描 QTimer/QThread 适配层，连接 MemoryMonitorService 与进程扫描。

**本地依赖：** `.memory_monitor`, `.models`, `.process_scanner`, `.qt_compat`

**优化方向：** 保留，但删除其对内部生命周期字段 `solver_process_active` 的写入责任。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| **class `TrackedMemoryJob`** | 22–27 | Small serializable description passed to the scan worker. |
| **class `MemoryScanWorker`** | 30–52 | Run one global Abaqus memory scan away from the UI thread. |
| ↳ `MemoryScanWorker.__init__()` | 36–38 | 实现 `__init__` 对应的业务逻辑。 |
| ↳ `MemoryScanWorker.run()` | 41–52 | 实现 `run` 对应的业务逻辑。 |
| **class `QtMemoryMonitorAdapter`** | 55–245 | Coordinate QTimer/QThread with :class:`MemoryMonitorService`. |
| ↳ `QtMemoryMonitorAdapter.__init__()` | 64–79 | 实现 `__init__` 对应的业务逻辑。 |
| ↳ `QtMemoryMonitorAdapter.start()` | 81–86 | 实现 `start` 对应的业务逻辑。 |
| ↳ `QtMemoryMonitorAdapter.stop()` | 88–97 | 实现 `stop` 对应的业务逻辑。 |
| ↳ `QtMemoryMonitorAdapter.schedule_memory_scan()` | 99–131 | 实现 `schedule_memory_scan` 对应的业务逻辑。 |
| ↳ `QtMemoryMonitorAdapter._handle_finished()` | 134–137 | 实现 `_handle_finished` 对应的业务逻辑。 |
| ↳ `QtMemoryMonitorAdapter._handle_failed()` | 140–144 | 实现 `_handle_failed` 对应的业务逻辑。 |
| ↳ `QtMemoryMonitorAdapter._cleanup_thread()` | 147–153 | 实现 `_cleanup_thread` 对应的业务逻辑。 |
| ↳ `QtMemoryMonitorAdapter.register_job()` | 155–168 | 实现 `register_job` 对应的业务逻辑。 |
| ↳ `QtMemoryMonitorAdapter.activate_job()` | 170–172 | 实现 `activate_job` 对应的业务逻辑。 |
| ↳ `QtMemoryMonitorAdapter.finalize_job()` | 174–176 | 实现 `finalize_job` 对应的业务逻辑。 |
| ↳ `QtMemoryMonitorAdapter.apply_scan_payload()` | 178–241 | Apply one worker result to the memory service in the Qt thread. |
| ↳ `QtMemoryMonitorAdapter.format_memory()` | 244–245 | 格式化展示文本。 |
| `get_system_memory_info()` | 248–266 | Return total/available physical memory without touching Qt objects. |

### `memory_monitor.py`

**当前职责：** UI 无关的内存学习、峰值记录和可用槽位估算。

**本地依赖：** `.constants`

**优化方向：** 保留。它是相对清晰的 UI 无关服务。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| **class `JobMemoryUsage`** | 22–29 | 数据结构或服务类。 |
| **class `JobMemoryEstimate`** | 33–43 | 数据结构或服务类。 |
| **class `JobMemoryTrackingState`** | 47–57 | 数据结构或服务类。 |
| **class `MemorySlotEstimate`** | 61–71 | 数据结构或服务类。 |
| `format_memory_size()` | 74–82 | Format memory size using the same compact style as the GUI. |
| `infer_model_group()` | 85–91 | Best-effort group name; never required for memory estimation. |
| `get_job_memory_step()` | 94–98 | Return current Abaqus step from a legacy job-state mapping. |
| `get_memory_safety_factor()` | 101–107 | Return the default memory safety factor for a job group. |
| `sync_legacy_job_state_from_tracking_state()` | 110–121 | Copy service tracking fields back to the legacy Tk dictionary. |
| `sync_tracking_state_from_legacy_job_state()` | 124–141 | Copy legacy Tk dictionary fields into a service tracking state. |
| **class `MemoryMonitorService`** | 144–475 | UI-independent Abaqus memory monitoring service. |
| ↳ `MemoryMonitorService.__init__()` | 152–175 | 实现 `__init__` 对应的业务逻辑。 |
| ↳ `MemoryMonitorService.register_job()` | 177–195 | 实现 `register_job` 对应的业务逻辑。 |
| ↳ `MemoryMonitorService.activate_job()` | 197–203 | 实现 `activate_job` 对应的业务逻辑。 |
| ↳ `MemoryMonitorService.finalize_job()` | 205–210 | 实现 `finalize_job` 对应的业务逻辑。 |
| ↳ `MemoryMonitorService.get_due_jobs()` | 212–218 | 读取或推导数据。 |
| ↳ `MemoryMonitorService.get_next_delay_ms()` | 220–237 | 读取或推导数据。 |
| ↳ `MemoryMonitorService.apply_usage_snapshot()` | 239–271 | 应用异步结果或转换结果。 |
| ↳ `MemoryMonitorService.update_external_job_estimate()` | 273–296 | 实现 `update_external_job_estimate` 对应的业务逻辑。 |
| ↳ `MemoryMonitorService.estimate_per_job_memory()` | 298–326 | 实现 `estimate_per_job_memory` 对应的业务逻辑。 |
| ↳ `MemoryMonitorService.estimate_available_slots()` | 328–360 | 实现 `estimate_available_slots` 对应的业务逻辑。 |
| ↳ `MemoryMonitorService._interval_seconds()` | 362–364 | 实现 `_interval_seconds` 对应的业务逻辑。 |
| ↳ `MemoryMonitorService._estimate_for_job()` | 366–371 | 实现 `_estimate_for_job` 对应的业务逻辑。 |
| ↳ `MemoryMonitorService._get_usage_for_state()` | 373–381 | 实现 `_get_usage_for_state` 对应的业务逻辑。 |
| ↳ `MemoryMonitorService._coerce_usage()` | 384–398 | 实现 `_coerce_usage` 对应的业务逻辑。 |
| ↳ `MemoryMonitorService._usage_memory()` | 401–402 | 实现 `_usage_memory` 对应的业务逻辑。 |
| ↳ `MemoryMonitorService._apply_usage_to_state()` | 404–456 | 实现 `_apply_usage_to_state` 对应的业务逻辑。 |
| ↳ `MemoryMonitorService._memory_event()` | 459–475 | 实现 `_memory_event` 对应的业务逻辑。 |

### `models.py`

**当前职责：** QueueItem 和系统内存结构体。

**本地依赖：** `.constants`

**优化方向：** 保留 `QueueItem`；将 `MemoryStatusEx` 移至 `system_memory.py` 或 `memory_adapter.py`。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| **class `QueueItem`** | 9–42 | 数据结构或服务类。 |
| **class `MemoryStatusEx`** | 45–58 | Windows GlobalMemoryStatusEx structure reused across queue checks. |

### `process_scanner.py`

**当前职责：** 系统进程快照、Abaqus 进程链匹配、外部作业扫描、外部状态分类和内存聚合。

**本地依赖：** `.abaqus_diagnostics`, `.constants`

**优化方向：** **继续拆分，优先级高。** 当前混合快照、匹配、外部扫描、分类和内存统计。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| `normalize_joblist_path()` | 20–22 | Return a stable absolute path for queue file comparisons. |
| `parse_job_name_from_command_line()` | 25–35 | Extract Abaqus job name from a process command line. |
| `find_process_abaqus_job_name()` | 38–62 | Trace process parents until a command line containing job/input is found. |
| `normalize_work_dir()` | 65–70 | Normalize a work directory for Windows-safe comparisons. |
| `join_process_command_line()` | 73–78 | Return a readable command line from psutil cmdline data. |
| `is_abaqus_process_name()` | 81–93 | Return True when a process name is specific enough to be Abaqus-related. |
| `normalize_process_name()` | 96–98 | Return a lowercase process name without path or extension. |
| `is_active_solver_process()` | 101–136 | Return True for real Abaqus solver processes, not launch helpers. |
| `is_related_abaqus_process()` | 139–151 | Return True for Abaqus-related processes used to resolve a job chain. |
| `get_command_parameter_patterns()` | 154–180 | Return cached regexes for extracting Abaqus command parameters. |
| `extract_abaqus_command_parameter()` | 183–193 | Extract one Abaqus command parameter from a command line. |
| `get_process_and_parent_chain()` | 196–216 | Return current process plus parents, limited to a small depth. |
| `is_possible_abaqus_process()` | 219–229 | Return True if a process chain looks Abaqus-related. |
| `get_first_chain_parameter()` | 232–239 | 从当前进程及父链提取 Abaqus 参数。 |
| `get_chain_work_dir()` | 242–263 | 从进程链 cwd/indir/outdir 中确认目标工作目录。 |
| `resolve_external_job_path()` | 266–278 | Resolve a command-line file path relative to an Abaqus work directory. |
| `detect_external_job_type()` | 281–289 | Infer Standard/Explicit/Abaqus from related process names and command lines. |
| `get_job_lock_info()` | 292–304 | Return lock-file presence and age for one Abaqus job. |
| `_diagnostics_status_is_completed()` | 307–308 | 实现 `_diagnostics_status_is_completed` 对应的业务逻辑。 |
| `_diagnostics_status_is_terminated()` | 311–313 | 实现 `_diagnostics_status_is_terminated` 对应的业务逻辑。 |
| `_diagnostics_status_is_failed()` | 316–317 | 实现 `_diagnostics_status_is_failed` 对应的业务逻辑。 |
| `classify_external_job_runtime()` | 320–373 | 结合 solver PID、相关 PID、LCK 和诊断文件判断外部作业状态。 |
| `log_performance()` | 376–379 | Print performance diagnostics only when explicitly enabled. |
| `get_psutil_process_snapshot()` | 382–509 | 缓存系统进程快照，并按需补充命令行、cwd、内存和创建时间。 |
| `fetch_psutil_process_rows_for_external_scan()` | 512–514 | Read process data needed to import externally launched Abaqus jobs. |
| `scan_running_abaqus_jobs_by_psutil()` | 517–698 | 按目标目录扫描外部 Abaqus 作业并输出运行状态。 |
| `fetch_psutil_process_rows()` | 701–703 | Read process rows with psutil, avoiding PowerShell startup overhead. |
| `fetch_windows_process_rows()` | 706–753 | Read Windows process rows through PowerShell CIM. |
| `get_abaqus_job_memory_usage()` | 756–810 | 按 job_name 聚合 Abaqus 进程内存；当前不适合承担生命周期 PID 归属。 |
| `get_cached_abaqus_job_memory_usage()` | 813–815 | Return the latest memory usage cache without scanning processes. |

### `qt_compat.py`

**当前职责：** PySide6/PyQt6 兼容导入边界。

**本地依赖：** 无

**优化方向：** 保留。小文件但边界清晰。

该文件仅包含兼容导入或常量，无函数。

### `queue_manager.py`

**当前职责：** 候选区与正式队列 Dialog，包含表格、文件夹扫描、候选校验、Restart 交互与配置持久化。

**本地依赖：** `.command`, `.constants`, `.models`, `.qt_compat`, `.queue_scheduler`

**优化方向：** **继续拆分，优先级中高。** 当前 Dialog 中混合 UI、候选服务、文件夹线程和配置持久化。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| `atomic_write_json()` | 68–75 | Atomically write JSON so joblist.json is never half-written. |
| **class `FolderScanWorker`** | 78–105 | Discover INP files outside the UI thread. |
| ↳ `FolderScanWorker.__init__()` | 85–88 | 实现 `__init__` 对应的业务逻辑。 |
| ↳ `FolderScanWorker.run()` | 91–105 | 实现 `run` 对应的业务逻辑。 |
| **class `NoHighlightCheckBoxDelegate`** | 108–114 | 数据结构或服务类。 |
| ↳ `NoHighlightCheckBoxDelegate.paint()` | 109–114 | 实现 `paint` 对应的业务逻辑。 |
| **class `QueueManagerDialog`** | 117–1613 | Manage candidate INP files and the formal run queue. |
| ↳ `QueueManagerDialog.__init__()` | 123–140 | 实现 `__init__` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.build_ui()` | 144–364 | 构造数据、计划或展示内容。 |
| ↳ `QueueManagerDialog.make_button()` | 366–369 | 实现 `make_button` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.setup_table()` | 371–380 | 实现 `setup_table` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.apply_table_item_alignment()` | 382–393 | 路径列左对齐，其他列居中显示。 |
| ↳ `QueueManagerDialog.item_row_key()` | 396–397 | 实现 `item_row_key` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.table_row_key()` | 400–405 | 实现 `table_row_key` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.table_row_key_map()` | 407–413 | 实现 `table_row_key_map` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.selected_table_row_keys()` | 415–421 | 实现 `selected_table_row_keys` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.current_table_row_key()` | 423–427 | 实现 `current_table_row_key` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.resize_table_to_count()` | 429–433 | 实现 `resize_table_to_count` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.update_table_cell()` | 435–464 | 实现 `update_table_cell` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.restore_table_view_state()` | 466–503 | 实现 `restore_table_view_state` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.sync_table_rows()` | 505–550 | 实现 `sync_table_rows` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.load_saved_paths()` | 552–558 | 实现 `load_saved_paths` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.save_saved_paths()` | 560–567 | 实现 `save_saved_paths` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.choose_ssd_dir()` | 569–579 | 实现 `choose_ssd_dir` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.choose_archive_dir()` | 581–589 | 实现 `choose_archive_dir` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.add_candidate()` | 593–642 | 实现 `add_candidate` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.new_candidate_add_stats()` | 645–659 | 实现 `new_candidate_add_stats` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.add_skip_detail()` | 662–665 | 实现 `add_skip_detail` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.candidate_skip_reason()` | 667–677 | 实现 `candidate_skip_reason` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.add_candidate_batch()` | 679–710 | 实现 `add_candidate_batch` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.format_candidate_add_stats_message()` | 712–748 | 格式化展示文本。 |
| ↳ `QueueManagerDialog.show_candidate_add_stats()` | 750–758 | 实现 `show_candidate_add_stats` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.add_current_inp()` | 760–763 | 实现 `add_current_inp` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.add_inp_files()` | 765–775 | 实现 `add_inp_files` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.scan_folder()` | 777–784 | 扫描数据源。 |
| ↳ `QueueManagerDialog.start_folder_scan()` | 786–807 | 实现 `start_folder_scan` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.set_folder_scan_busy()` | 809–811 | 更新状态或 UI。 |
| ↳ `QueueManagerDialog.handle_folder_scan_finished()` | 813–817 | 实现 `handle_folder_scan_finished` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.handle_folder_scan_failed()` | 819–822 | 实现 `handle_folder_scan_failed` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.handle_folder_scan_done()` | 824–829 | 实现 `handle_folder_scan_done` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.closeEvent()` | 831–833 | 实现 `closeEvent` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.has_result_files()` | 835–837 | 实现 `has_result_files` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.detect_restart()` | 839–851 | 实现 `detect_restart` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.validate_candidate()` | 853–890 | 执行输入或状态校验。 |
| ↳ `QueueManagerDialog.candidate_conflict_item()` | 892–904 | 实现 `candidate_conflict_item` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.revalidate_candidates()` | 906–910 | 实现 `revalidate_candidates` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.on_candidate_cell_changed()` | 914–921 | Qt 事件或异步结果回调。 |
| ↳ `QueueManagerDialog.on_candidate_item_double_clicked()` | 923–927 | Qt 事件或异步结果回调。 |
| ↳ `QueueManagerDialog.set_candidate_selection()` | 929–933 | 更新状态或 UI。 |
| ↳ `QueueManagerDialog.invert_candidate_selection()` | 935–939 | 实现 `invert_candidate_selection` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.remove_selected_candidates()` | 941–947 | 实现 `remove_selected_candidates` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.format_count_distribution()` | 950–962 | 格式化展示文本。 |
| ↳ `QueueManagerDialog.expected_oldjob_path_for_item()` | 964–976 | 实现 `expected_oldjob_path_for_item` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.restart_dependency_source_items()` | 978–994 | 实现 `restart_dependency_source_items` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.restart_dependency_resolved()` | 996–1022 | 实现 `restart_dependency_resolved` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.build_restart_dependency_options()` | 1024–1043 | 构造数据、计划或展示内容。 |
| ↳ `QueueManagerDialog.prompt_restart_dependency()` | 1045–1148 | 实现 `prompt_restart_dependency` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.ensure_restart_dependencies_for_candidates()` | 1150–1169 | 实现 `ensure_restart_dependencies_for_candidates` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.build_confirm_candidates_summary()` | 1171–1205 | 构造数据、计划或展示内容。 |
| ↳ `QueueManagerDialog.confirm_selected_candidates_action()` | 1207–1215 | 实现 `confirm_selected_candidates_action` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.confirm_candidates()` | 1217–1267 | 实现 `confirm_candidates` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.selected_queue_rows()` | 1271–1272 | 实现 `selected_queue_rows` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.cancel_selected_pending()` | 1274–1287 | 实现 `cancel_selected_pending` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.edit_selected_pending()` | 1289–1332 | 实现 `edit_selected_pending` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.terminate_selected_running()` | 1334–1346 | 终止相关处理。 |
| ↳ `QueueManagerDialog.clear_finished()` | 1348–1350 | 实现 `clear_finished` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.choose_work_dir()` | 1352–1355 | 实现 `choose_work_dir` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.set_external_scan_busy()` | 1357–1381 | 更新外部作业扫描状态。 |
| ↳ `QueueManagerDialog.request_external_scan()` | 1383–1404 | 实现 `request_external_scan` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.refresh_tables()` | 1408–1410 | 刷新界面或派生状态。 |
| ↳ `QueueManagerDialog.refresh_candidate_table()` | 1412–1421 | 刷新界面或派生状态。 |
| ↳ `QueueManagerDialog.refresh_queue_table()` | 1423–1431 | 刷新界面或派生状态。 |
| ↳ `QueueManagerDialog.update_queue_memory_cells()` | 1433–1465 | Update volatile queue cells without rebuilding the whole table. |
| ↳ `QueueManagerDialog.refresh_summaries()` | 1467–1526 | 刷新界面或派生状态。 |
| ↳ `QueueManagerDialog.candidate_row_values()` | 1528–1540 | 实现 `candidate_row_values` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.format_runtime_memory()` | 1543–1562 | 将最近一次统计到的内存占用量格式化为 MB 或 GB。 |
| ↳ `QueueManagerDialog.formal_row_values()` | 1564–1577 | 实现 `formal_row_values` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.ensure_candidate_columns_initialized()` | 1579–1583 | 实现 `ensure_candidate_columns_initialized` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.ensure_formal_columns_initialized()` | 1585–1589 | 实现 `ensure_formal_columns_initialized` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.resize_candidate_columns()` | 1591–1594 | 实现 `resize_candidate_columns` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.resize_formal_columns()` | 1596–1599 | 实现 `resize_formal_columns` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.default_work_dir()` | 1601–1606 | 实现 `default_work_dir` 对应的业务逻辑。 |
| ↳ `QueueManagerDialog.work_dir_from_queue()` | 1608–1613 | 实现 `work_dir_from_queue` 对应的业务逻辑。 |

### `queue_scheduler.py`

**当前职责：** UI 无关的队列冲突键、活动状态、Restart 依赖关系和活动作业集合。

**本地依赖：** `.constants`, `.models`

**优化方向：** 保留纯逻辑模块；统一状态常量并修正目录字段语义。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| `normalize_work_dir()` | 19–22 | 实现 `normalize_work_dir` 对应的业务逻辑。 |
| `managed_job_key()` | 25–29 | 以规范化 work_dir + 小写 job_name 形成管理键。 |
| `effective_queue_item_work_dir()` | 32–38 | 推导 QueueItem 的实际计算目录。 |
| `queue_item_conflict_key()` | 41–45 | 实现 `queue_item_conflict_key` 对应的业务逻辑。 |
| `submit_effective_work_dir()` | 48–53 | 实现 `submit_effective_work_dir` 对应的业务逻辑。 |
| `submit_conflict_key()` | 56–60 | 实现 `submit_conflict_key` 对应的业务逻辑。 |
| `find_formal_queue_conflict()` | 63–77 | 实现 `find_formal_queue_conflict` 对应的业务逻辑。 |
| `active_submit_conflict_message()` | 80–103 | 实现 `active_submit_conflict_message` 对应的业务逻辑。 |
| `managed_active_statuses()` | 106–112 | 实现 `managed_active_statuses` 对应的业务逻辑。 |
| `queue_item_is_finished()` | 115–125 | 实现 `queue_item_is_finished` 对应的业务逻辑。 |
| `oldjob_name_from_item()` | 128–133 | 实现 `oldjob_name_from_item` 对应的业务逻辑。 |
| `find_queue_oldjob_item()` | 136–148 | 实现 `find_queue_oldjob_item` 对应的业务逻辑。 |
| `queue_item_dependency_state()` | 151–166 | 实现 `queue_item_dependency_state` 对应的业务逻辑。 |
| `refresh_queue_dependencies()` | 169–191 | 更新 Restart 前置依赖状态。 |
| `queue_item_depends_on_job()` | 194–214 | 实现 `queue_item_depends_on_job` 对应的业务逻辑。 |
| `unfinished_restart_dependents()` | 217–231 | 实现 `unfinished_restart_dependents` 对应的业务逻辑。 |
| `get_managed_active_job_keys()` | 234–257 | 读取或推导数据。 |
| `get_managed_active_job_names()` | 260–291 | 读取或推导数据。 |

### `ui_components.py`

**当前职责：** 运行状态文本、显示宽度、内存摘要表和文件选择组件。

**本地依赖：** `.qt_compat`

**优化方向：** 保留。运行视图继续拆分后可承接更多纯展示函数。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| `format_elapsed_seconds()` | 11–23 | 将秒数格式化为旧版风格的紧凑耗时。 |
| `format_run_status()` | 26–55 | 生成右侧 Job 名称右边的详细状态。 |
| `format_abaqus_stage()` | 58–70 | Return the most specific Abaqus launcher stage known before STA progress exists. |
| `build_job_display_label()` | 73–85 | Build a UI-only job label without changing the real Abaqus job name. |
| `duplicated_runtime_job_names()` | 88–95 | 实现 `duplicated_runtime_job_names` 对应的业务逻辑。 |
| `runtime_job_display_label()` | 98–115 | 实现 `runtime_job_display_label` 对应的业务逻辑。 |
| `safe_int()` | 118–123 | 将任意值安全转换为 int，转换失败时返回默认值。 |
| `display_text_width()` | 126–141 | Return the terminal-style display width of text. |
| `pad_display_text()` | 144–149 | Pad text according to its visible display width. |
| `build_memory_summary_table()` | 152–191 | Build the two-row runtime memory summary table. |
| **class `FilePickerRow`** | 194–226 | Original-style one-line file selector. |
| ↳ `FilePickerRow.__init__()` | 199–215 | 实现 `__init__` 对应的业务逻辑。 |
| ↳ `FilePickerRow.text()` | 217–218 | 实现 `text` 对应的业务逻辑。 |
| ↳ `FilePickerRow.set_path()` | 220–226 | 更新状态或 UI。 |

### `ui_styles.py`

**当前职责：** 主窗口样式表和 UI 尺寸常量。

**本地依赖：** 无

**优化方向：** 保留。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| `build_main_stylesheet()` | 32–233 | 构造数据、计划或展示内容。 |

### `workspace_prepare.py`

**当前职责：** 复制 INP 与 Restart 依赖文件到 SSD 计算目录的纯文件 I/O。

**本地依赖：** 无

**优化方向：** 保留纯 I/O 模块；接收从 `archive.py` 迁入的 workspace 计划函数。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| **class `WorkspacePreparePlan`** | 26–32 | 数据结构或服务类。 |
| **class `WorkspacePrepareResult`** | 36–40 | 数据结构或服务类。 |
| `copy_restart_dependency_files()` | 43–60 | 按精确 stem 复制 Restart 依赖文件。 |
| `execute_workspace_prepare()` | 63–92 | 复制 INP 和 Restart 依赖文件。 |

### `workspace_prepare_adapter.py`

**当前职责：** 将工作目录准备任务串行放入 Qt 工作线程。

**本地依赖：** `.qt_compat`, `.workspace_prepare`

**优化方向：** **不要直接并入业务文件。** 与 `archive_move_adapter.py` 抽取公共串行 Qt 任务队列基类。

| 函数 / 类 | 行号 | 当前功能 |
|---|---:|---|
| **class `WorkspacePrepareTask`** | 13–16 | 数据结构或服务类。 |
| **class `WorkspacePrepareWorker`** | 19–39 | Run one workspace preparation plan outside the UI thread. |
| ↳ `WorkspacePrepareWorker.__init__()` | 26–28 | 实现 `__init__` 对应的业务逻辑。 |
| ↳ `WorkspacePrepareWorker.run()` | 31–39 | 实现 `run` 对应的业务逻辑。 |
| **class `WorkspacePrepareService`** | 42–123 | Serial QThread service for workspace preparation. |
| ↳ `WorkspacePrepareService.__init__()` | 49–55 | 实现 `__init__` 对应的业务逻辑。 |
| ↳ `WorkspacePrepareService.enqueue()` | 57–62 | 实现 `enqueue` 对应的业务逻辑。 |
| ↳ `WorkspacePrepareService.is_busy()` | 64–65 | 实现 `is_busy` 对应的业务逻辑。 |
| ↳ `WorkspacePrepareService.cancel_pending()` | 67–73 | 实现 `cancel_pending` 对应的业务逻辑。 |
| ↳ `WorkspacePrepareService.shutdown()` | 75–77 | 实现 `shutdown` 对应的业务逻辑。 |
| ↳ `WorkspacePrepareService._start_next_if_idle()` | 79–100 | 实现 `_start_next_if_idle` 对应的业务逻辑。 |
| ↳ `WorkspacePrepareService._on_worker_succeeded()` | 103–105 | 实现 `_on_worker_succeeded` 对应的业务逻辑。 |
| ↳ `WorkspacePrepareService._on_worker_failed()` | 108–110 | 实现 `_on_worker_failed` 对应的业务逻辑。 |
| ↳ `WorkspacePrepareService._on_thread_finished()` | 113–123 | 实现 `_on_thread_finished` 对应的业务逻辑。 |

---

## 12. 最终建议

当前最合适的执行方式不是立即大规模拆目录，而是：

```text
先修 P0/P1 明确问题
→ 再收敛内部状态机
→ 再完成外部作业共享监控
→ 最后逐步迁移大文件中的职责
```

第一轮修改完成前，不建议继续扩大 UI 功能。生命周期、归档键和目录字段语义稳定之后，再做模块重构，风险最低。
