# Tk / Qt 功能对照表

说明：本表用于跟踪旧 Tk 版与 Qt 版的业务能力迁移。只有手动回归通过后，完成情况才应改为“完成”。

| Tk 功能 | Qt 当前状态 | Qt 目标状态 | 涉及模块 | 测试方式 | 完成情况 |
| --- | --- | --- | --- | --- | --- |
| 选择 INP 文件 | 已实现 | 等同 Tk | `abaqus_submitter_qt.main`, `command` | 手动选择 `.inp` | 待验证 |
| 自动生成 Job 名称 | 已实现 | 等同 Tk | `command` | 选择 INP 后预览命令 | 待验证 |
| 选择重启动 ODB | 已实现 | 等同 Tk | `main`, `command` | 选择 `.odb` 并预览 oldjob | 待验证 |
| oldjob 参数生成 | 已实现 | 等同 Tk | `command` | 预览 restart 命令 | 待验证 |
| 选择 FOR 文件 | 已实现 | 等同 Tk | `main`, `command` | 选择 `.for` 并预览 user 参数 | 待验证 |
| user=FOR 参数生成 | 已实现 | 等同 Tk | `command` | 预览命令 | 待验证 |
| Core 设置 | 已实现 | 等同 Tk | `main`, `command` | 修改核心数并提交 | 待验证 |
| Mem 设置 | 已实现 | 等同 Tk | `main`, `command` | `%/GB/MB` 预览 | 待验证 |
| interactive | 已实现 | 等同 Tk | `main`, `command` | 勾选并预览 | 待验证 |
| datacheck | 已实现 | 等同 Tk | `main`, `command` | 勾选并预览/提交 | 待验证 |
| ask_delete=OFF | 已实现 | 等同 Tk | `command` | 预览命令 | 待验证 |
| 命令预览 | 已实现 | 等同 Tk | `main` | 点击预览 | 待验证 |
| 普通 Job 提交 | 已实现 | 等同 Tk | `main` | 提交测试 INP | 待验证 |
| Restart Job 提交 | 部分实现 | 等同 Tk | `main`, `workspace_archive` | 依赖 ODB 提交 | 待迁移/验证 |
| Datacheck 提交 | 已实现 | 等同 Tk | `main`, `command` | 提交 datacheck | 待验证 |
| 暂停 | 已实现 | 等同 Tk | `main` | 点击暂停 | 待验证 |
| 恢复 | 已实现 | 等同 Tk | `main` | 暂停后恢复 | 待验证 |
| 终止 | 已实现 | 等同 Tk | `main` | 点击终止 | 待验证 |
| 打开工作目录 | 已实现 | 等同 Tk | `main` | 点击按钮 | 待验证 |
| 打开 STA/MSG/DAT | 已实现 | 等同 Tk | `main` | 点击按钮 | 待验证 |
| QProcess 输出读取 | 已实现 | 等同 Tk | `main` | 查看运行监控输出 | 待验证 |
| 进程结束处理 | 已实现 | 等同 Tk | `main`, `abaqus_diagnostics` | 正常/失败/终止 | 待验证 |
| STA 增量读取 | 已实现 | 等同 Tk | `main`, `abaqus_diagnostics` | 运行中查看 STA | 待验证 |
| STA 格式化输出 | 已实现 | 等同 Tk | `abaqus_diagnostics` | 查看日志格式 | 待验证 |
| MSG/DAT/STA 联合诊断 | 已实现 | 等同 Tk | `abaqus_diagnostics` | 失败模型诊断 | 待验证 |
| 候选区加入当前 INP | 已实现 | 等同 Tk | `queue_manager` | 队列管理 | 待验证 |
| 多选添加 INP | 已实现 | 等同 Tk | `queue_manager` | 添加多个 INP | 待验证 |
| 扫描文件夹 | 已实现 | 等同 Tk | `queue_manager` | 扫描目录 | 待验证 |
| 扫描子文件夹 | 已实现 | 等同 Tk | `queue_manager` | 勾选后扫描 | 待验证 |
| 候选项校验 | 已实现 | 等同 Tk | `queue_manager` | 选择/扫描 INP | 待验证 |
| 确认进入正式队列 | 已实现 | 等同 Tk | `queue_manager`, `main` | 加入正式队列 | 待验证 |
| 正式队列状态流转 | 部分实现 | 等同 Tk | `main`, `queue_manager` | 启动/完成/失败 | 待迁移/验证 |
| 编辑待运行 Job | 已实现 | 等同 Tk | `queue_manager` | 编辑队列项 | 待验证 |
| 取消待运行 Job | 已实现 | 等同 Tk | `queue_manager` | 取消队列项 | 待验证 |
| 终止运行中 Job | 已实现 | 等同 Tk | `queue_manager`, `main` | 终止队列项 | 待验证 |
| 最大并行数 | 已实现 | 等同 Tk | `main` | 修改并发数 | 待验证 |
| 自动补位 | 已实现 | 等同 Tk | `main` | 多任务队列 | 待验证 |
| Restart 依赖识别 | 部分实现 | 等同 Tk | `main`, `queue_manager` | restart 队列 | 待迁移/验证 |
| 前置失败跳过后续 Restart | 已实现 | 等同 Tk | `main` | 前置失败场景 | 待验证 |
| SSD 工作目录 | 已实现 | 等同 Tk | `queue_manager`, `workspace_archive` | 设置 SSD 并提交 | 待验证 |
| ARC 归档目录 | 已实现 | 等同 Tk | `queue_manager`, `workspace_archive` | 完成后归档 | 待验证 |
| 前置结果延迟归档 | 部分实现 | 等同 Tk | `main`, `workspace_archive` | restart 依赖链 | 待迁移/验证 |
| 外部 Job 扫描 | 已实现 | 等同 Tk | `main`, `process_scanner` | 扫描运行进程 | 待验证 |
| 后台扫描 | 已实现 | 等同 Tk | `main` | 扫描时拖动窗口 | 待验证 |
| 外部 Job 状态更新 | 部分实现 | 等同 Tk | `main`, `process_scanner` | 导入后自然结束 | 待迁移/验证 |
| 内存全局扫描聚合 | 已抽出逻辑，Qt 适配层新增 | 等同 Tk | `memory_monitor`, `memory_adapter`, `process_scanner` | 手动内存测试/运行任务 | 待接入/验证 |
| learning / patrol | 已抽出逻辑 | 等同 Tk | `memory_monitor` | `manual_test_memory_monitor.py` | 完成逻辑测试 |
| Step 峰值 | 已抽出逻辑 | 等同 Tk | `memory_monitor` | `manual_test_memory_monitor.py` | 完成逻辑测试 |
| 动态内存槽位 | 已抽出逻辑 | 等同 Tk | `memory_monitor` | `manual_test_memory_monitor.py` | 待 Qt 接入 |
| joblist.json 保存 | 部分实现 | 等同 Tk | `persistence`, `queue_manager` | 关闭/重开 | 待迁移/验证 |
| 正式队列恢复 | 部分实现 | 等同 Tk | `queue_manager` | 重启恢复 | 待迁移/验证 |
| 结束提醒 | 部分实现 | 等同 Tk | `main` | 完成/失败/终止 | 待验证 |
| 大文件后台操作 | 部分实现 | 等同 Tk | `workspace_archive` | SSD/ARC 操作时拖动窗口 | 待迁移/验证 |
| 关闭前安全清理 | 部分实现 | 等同 Tk | `main` | 关闭运行中程序 | 待迁移/验证 |

