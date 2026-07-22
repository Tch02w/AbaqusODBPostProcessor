import path from "node:path";
import { fileURLToPath } from "node:url";

import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const outputPath = path.join(
  scriptDir,
  "outputs",
  "019f840d-1cee-7f02-bbee-64980b38c793",
  "GJA-32_U20D_V20D_ODB_extraction.xlsx",
);
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
workbook.worksheets.getItem("Summary").getRange("C13").formulas = [
  ["=COUNTA(Load_Raw!A2:A45)-3"],
];
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);
console.log(JSON.stringify({ outputPath, formula: "=COUNTA(Load_Raw!A2:A45)-3" }));
