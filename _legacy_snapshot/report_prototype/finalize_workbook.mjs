import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const outputDir = path.join(scriptDir, "outputs", "019f840d-1cee-7f02-bbee-64980b38c793");
const outputPath = path.join(outputDir, "GJA-32_U20D_V20D_ODB_extraction.xlsx");
const input = await FileBlob.load(outputPath);
const workbook = await SpreadsheetFile.importXlsx(input);

const summary = workbook.worksheets.getItem("Summary");
summary.getRange("C13").formulas = [["=COUNTIF(Load_Raw!H2:H45,FALSE)"]];

const formulaCheck = await workbook.inspect({
  kind: "formula",
  sheetId: "Summary",
  range: "C6:C14",
  maxChars: 3000,
  options: { maxResults: 20 },
});
await fs.writeFile(
  path.join(outputDir, "qa", "summary_formula_check.ndjson"),
  formulaCheck.ndjson ?? JSON.stringify(formulaCheck, null, 2),
  "utf8",
);

const preview = await workbook.render({
  sheetName: "Summary",
  range: "A1:P52",
  autoCrop: "all",
  scale: 1,
  format: "png",
});
await fs.writeFile(
  path.join(outputDir, "qa", "Summary.png"),
  new Uint8Array(await preview.arrayBuffer()),
);

const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);
console.log(JSON.stringify({ outputPath, formula: "=COUNTIF(Load_Raw!H2:H45,FALSE)" }));