当前结论：当前尚不能声明 Qt 与 Tk 完全功能对等。
## 本轮更新：Qt 内存监测接入

| 功能 | 本轮状态 | 验证方式 | 备注 |
| --- | --- | --- | --- |
| 内存全局扫描聚合 | 已接入，待 Abaqus 实机验证 | `manual_test_qt_memory_adapter.py` | QThread Worker 一次扫描返回 `usage_by_job` |
| learning / patrol | 完成逻辑测试 | `manual_test_qt_memory_adapter.py` | STA 出现后激活，稳定后转入 patrol |
| Step 峰值 | 完成逻辑测试 | `manual_test_qt_memory_adapter.py` | `poll_sta_file()` 记录当前 Step |
| 动态内存槽位 | 已接入，待 Abaqus 实机验证 | `manual_test_qt_memory_adapter.py` | `dispatch_queue()` 同时考虑手动并行和内存槽位 |
| 外部 Job 内存估计 | 已接入，待 Abaqus 实机验证 | `manual_test_qt_memory_adapter.py` | 外部扫描导入时更新 estimate |
| 外部 Job 占用槽位 | 已接入，待 Abaqus 实机验证 | `manual_test_qt_memory_adapter.py` | 运行中/启动中/确认中/终止中计入 |
| GUI Job 和外部 Job 去重 | 完成逻辑测试 | `manual_test_qt_memory_adapter.py` | 去重键为 `work_dir + job_name.lower()` |
| 正式队列表格内存列局部刷新 | 已接入，待界面实机验证 | `manual_test_qt_memory_adapter.py` + 手动 UI | 只更新 Mem/Status/Message 单元格 |

当前仍不能声明 Qt 与 Tk 完全功能对等。
