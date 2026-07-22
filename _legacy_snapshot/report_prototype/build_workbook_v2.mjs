import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const resultRoot = path.join(scriptDir, "output_GJA-32_U20D_V20D");
const outputDir = path.join(scriptDir, "outputs", "019f840d-1cee-7f02-bbee-64980b38c793");
const qaDir = path.join(outputDir, "qa");
await fs.mkdir(qaDir, { recursive: true });

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (quoted) {
      if (character === '"' && text[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (character === '"') {
        quoted = false;
      } else {
        field += character;
      }
    } else if (character === '"') {
      quoted = true;
    } else if (character === ",") {
      row.push(field);
      field = "";
    } else if (character === "\n") {
      row.push(field.replace(/\r$/, ""));
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += character;
    }
  }
  if (field.length || row.length) {
    row.push(field.replace(/\r$/, ""));
    rows.push(row);
  }
  rows[0][0] = rows[0][0].replace(/^\uFEFF/, "");
  return rows.map((values, rowIndex) => values.map((value) => {
    if (rowIndex === 0 || value === "") return value;
    if (value === "True") return true;
    if (value === "False") return false;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : value;
  }));
}

function columnName(index) {
  let value = index + 1;
  let name = "";
  while (value > 0) {
    value -= 1;
    name = String.fromCharCode(65 + (value % 26)) + name;
    value = Math.floor(value / 26);
  }
  return name;
}

const workbook = Workbook.create();
const summary = workbook.worksheets.add("Summary");
const csvSheets = [
  ["Load_Raw", "data/load_point_raw.csv"],
  ["Load_Dir1", "data/load_displacement_dir1.csv"],
  ["Load_Dir3", "data/load_displacement_dir3.csv"],
  ["Damage_Scan", "data/damage_ring_scan.csv"],
  ["Concrete_Axial", "freebody/axial_force_depth_LAST.csv"],
  ["Rebar_Force", "rebar/rebar_actual_force_depth_LAST.csv"],
  ["Rebar_Bars", "rebar/rebar_bar_actual_summary_last_frame.csv"],
];
const dimensions = new Map();
for (const [sheetName, relativePath] of csvSheets) {
  const matrix = parseCsv(await fs.readFile(path.join(resultRoot, relativePath), "utf8"));
  const sheet = workbook.worksheets.add(sheetName);
  sheet.getRangeByIndexes(0, 0, matrix.length, matrix[0].length).values = matrix;
  dimensions.set(sheetName, { rows: matrix.length, cols: matrix[0].length });
}

const headerStyle = {
  fill: "#0F766E",
  font: { bold: true, color: "#FFFFFF" },
  wrapText: true,
  verticalAlignment: "center",
  horizontalAlignment: "center",
  borders: { preset: "all", style: "thin", color: "#CBD5E1" },
};
for (const [sheetName] of csvSheets) {
  const sheet = workbook.worksheets.getItem(sheetName);
  const { rows, cols } = dimensions.get(sheetName);
  const lastColumn = columnName(cols - 1);
  const used = sheet.getRange(`A1:${lastColumn}${rows}`);
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  sheet.getRange(`A1:${lastColumn}1`).format = headerStyle;
  sheet.getRange(`A1:${lastColumn}1`).format.rowHeight = 38;
  used.format.borders = { preset: "all", style: "thin", color: "#E2E8F0" };
  used.format.autofitColumns();
  used.format.autofitRows();
}

for (const name of ["Load_Raw", "Load_Dir1", "Load_Dir3"]) {
  const sheet = workbook.worksheets.getItem(name);
  const rows = dimensions.get(name).rows;
  sheet.getRange(`C2:G${rows}`).format.numberFormat = "0.000000";
  sheet.getRange(`J2:U${rows}`).format.numberFormat = "0.000000";
}
workbook.worksheets.getItem("Damage_Scan").getRange("F2:M42").format.numberFormat = "0.000000";
workbook.worksheets.getItem("Concrete_Axial").getRange("C2:O101").format.numberFormat = "0.000";
workbook.worksheets.getItem("Rebar_Force").getRange("F2:U319").format.numberFormat = "0.000000";
workbook.worksheets.getItem("Rebar_Bars").getRange("B2:O33").format.numberFormat = "0.000000";

