import fs from "node:fs/promises";
import process from "node:process";
import { dirname } from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [inputPath, outputPath] = process.argv.slice(2);
const rows = JSON.parse(await fs.readFile(inputPath, "utf8"));
const headers = ["视觉类型", "素材排名", "素材 ID", "素材标题", "画面类型", "高光帧图片", "秒数", "点击峰值排名", "点击指数"];
const values = rows.map((row) => headers.map((header) => header === "高光帧图片" ? null : row[header] ?? null));
const workbook = Workbook.create();
const sheet = workbook.worksheets.add("高光帧资产");
sheet.showGridLines = false;
sheet.getRangeByIndexes(0, 0, 1, headers.length).values = [headers];
if (values.length) sheet.getRangeByIndexes(1, 0, values.length, headers.length).values = values;
const endRow = Math.max(1, values.length + 1);
sheet.getRange(`A1:I${endRow}`).format.wrapText = true;
sheet.getRange("A1:I1").format = { fill: "#2c1810", font: { bold: true, color: "#ffffff" }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true };
sheet.getRange("A1:I1").format.rowHeight = 30;
sheet.getRange("A:A").format.columnWidth = 14;
sheet.getRange("B:C").format.columnWidth = 16;
sheet.getRange("D:D").format.columnWidth = 42;
sheet.getRange("E:F").format.columnWidth = 18;
sheet.getRange("G:I").format.columnWidth = 12;
sheet.freezePanes.freezeRows(1);
sheet.tables.add(`A1:I${endRow}`, true, "HighlightFramesTable").showFilterButton = true;
for (let index = 0; index < rows.length; index += 1) {
  const imagePath = rows[index].__image_path;
  if (!imagePath) continue;
  try {
    const bytes = await fs.readFile(imagePath);
    const dataUrl = `data:image/jpeg;base64,${Buffer.from(bytes).toString("base64")}`;
    sheet.images.add({ dataUrl, anchor: { from: { row: index + 1, col: 5 }, extent: { widthPx: 92, heightPx: 112 } } });
  } catch {}
}
await fs.mkdir(dirname(outputPath), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
