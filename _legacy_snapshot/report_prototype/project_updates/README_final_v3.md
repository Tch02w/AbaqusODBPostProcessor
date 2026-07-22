# Abaqus ODB PostProcessor

面向 Abaqus 2025 的独立 ODB 批量后处理工具。项目包含自己的 Python 虚拟环境，可扫描文件夹中的全部 ODB，配置 Assembly 级集合、Step、帧、加载方向和钢筋参数，并按多个对比组生成统一图例的云图、GIF、曲线、CSV 和 Excel。

## 启动

```powershell
cd G:\PythonProject\AbaqusODBPostProcessor
.\scripts\run_gui.ps1
```

如果 PowerShell 禁止执行脚本，可运行：

```powershell
powershell -ExecutionPolicy Bypass -File G:\PythonProject\AbaqusODBPostProcessor\scripts\run_gui.ps1
```

## 扫描进度

点击“扫描 ODB”后，界面明确显示：

- 已发现的 ODB 总数；
- 当前读取的 ODB 文件名；
- `已完成数量/总数量`；
- 扫描已用时间；
- “取消扫描”按钮。

扫描器每完成一个 ODB 就更新一次 `scan_cache/scan_report.json`。取消扫描时会终止 Abaqus 扫描进程，并保留已经完成的 ODB，不会清空已有结果。

## 对比组

界面采用资源管理器式布局：左侧为“全部 ODB”和一级“对比组”目录树，右侧为 ODB 全局配置表。

- 在“对比组”节点或空白处右键可新建对比组。
- 对比组支持重命名、删除和图例范围设置，不允许嵌套子组。
- 从“全部 ODB”中拖动 ODB 到对比组即添加成员。
- 同一个 ODB 可以同时属于多个对比组；拖入新组不会从原组移除。
- 同一组内不会重复添加相同 ODB。
- 在组内右键 ODB，选择“从本组移除”可删除该成员关系。
- 删除组只删除界面关系，不删除 ODB 和已经生成的结果。
- 点击某个组，右侧表格只显示该组成员；点击“全部 ODB”恢复全部行。

ODB 的 Step、帧模式、集合映射、加载方向、桩型和纵筋参数是全局配置，在多个组中保持一致。组只控制成员关系、图例范围和组输出。

组关系及 ODB 配置自动保存到 `project_state.json`，重新扫描同一文件夹时自动恢复。缺失的 ODB 配置不会被自动删除。

## 对比组图例

每个对比组、每个变量均可选择：

- 自动：汇总本组全部启用 ODB 的全部所选帧，使用整组实际最小值和最大值；
- 手动：锁定该变量在本组中的最小值和最大值。

DAMAGET 和 DAMAGEC 在自动模式下分别以 0 为下限，分别使用本组实际最大值为上限。损伤变量采用指定十级颜色；其他变量采用 Abaqus 自带 `Rainbow`。

未加入任何组的 ODB 仍可运行，并自动作为只包含自身的独立结果，其图例只取自身范围。取消 ODB 的“启用”后，该 ODB 暂时退出所有组的图例和输出计算，但组成员关系仍保留。

## 运行范围和缓存

- “运行当前组”：只运行左侧当前选择的对比组；选择“全部 ODB”时，对勾选 ODB 分别生成独立结果。
- “运行全部组”：运行全部对比组，并处理未分组但已启用的 ODB。

同一个 ODB 属于多个对比组时，荷载、位移、纵筋和 FreeBody 数据只提取一次。第一组完成基础数据提取，其他组复用数值缓存，只根据各组图例重新渲染 PNG 和 GIF。

输出目录结构为：

```text
runs/时间戳/
├─ comparison_group_legends.json
├─ groups/
│  ├─ 对比组A/ODB名称/
│  └─ 对比组B/ODB名称/
└─ standalone/ODB名称/
```

## 集合约定

- 加载点：`SET-LOAD`，由一个参考点 RP 组成。
- 桩体显示：`SET-PILE`。
- 混凝土：在界面中选择相应 Assembly 集合。
- 钢构件/根键：普通钢筋混凝土桩优先使用 `SET-KEY`；钢管混凝土桩可选择 `SETPILESTEEL`。
- 土体剖面显示：`SET-SOIL_CUT`。`SET-SOIL_IN` 是桩体置换时删除的土体，不作为近场土体。
- 钢筋：`SET-REBAR`，再按 `HRB400`、T3D2 和接近全局 Z 方向筛选纵筋，排除箍筋。

所有集合只扫描 Assembly 级别，最终由用户逐个 ODB 确认。

## Step 和帧

- 开始 Step 与结束 Step 均包含在提取范围内。
- 后续 Step 重复的 frame 0 自动跳过。
- “关键帧（自动）”：末帧及程序尝试判定的贯穿断裂前一帧。
- “手动选择”：支持 `0,10,20-25` 形式的统一时程序号。
- “全部帧”：提取 Step 范围内的全部有效帧。
- 所有数据、云图、GIF 和 FreeBody 使用同一统一时程序号对齐，不扣除初始沉降。

## 云图显示

- 1600×1200 PNG、纯白背景、正交投影、无透视。
- 隐藏三轴、状态、标题和底部注释，仅保留图例。
- visible edges 使用 `FREE`，不显示实体内部网格边。
- 桩体视角使用 `(1,1,0.5)`，up vector 为 `(0,0,1)`。
- 土体使用 `SET-SOIL_CUT`、`Y-Plane` XZ 剖面和变形后形状。
- 土体相机位于 −Y 侧，显式使用 `viewVector=(0,1,0)`、`cameraUpVector=(0,0,1)`，可看到中央桩体、根键和桩周区域。
- View Cut Manager：`Above=OFF`、`On=ON`、`Below=ON`、`Free Body=OFF`。
- `displaySlicing=OFF`，避免 FreeBody 100 切片遗留的密集竖线。

## 纵筋和桩体内力

- 纵筋根数以 ODB 实际连通链识别为准。
- 每个纵筋单元读取 S11，轴力为 `S11 × πd²/4`。
- 桩体沿 Z 轴使用 100 个 XY FreeBodyCut，包含地表以上 500 mm 桩头。
- 钢筋轴力和弯矩沿 Z 插值到混凝土切面后相加。
- 输出拉力为正的原始值及压缩为正的派生列。
- U1 水平加载重点读取总 My，同时输出 Mx、My 和弯矩合量。
- FreeBody 始终使用 `append=OFF`。

## 主要输出

- `data/timeline_alignment.csv`
- `data/load_point_raw.csv`
- `rebar/rebar_element_stress_force_timehistory.csv`
- `freebody/pile_total_axial_force_time_aligned.csv`
- `freebody/pile_total_force_moment_time_aligned.csv`
- `freebody/pile_bending_moment_maxima.csv`
- `frames/`、`contours/`、`animations/`、`plots/`
- `summary.xlsx`
- `metadata.json`、`validation_report.json`

代表 ODB `G:\Job\GJA_ODB\GJA-32_U20D_V20D.odb` 的最终结果位于 `runs\smoke_GJA32_v6`，最终验收 19 项全部通过。
