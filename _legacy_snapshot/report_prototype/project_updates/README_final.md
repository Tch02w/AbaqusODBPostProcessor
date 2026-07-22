# Abaqus ODB PostProcessor

面向 Abaqus 2025 的独立 ODB 批量后处理项目。GUI 扫描文件夹内全部 ODB，每个 ODB 一行配置 Assembly 级集合、Step 范围、帧模式、加载方向、桩型、纵筋材料/直径和对比组。

## 启动

```powershell
cd G:\PythonProject\AbaqusODBPostProcessor
.\.venv\Scripts\Activate.ps1
abaqus-odb-post
```

也可运行 `scripts\run_gui.ps1`。

## 帧模式

- `关键帧（自动）`：末帧 + 自动判定的贯穿环形损伤前一帧。判据是混凝土 DAMAGET 阈值与按深度分箱的周向覆盖率；结果写入 `frame_catalog_and_ranges.json`，允许人工复核或用“断裂前序号覆盖”修正。
- `手动选择`：输入统一时程序号，支持 `0,10,20-25`。
- `全部帧`：提取起止 Step 内所有有效帧，后续 Step 的重复 frame 0 自动跳过。

数值、钢筋、云图和 GIF 都服从帧选择。FreeBody 在自动/手动模式对显式目标帧执行；全部帧模式默认只算末帧与确认的断裂前帧，只有勾选“全部帧均做 100 个 FreeBodyCut”才执行全时程切片。

## 对比组图例

程序先调用 Abaqus 当前活动显示组的 `autoMinValue/autoMaxValue`，不再用 Python 遍历全部单元场值。每个变量在同一对比组的全部已选帧与 ODB 间汇总最小/最大值；不同组独立。DAMAGET/DAMAGEC 是例外，固定 `0–0.886` 和指定十级色阶，实际观测范围仍写入图例方案。

## 云图约定

- 纯白 RGB 背景，1600×1200 PNG，5 fps GIF。
- 正交投影，无透视；三维视向 `(1,1,0.5)`，up vector `(0,0,1)`。
- 仅显示 Free edges，不显示实体内部网格边线。
- 土体 XZ 剖面沿法向观察并显示变形；钢筋云图和轴力切片使用未变形形状。
- 输出桩体 U magnitude、S Mises，混凝土 DAMAGET/DAMAGEC，土体 PEEQ/PEMAG/S33/S Mises，以及纵筋 S Mises/S11。

## 纵筋与桩体内力

- 纵筋来源于 `SET-REBAR`，先匹配材料 `HRB400`，再筛选近全局 Z 向的 T3D2；箍筋排除。
- 根数以 ODB 连通链识别结果为准；应力逐单元读取，有符号轴力为 `S11 × πd²/4`。
- 混凝土桩用 100 个 XY FreeBodyCut；CFST 使用混凝土与钢管集合的合集。
- 钢筋轴力沿 Z 插值到 FreeBody 切面，压缩为正：`Npile=Nconcrete/pipe+Nrebar`。
- 钢筋弯矩由纵筋轴力和坐标计算：`Mx=Σ(yN)`、`My=Σ(-xN)`；再与 FreeBody 的 Mx/My 相加。全局 1 方向水平加载的控制曲线为总 `My—埋深`。
- FreeBody 报告始终 `append=OFF`。

## 主要输出

- `data/timeline_alignment.csv`、`data/load_point_raw.csv`
- `rebar/rebar_element_stress_force_timehistory.csv`
- `freebody/pile_total_axial_force_time_aligned.csv`
- `freebody/pile_total_force_moment_time_aligned.csv`
- `freebody/pile_bending_moment_maxima.csv`
- `frames/`、`contours/`、`animations/`、`plots/`
- `summary.xlsx`、`validation_report.json`

代表验证结果位于 `runs\smoke_GJA32_v2`。
