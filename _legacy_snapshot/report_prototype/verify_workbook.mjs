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
const summary = workbook.worksheets.getItem("Summary");
const values = summary.getRange("C6:C14").values;
const formulas = summary.getRange("C6:C14").formulas;
const sheets = [];
for (let index = 0; index < 8; index += 1) {
  sheets.push(workbook.worksheets.getItemAt(index).name);
}
console.log(JSON.stringify({ outputPath, sheets, values, formulas }));
