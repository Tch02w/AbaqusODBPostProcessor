import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const outputDir = path.join(scriptDir, "outputs", "019f840d-1cee-7f02-bbee-64980b38c793");
const workbookPath = path.join(outputDir, "GJA-32_U20D_V20D_ODB_extraction.xlsx");
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
const preview = await workbook.render({
  sheetName: "Summary",
  range: "A1:P52",
  autoCrop: "all",
  scale: 1,
  format: "png",
});
const previewPath = path.join(outputDir, "qa", "Summary.png");
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
console.log(JSON.stringify({ previewPath }));
