# Abaqus ODB PostProcessor

Abaqus 2025 ODB 批量后处理工具。当前工作树只保留唯一最新版代码；旧实现通过 Git 历史保存，不再使用 `*_v2.py`、`*_final.py` 等本地副本。

GitHub：<https://github.com/Tch02w/AbaqusODBPostProcessor>

## 本地目录职责

- 项目与源码：`G:\PythonProject\AbaqusODBPostProcessor`
- ODB：`G:\Job\GJA_ODB`
- 提取结果：`G:\Job\GJA_ODB\AbaqusODBPostProcessor_Results`
- 扫描/预扫描缓存：`%TEMP%\AbaqusODBPostProcessor`
- 用户界面状态：`%APPDATA%\AbaqusODBPostProcessor\project_state.json`

项目目录中不存放 ODB、云图、CSV、GIF、XLSX、运行结果或扫描缓存。`.venv`、`.git`、源代码、测试、配置、文档与启动入口保留在项目中。

## 启动

- 双击 `Abaqus ODB 后处理器.lnk`；或
- 双击 `启动AbaqusODB后处理器.bat`。

双击 `打开源代码文件夹.bat` 可直接打开当前源码目录。

## 文件名规则

以 `GJA-1-R_U100D` 为例：

- `GJA`：A 方案钢筋混凝土模型；
- `1`：样本编号；
- `R`：配筋模型；
- `U100D`：上拔位移 100 mm，对应全局 3 方向；
- `V20D`：水平位移 20 mm，对应全局 1 方向；
- 同时存在 U、V：复合加载，读取 1+3 方向；
- `miu03/miu04`：变参数标签，不参与工况组名。

扫描后自动建立 `工况-U100D`、`工况-U40D_V20D` 等初始组。主筋直径按样本编号自动匹配，并允许手动覆盖。

## 扫描、分组与并行

1. 点击“发现并选择 ODB”，第一阶段只枚举文件。
2. 勾选需要 Abaqus 打开的 ODB 后点击“开始读取”。
3. “全部 ODB”支持 `Ctrl` 离散多选、`Shift` 连续多选和批量拖放。
4. ODB 右侧灰色数字表示当前所属组数；同一 ODB 可属于多个组。
5. 并行数范围 1–4，默认 2；每个工作单元是独立 Abaqus CAE 进程。
6. 组内 ODB 完成预扫描后统一确定图例上下限，再并行正式提取。
7. “取消当前批次”只停止本工具启动的 CAE 后处理进程，不结束 `standard.exe` 求解。

## 结果结构

```text
G:\Job\GJA_ODB\AbaqusODBPostProcessor_Results\
├─ 工况-U100D\
│  └─ YYYYMMDD_HHMMSS\
│     └─ ODB名称\
├─ 工况-U40D_V20D\
├─ 未分组\
├─ _批次记录\
└─ _历史测试\
```

自定义对比组使用对应组名作为结果第一层目录。同一 ODB 属于多个组时，只提取一次数值数据，其他组复用数值缓存并按各组图例重渲染。

## 当前代码结构

- `app.py`：两阶段选择和程序入口；
- `main_window.py`：基础表格与作业配置；
- `comparison_groups.py`：非嵌套、多成员对比组；
- `batch_window.py`：自动命名、并行批处理、取消与结果布局；
- `paths.py`：缓存、状态与结果路径；
- `runner.py` / `process_runner.py` / `runner_parallel.py`：进程调度；
- `postprocess.py` / `postprocess_core.py`：宿主机后处理；
- `abaqus_scripts/`：唯一当前 Abaqus 运行链；
- `tests/`：自动化测试。

## Git 版本管理

- `127df4b`：整理前全部旧版本与开发脚本快照；
- 当前提交：单一最新版工作树。

后续迭代直接修改当前文件并提交 Git，不再复制带版本号的 Python 文件。

常用命令：

```powershell
git status -sb
git add <本次修改文件>
git commit -m "说明本次修改"
git push origin main
```
