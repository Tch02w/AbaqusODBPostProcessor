import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const resultRoot = path.join(scriptDir, "output_GJA-32_U20D_V20D");
const outputDir = path.join(scriptDir, "outputs", "019f840d-1cee-7f02-bbee-64980b38c793");
const outputPath = path.join(outputDir, "GJA-32_U20D_V20D_ODB_extraction.xlsx");
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));

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

const additions = [
  ["Timeline", "data/timeline_alignment.csv"],
  ["Pile_Axial_Total", "freebody/pile_total_axial_force_time_aligned.csv"],
];
const headerStyle = {
  fill: "#0F766E",
  font: { bold: true, color: "#FFFFFF" },
  wrapText: true,
  verticalAlignment: "center",
  horizontalAlignment: "center",
  borders: { preset: "all", style: "thin", color: "#CBD5E1" },
};
for (const [sheetName, relativePath] of additions) {
  const matrix = parseCsv(await fs.readFile(path.join(resultRoot, relativePath), "utf8"));
  const sheet = workbook.worksheets.add(sheetName);
  sheet.getRangeByIndexes(0, 0, matrix.length, matrix[0].length).values = matrix;
  const lastColumn = sheetName === "Timeline" ? "V" : "O";
  const used = sheet.getRange(`A1:${lastColumn}${matrix.length}`);
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  sheet.getRange(`A1:${lastColumn}1`).format = headerStyle;
  sheet.getRange(`A1:${lastColumn}1`).format.rowHeight = 38;
  used.format.borders = { preset: "all", style: "thin", color: "#E2E8F0" };
  used.format.autofitColumns();
  used.format.autofitRows();
}
workbook.worksheets.getItem("Timeline").getRange("D2:V42").format.numberFormat = "0.000000";
workbook.worksheets.getItem("Pile_Axial_Total").getRange("F2:N101").format.numberFormat = "0.000000";

const summary = workbook.worksheets.getItem("Summary");
summary.getRange("B11").values = [["逐 T3D2 单元 S11×单筋面积；不切片"]];
summary.getRange("A17").values = [[
  "1. 纵筋保持逐单元、逐时程点计算；桩体总轴力合成时，才把同一时程点的纵筋合力沿 Z 插值到 SET-PILE_CON 的 100 个 FreeBodyCut 标高。",
]];
summary.getRange("B28").values = [["32 根纵筋在 318 个单元重心标高的合力；用于插值，不是切片"]];
summary.getRange("D23:E24").values = [
  ["Timeline", "41 个统一时程键及 SET-LOAD 原始响应"],
  ["Pile_Axial_Total", "同一时程点：混凝土＋钢筋插值＋桩体总轴力"],
];
summary.getRange("D23:E24").format.borders = { preset: "all", style: "thin", color: "#CBD5E1" };
summary.getRange("D23:D24").format.font = { bold: true, color: "#0F766E" };
summary.getRange("D23:D24").format.columnWidth = 24;
summary.getRange("E23:E24").format.columnWidth = 50;
summary.getRange("A17:H17").format.autofitRows();

const previews = {
  Summary: "A1:P52",
  Timeline: "A1:L20",
  Pile_Axial_Total: "A1:O22",
};
for (const [sheetName, range] of Object.entries(previews)) {
  const preview = await workbook.render({ sheetName, range, autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(
    path.join(outputDir, "qa", `${sheetName}.png`),
    new Uint8Array(await preview.arrayBuffer()),
  );
}

const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);
console.log(JSON.stringify({ outputPath, addedSheets: additions.map(([name]) => name) }));
