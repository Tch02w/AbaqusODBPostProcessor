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

扫描后自动建立 `工况-U100D`、`工况-U40D_V20D` 等浏览分类。分类不是对比组，
不会参与组图例或组计算。主筋直径按样本编号自动匹配，并允许手动覆盖。

## 扫描、分组与并行

1. 点击“发现并选择 ODB”，第一阶段只枚举文件。
2. ODB 选择窗口分列显示文件名、大小、修改时间、兼容性和说明。
3. 点击“检测所选 ODB”，或直接点击“开始读取”触发自动检测。
4. 检测结果区分：可直接读取、需要升级、版本高于本机、无效/损坏、空文件和文件不存在。
5. 对 Abaqus 2021HF4 等旧版 ODB，点击“升级旧版 ODB 到 2025”后，新版继续
   使用原文件名，旧版改名为 `原名-old.odb` 保留；已有同名备份时自动递增编号。
   程序先生成并验证临时新版，成功后才交换名称；失败时自动恢复原文件。
6. 升级成功后会再次以只读方式打开新 ODB，验证通过后才允许进入正式扫描。
7. 左侧“全部 ODB”和“按工况分类”只作为 ODB 来源；选中根节点或工况节点不会启动计算。
8. 右侧横向标签页对应用户创建的对比组。先打开目标标签，再从左侧将 ODB 拖入右侧
   表格即可添加成员；标签右键可新建、重命名、设置图例范围或删除组。
9. 左侧 ODB 支持 `Ctrl` 离散多选、`Shift` 连续多选和一次拖放多个文件。双击单个
   ODB（或使用右键菜单）可切换为单项配置视图。
10. 只有单个 ODB 和用户创建的对比组可运行；“运行全部项目”运行对比组，并将未加入
    用户对比组的已启用 ODB 作为单项运行；同一 ODB 可以属于多个对比组。
11. 并行数范围 1–4，默认 2；首轮 ODB 读取和正式后处理都使用该设置，每个工作
    单元是独立 Abaqus 进程。
12. 组内 ODB 完成预扫描后统一确定图例上下限，再并行正式提取。
13. “取消当前批次”只停止本工具启动的 CAE 后处理进程，不结束 `standard.exe` 求解。
14. 运行日志自动隐藏 Visual Studio Developer Command Prompt、`vcvars.bat` 和
    DPC++ 环境初始化噪声，保留许可证信息、ODB 错误、脚本异常和实际计算警告。

表格下拉框和数字输入框在关闭状态下会忽略鼠标滚轮，避免浏览表格时误改配置；
展开下拉列表后仍可正常滚动。界面使用 Qt 自带的 Fusion 浅色按钮、输入框、下拉框、
表格、滚动条和进度条，不再自绘控件；全局统一为 11pt 字号。ODB 树不显示所属
次数徽标；下拉列表关闭横向滚动条。

作业配置表不使用行选择或当前行高亮；点击只操作单元格内的复选框、下拉框和输入框，
不会再改变标题样式或在控件周围显示选中行底色。

荷载—位移等基础历程和所有 GIF 始终使用开始 Step 到结束 Step 的全部正式加载帧。
“帧模式”只限制 FreeBodyCut、纵筋数值提取等高开销计算；因此选择末帧不会再把
荷载—位移曲线截成一个点，也不会再生成只有末帧的普通动画。

每次后处理同时保留两套 PNG：`frames/`、`contours/` 是 Abaqus 直接输出且完全不
改写的原图；`frames_transparent/`、`contours_transparent/` 是去除近白背景后的
透明 PNG 副本。GIF 从未处理的完整帧生成，避免透明 GIF 调色板造成颜色或动画损失。

“输出图像”提供横向、纵向和单位设置。默认 `1500×1000 pixel`；pixel 模式按
输入比例创建当前屏幕可容纳的最大 Abaqus Viewport，再输出指定像素。切换为 mm
时，输入值直接作为 Viewport 的毫米尺寸并按屏幕原生像素输出。两种模式都不会再
使用固定的 180×120 mm 小 Viewport，因此图例、文字和模型保持正常比例。

升级时 Abaqus 会在升级文件附近生成转换日志。应保留并检查该日志中的警告；如果
ODB 正在从超算下载或复制，应等待文件大小稳定后再检测。

## 结果结构

```text
G:\Job\GJA_ODB\AbaqusODBPostProcessor_Results\
├─ 用户对比组名\
│  └─ YYYYMMDD_HHMMSS\
│     └─ ODB名称\
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
- `ui_style.py`：统一字体、控件尺寸、间距和高可读性样式；
- `paths.py`：缓存、状态与结果路径；
- `runner.py` / `process_runner.py` / `runner_parallel.py`：进程调度；
- `postprocess.py` / `postprocess_core.py`：宿主机后处理；
- `abaqus_scripts/odb_compatibility.py`：ODB 有效性检测和旧版本升级；
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