summary.showGridLines = false;
summary.freezePanes.freezeRows(3);
summary.getRange("A1:H1").merge();
summary.getRange("A1").values = [["GJA-32_U20D_V20D — Abaqus ODB 提取汇总"]];
summary.getRange("A1:H1").format = {
  fill: "#134E4A",
  font: { bold: true, color: "#FFFFFF", fontSize: 18 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
summary.getRange("A1:H1").format.rowHeight = 34;
summary.getRange("A3:C3").values = [["项目", "采用值", "工作簿核对"]];
summary.getRange("A3:C3").format = headerStyle;
summary.getRange("A4:B14").values = [
  ["ODB 文件", "G:\\Job\\GJA_ODB\\GJA-32_U20D_V20D.odb"],
  ["加载点集合", "SET-LOAD（1 个 RP）"],
  ["纵向主筋根数", "32（ODB 连通链识别）"],
  ["目录表根数", "30（仅参考，不参与折算）"],
  ["主筋直径", "32 mm"],
  ["单筋面积", "804.247719 mm²"],
  ["钢筋云图", "S, Mises；未变形"],
  ["钢筋轴力", "Σ(S11 × 单筋面积)；保留符号"],
  ["混凝土轴力切片", "100 个 XY 平面；末帧"],
  ["连续加载帧数", "41（U10D 至 V20D，边界帧去重）"],
  ["动画数量", "10 个 GIF；5 fps"],
];
summary.getRange("C6").formulas = [["=Rebar_Force!J2"]];
summary.getRange("C12").formulas = [["=COUNTA(Concrete_Axial!A2:A101)"]];
summary.getRange("C13").formulas = [["=COUNTA(Load_Raw!A2:A42)"]];
summary.getRange("C14").values = [[10]];
summary.getRange("A3:C14").format.borders = { preset: "all", style: "thin", color: "#CBD5E1" };
summary.getRange("A4:A14").format.font = { bold: true, color: "#134E4A" };

summary.getRange("A16:H16").merge();
summary.getRange("A16").values = [["判定与限制"]];
summary.getRange("A16:H16").format = { fill: "#D97706", font: { bold: true, color: "#FFFFFF" } };
const notes = [
  "1. SET-PILE 含嵌入式 T3D2，FreeBodyCut 不计其贡献；混凝土按 SET-PILE_CON 提取，纵筋轴力另由 S11×面积计算。",
  "2. DAMAGET 判据阈值 0.90、角向覆盖率 90%；本 ODB 最大值约 0.886，未自动识别到断裂前帧。",
  "3. 当前云图图例为逐帧自动范围；对照组统一上下限待用户分组后再应用。",
  "4. 逐单元钢筋原始数据约 41.7 万行，保存在 CSV 中，未复制进本工作簿。",
];
for (let index = 0; index < notes.length; index += 1) {
  const row = 17 + index;
  summary.getRange(`A${row}:H${row}`).merge();
  summary.getRange(`A${row}`).values = [[notes[index]]];
  summary.getRange(`A${row}:H${row}`).format = {
    fill: "#FFF7ED",
    font: { color: "#7C2D12" },
    wrapText: true,
    borders: { preset: "all", style: "thin", color: "#FED7AA" },
  };
}

summary.getRange("A22:H22").merge();
summary.getRange("A22").values = [["工作表说明"]];
summary.getRange("A22:H22").format = headerStyle;
summary.getRange("A23:B29").values = [
  ["Load_Raw", "SET-LOAD 原始 U/RF/UR/RM，未扣初始沉降"],
  ["Load_Dir1", "1 方向加载阶段 RF1-U1"],
  ["Load_Dir3", "3 方向加载阶段 RF3-U3"],
  ["Damage_Scan", "环形损伤自动判定过程"],
  ["Concrete_Axial", "SET-PILE_CON 末帧 100 切片轴力—埋深"],
  ["Rebar_Force", "ODB 识别 32 根纵筋末帧轴力—埋深"],
  ["Rebar_Bars", "32 根纵筋逐根末帧应力与实际轴力"],
];
summary.getRange("A23:B29").format.borders = { preset: "all", style: "thin", color: "#CBD5E1" };
summary.getRange("A23:A29").format.font = { bold: true, color: "#0F766E" };
summary.getRange("A1:H29").format.autofitRows();
summary.getRange("A1:A29").format.columnWidth = 24;
summary.getRange("B1:B29").format.columnWidth = 62;
summary.getRange("C1:C29").format.columnWidth = 18;
summary.getRange("D1:H29").format.columnWidth = 12;

for (const [relativePath, row, col] of [
  ["plots/load_displacement_dir3.png", 30, 0],
  ["plots/rebar_actual_force_depth_LAST.png", 30, 8],
]) {
  const bytes = await fs.readFile(path.join(resultRoot, relativePath));
  summary.images.add({
    dataUrl: `data:image/png;base64,${bytes.toString("base64")}`,
    anchor: { from: { row, col }, extent: { widthPx: 520, heightPx: 360 } },
  });
}

const inspection = await workbook.inspect({
  kind: "workbook,sheet,formula",
  maxChars: 12000,
  tableMaxRows: 8,
  tableMaxCols: 10,
});
await fs.writeFile(path.join(qaDir, "workbook_inspection.ndjson"), inspection.ndjson ?? JSON.stringify(inspection, null, 2));

const previewRanges = {
  Summary: "A1:P52",
  Load_Raw: "A1:K18",
  Load_Dir1: "A1:K18",
  Load_Dir3: "A1:K18",
  Damage_Scan: "A1:M20",
  Concrete_Axial: "A1:H22",
  Rebar_Force: "A1:L22",
  Rebar_Bars: "A1:O22",
};
for (const [sheetName, range] of Object.entries(previewRanges)) {
  const preview = await workbook.render({ sheetName, range, autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(path.join(qaDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
}

const outputPath = path.join(outputDir, "GJA-32_U20D_V20D_ODB_extraction.xlsx");
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);
console.log(JSON.stringify({ outputPath, sheets: ["Summary", ...csvSheets.map(([name]) => name)], previews: 8 }));
