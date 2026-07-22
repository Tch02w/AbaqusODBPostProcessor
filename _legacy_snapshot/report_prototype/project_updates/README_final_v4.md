# Abaqus ODB PostProcessor 0.4

Abaqus 2025 ODB 批量后处理工具，支持选择性扫描、多对比组统一图例、云图/GIF、荷载位移、纵筋实际轴力及桩体轴力—弯矩埋深结果。

## 快速启动

双击项目根目录中的：

- `Abaqus ODB 后处理器.lnk`：无控制台窗口启动；
- `启动AbaqusODB后处理器.bat`：需要查看启动错误时使用。

也可运行：

```powershell
G:\PythonProject\AbaqusODBPostProcessor\scripts\run_gui.ps1
```

## 选择性扫描 ODB

扫描分为两个阶段：

1. 点击“发现并选择 ODB”。程序仅使用文件系统快速列出当前文件夹中的 `.odb`，此时不会调用 Abaqus 打开 ODB。
2. 在弹出的选择窗口中通过“全选”“全不选”“反选”或文件名筛选，勾选本次需要读取的 ODB。
3. 点击“开始读取”后，Abaqus 只打开勾选的文件。

读取进度区域显示当前文件、`N/M`、已用时间和“取消扫描”。每一条读取开始日志均带有时间戳，例如：

```text
[2026-07-22 19:03:05] 正在读取 [2/5] GJA-32_U20D_V20D.odb
```

扫描器每完成一个 ODB 都更新 `scan_cache/scan_report.json`。取消后保留已经完成的结果。

## 对比组

- 左侧为“全部 ODB”和一级对比组目录树，右侧为 ODB 全局配置表。
- 在“对比组”处右键可新建、重命名、删除组及设置图例范围。
- 从“全部 ODB”拖动 ODB 到组内即可添加成员。
- 一个 ODB 可以同时属于多个组；拖入新组不会从原组移除。
- 对比组不允许嵌套。
- 组内右键 ODB 可“从本组移除”。
- 删除组不会删除 ODB 或已有结果。
- 组关系和 ODB 配置保存在 `project_state.json`，重新扫描时恢复。

## 图例与运行

每个组、每个变量均支持：

- 自动范围：汇总本组全部启用 ODB 的全部所选帧；
- 手动范围：锁定该变量在本组中的最小值和最大值。

DAMAGET/DAMAGEC 分别计算本组上限，使用指定十级损伤颜色；其他变量使用 Abaqus 自带 `Rainbow`。

- “运行当前组”：只运行左侧当前组；
- “运行全部组”：处理所有组以及未分组的启用 ODB。

同一 ODB 属于多个组时，荷载、位移、纵筋和 FreeBody 数据只提取一次，其他组复用数值缓存，仅按本组图例重新渲染 PNG 和 GIF。

## 土体剖面

- 集合：`SET-SOIL_CUT`；
- 剖面：`Y-Plane`，XZ 平面；
- 相机：`viewVector=(0,1,0)`，`cameraUpVector=(0,0,1)`；
- View Cut：`Above=OFF`、`On=ON`、`Below=ON`、`Free Body=OFF`；
- `displaySlicing=OFF`，visible edges 为 `FREE`；
- 1600×1200、纯白背景、正交投影，仅保留图例。

## 主要输出

```text
runs/时间戳/
├─ comparison_group_legends.json
├─ groups/对比组名称/ODB名称/
└─ standalone/ODB名称/
```

每个结果包含：

- `data/timeline_alignment.csv`、`data/load_point_raw.csv`；
- `rebar/rebar_element_stress_force_timehistory.csv`；
- `freebody/pile_total_axial_force_time_aligned.csv`；
- `freebody/pile_total_force_moment_time_aligned.csv`；
- `frames/`、`contours/`、`animations/`、`plots/`；
- `summary.xlsx`、`metadata.json`。

完整的集合、帧、内力和云图规则保留在 `README_full_v3.md`。代表 ODB 的最终验收为 19/19，通过的自动化测试为 17 项。
