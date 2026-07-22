# Abaqus ODB PostProcessor

面向 Abaqus 2025 的独立 ODB 批量后处理项目。界面扫描文件夹内全部 ODB，每个 ODB 一行，可分别选择 Assembly 级集合、Step 范围、帧模式、加载方向、桩型、纵筋材料与直径，并指定对比组。

## 启动

```powershell
cd G:\PythonProject\AbaqusODBPostProcessor
.\scripts\run_gui.ps1
```

也可激活项目虚拟环境后运行 `abaqus-odb-post`。

## 集合约定

- 加载点：`SET-LOAD`，由一个参考点 RP 组成。
- 桩体显示：`SET-PILE`。
- 混凝土：在界面中选择相应 Assembly 集合。
- 钢构件/根键：普通钢筋混凝土桩优先使用 `SET-KEY`；钢管混凝土桩可选择 `SETPILESTEEL`。带后缀的根键集合代表各层根键，层数从桩顶向桩底递增。
- 土体剖面显示：`SET-SOIL_CUT`；`SET-SOIL_IN` 是置换桩体时删除的土体，不作为近场土体使用。
- 钢筋：`SET-REBAR`，再按材料 `HRB400`、T3D2 单元及接近全局 Z 方向筛选纵筋，箍筋自动排除。
- 钢管混凝土桩的内力切分使用混凝土集合与 `SETPILESTEEL` 的合集。

所有集合均只扫描 Assembly 级别，最终由用户逐个 ODB 确认。

## Step、帧与时程

- 可选择开始 Step 和结束 Step，范围包含两端；后续 Step 的重复 frame 0 自动跳过。
- `关键帧（自动）`：末帧以及程序尝试判定的混凝土贯穿环形断裂带前一帧。判定结果须由用户复核，也可输入断裂前统一时程序号覆盖。
- `手动选择`：支持 `0,10,20-25` 形式的统一时程序号。
- `全部帧`：提取所选 Step 范围内的全部有效帧。
- 荷载、位移、钢筋、FreeBody、云图和动画都使用同一统一时程序号对齐，不扣除初始沉降。
- 动画从正式加载的第一个所选帧连续生成，不按 Step 分段。

## 云图与图例

- 输出分辨率为 1600×1200，纯白背景，正交投影，无透视。
- 隐藏三轴、状态、标题和底部注释，仅保留图例。
- 显示边统一设为 `FREE`，不显示实体内部网格边。
- 位移、S Mises、S33、PEEQ、PEMAG 使用 Abaqus 自带 `Rainbow`。
- 仅新建损伤色阶 `DAMAGE_DYNAMIC_10`，颜色依次为 `#F2F2F2`、`#D9E8F5`、`#B7D4EA`、`#7BC8B8`、`#2FBF71`、`#B7DD3B`、`#F2D13D`、`#E85B2A`、`#CC1F2F`、`#FF0000`。
- DAMAGET 与 DAMAGEC 分别以 0 为下限、以当前对比组全部所选 ODB/帧的实际最大值为上限；两种损伤互不共用峰值。
- 其他变量同样按“对比组 + 变量”共享整组实际最小值和最大值；不同对比组相互独立。
- 桩体应力拆分输出：混凝土 S Mises、钢构件/根键 S Mises、纵筋 S11 和纵筋 S Mises，避免钢材峰值淹没混凝土应力细节。

## 土体剖面

土体云图严格复现以下操作：

1. `Apply Bottom View`。
2. 关闭透视，使用 `PARALLEL`。
3. Display Group 以 `SET-SOIL_CUT` 执行 `Replace`。
4. Common Options 的 visible edges 设为 `FREE`。
5. 激活 View Cut Manager 中的 `Y-Plane`，得到 XZ 剖面。

View Cut Manager 状态固定为：`Show=ON`、`Above=OFF`、`On=ON`、`Below=ON`、`Free Body Resultant=OFF`。同时将 `viewCutOptions.displaySlicing=OFF`，清除 FreeBody 100 切片留下的多切面显示，因此不会再出现密集竖线。土体采用变形后形状显示。

## 纵筋与桩体内力

- 纵筋根数以 ODB 内实际连通链识别结果为准；代表 ODB 识别到 32 根纵筋和 10176 个纵筋单元。
- 每个纵筋单元直接读取 S11，实际轴力为 `S11 × πd²/4`；无需按 100 个切片读取钢筋。
- 混凝土/钢管桩体沿 Z 轴用 100 个 XY FreeBodyCut；显示和计算均使用未变形位置，地表深度为 0，包含地表以上 500 mm 桩头。
- 钢筋轴力和弯矩沿 Z 插值到混凝土的 100 个切面后相加。
- Abaqus 原始 Fz 和纵筋 `S11 × A` 均按拉力为正；输出同时提供压缩为正的派生列，避免隐式改号。
- 钢筋弯矩按 `Mx=Σ(yN)`、`My=Σ(-xN)` 计算，再与 FreeBody 的 Mx、My 相加；U1 水平加载重点读取总 My，同时输出 Mx、My 和弯矩合量。
- FreeBody 报告始终使用 `append=OFF`。

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

## 代表 ODB 验证

代表文件：`G:\Job\GJA_ODB\GJA-32_U20D_V20D.odb`。

最终结果位于 `runs\smoke_GJA32_v6`。共 41 个统一时程点、11 类云图序列、451 张 1600×1200 PNG 和 11 个 GIF。自动判定未找到 DAMAGET 达到 0.9 的贯穿前帧，因此该 ODB 的关键帧需人工复核或手动指定。最终验证报告 17 项检查全部通过。
