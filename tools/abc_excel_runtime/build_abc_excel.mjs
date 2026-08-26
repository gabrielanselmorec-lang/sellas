import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";


const [inputPath, outputPath, previewDir] = process.argv.slice(2);
if (!inputPath || !outputPath) {
  throw new Error("Uso: node build_abc_excel.mjs <entrada.json> <saida.xlsx> [pasta_previews]");
}

const payload = JSON.parse(await fs.readFile(inputPath, "utf8"));
const workbook = Workbook.create();
const summary = workbook.worksheets.add("Resumo");
const active = workbook.worksheets.add("Acontecimentos ativos");
const actions = workbook.worksheets.add("Log de ações");
const categories = workbook.worksheets.add("Categorias");

const palette = {
  dark: "#3F5D50",
  green: "#DCE8DF",
  rose: "#EEDBD7",
  blue: "#DDE6EF",
  gold: "#E9D9B5",
  surface: "#FFF9F0",
  text: "#2F2923",
  muted: "#6B6259",
  border: "#D8CBB8",
};

function styleHeader(range, fill = palette.dark) {
  range.format = {
    fill,
    font: { bold: true, color: "#FFFFFF" },
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "outside", style: "thin", color: palette.border },
  };
  range.format.rowHeight = 28;
}

function styleData(range) {
  range.format = {
    font: { color: palette.text },
    verticalAlignment: "top",
    wrapText: true,
    borders: {
      insideHorizontal: { style: "thin", color: "#E8DED0" },
      bottom: { style: "thin", color: palette.border },
    },
  };
}

function saoPauloParts(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Sao_Paulo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  return Object.fromEntries(formatter.formatToParts(date).map((part) => [part.type, part.value]));
}

function excelLocalDateTime(value) {
  const parts = saoPauloParts(value);
  if (!parts) return null;
  return new Date(Date.UTC(
    Number(parts.year),
    Number(parts.month) - 1,
    Number(parts.day),
    Number(parts.hour),
    Number(parts.minute),
    Number(parts.second),
  ));
}

function excelLocalDate(value) {
  const parts = saoPauloParts(value);
  if (!parts) return null;
  return new Date(Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day)));
}

function localTimeText(value) {
  const parts = saoPauloParts(value);
  if (!parts) return "";
  return `${parts.hour}:${parts.minute}:${parts.second}`;
}

function snapshotOf(log) {
  return log?.snapshot && typeof log.snapshot === "object" ? log.snapshot : {};
}

summary.showGridLines = false;
summary.getRange("A1:G1").merge();
summary.getRange("A1").values = [["Registro ABC fechado - Log do paciente"]];
summary.getRange("A1:G1").format = {
  fill: palette.dark,
  font: { bold: true, color: "#FFFFFF", size: 18 },
  verticalAlignment: "center",
};
summary.getRange("A1:G1").format.rowHeight = 38;

summary.getRange("A3:B7").values = [
  ["Paciente", payload.patientName ?? "Não informado"],
  ["Atualizado em", excelLocalDateTime(payload.generatedAt)],
  ["Arquivo", path.basename(outputPath)],
  ["Acontecimentos ativos", null],
  ["Ações registradas", null],
];
summary.getRange("A3:A7").format = {
  fill: palette.green,
  font: { bold: true, color: palette.text },
};
summary.getRange("A3:B7").format.borders = { preset: "outside", style: "thin", color: palette.border };
summary.getRange("B4").format.numberFormat = "yyyy-mm-dd hh:mm:ss";
summary.getRange("B6").formulas = [["=COUNTA('Acontecimentos ativos'!A2:A5001)"]];
summary.getRange("B7").formulas = [["=COUNTA('Log de ações'!A2:A5001)"]];
summary.getRange("B6:B7").format.numberFormat = "0";

summary.getRange("A9:G10").merge();
summary.getRange("A9").values = [[
  "Uso analítico e descritivo. As associações ABC não confirmam causalidade nem função comportamental e exigem interpretação profissional.",
]];
summary.getRange("A9:G10").format = {
  fill: palette.gold,
  font: { color: palette.text, italic: true },
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "outside", style: "thin", color: "#CBB681" },
};
summary.getRange("A:A").format.columnWidth = 24;
summary.getRange("B:B").format.columnWidth = 35;
summary.getRange("C:G").format.columnWidth = 13;

const activeHeaders = [[
  "ID do intervalo", "Data", "Hora", "Ambiente", "Antecedente", "Comportamento",
  "Classificação", "Função informada", "Machucou ou feriu", "Sangramento",
  "Ponto vital", "Índice de perigo", "Consequência",
]];
active.getRange("A1:M1").values = activeHeaders;
styleHeader(active.getRange("A1:M1"));
const activeRows = (payload.activeRecords ?? []).map((record) => {
  return [
    record.intervalo_id ?? "",
    excelLocalDate(record.data_hora),
    localTimeText(record.data_hora),
    record.ambiente ?? "",
    record.antecedente ?? "",
    record.comportamento ?? "",
    record.classificacao_rotulo ?? "Não classificado",
    record.funcao ?? "Não informada",
    record.causou_lesao ? "Sim" : "Não",
    record.houve_sangramento ? "Sim" : "Não",
    record.direcionado_ponto_vital ? "Sim" : "Não",
    record.indice_perigo ?? null,
    record.consequencia ?? "",
  ];
});
if (activeRows.length) {
  active.getRangeByIndexes(1, 0, activeRows.length, 13).values = activeRows;
  styleData(active.getRangeByIndexes(1, 0, activeRows.length, 13));
  active.getRange(`B2:B${activeRows.length + 1}`).format.numberFormat = "yyyy-mm-dd";
  active.getRange(`L2:L${activeRows.length + 1}`).format.numberFormat = "0%";
  const table = active.tables.add(`A1:M${activeRows.length + 1}`, true, "AcontecimentosAtivosTable");
  table.style = "TableStyleMedium4";
  active.getRange(`B2:B${activeRows.length + 1}`).format.numberFormat = "yyyy-mm-dd";
}
active.freezePanes.freezeRows(1);
active.showGridLines = false;
active.getRange("A:A").format.columnWidth = 38;
active.getRange("B:C").format.columnWidth = 14;
active.getRange("D:F").format.columnWidth = 28;
active.getRange("G:G").format.columnWidth = 20;
active.getRange("H:H").format.columnWidth = 30;
active.getRange("I:K").format.columnWidth = 20;
active.getRange("L:L").format.columnWidth = 18;
active.getRange("M:M").format.columnWidth = 28;

const actionHeaders = [[
  "Data e hora da ação", "Ação", "Data e hora do acontecimento", "ID do intervalo",
  "Ambiente", "Antecedente", "Comportamento", "Classificação", "Função informada",
  "Machucou ou feriu", "Sangramento", "Ponto vital", "Consequência", "Categoria criada",
  "Classificação anterior", "Função anterior",
]];
actions.getRange("A1:P1").values = actionHeaders;
styleHeader(actions.getRange("A1:P1"), "#6F786E");
const actionRows = (payload.actionLogs ?? []).map((log) => {
  const snapshot = snapshotOf(log);
  return [
    excelLocalDateTime(log.criado_em),
    log.acao ?? "",
    excelLocalDateTime(snapshot.data_hora),
    log.intervalo_id ?? "",
    snapshot.ambiente ?? "",
    snapshot.antecedente ?? "",
    snapshot.comportamento ?? "",
    snapshot.classificacao_rotulo ?? snapshot.classificacao ?? "",
    snapshot.funcao ?? "",
    snapshot.causou_lesao ? "Sim" : "Não",
    snapshot.houve_sangramento ? "Sim" : "Não",
    snapshot.direcionado_ponto_vital ? "Sim" : "Não",
    snapshot.consequencia ?? "",
    snapshot.nome ?? "",
    snapshot.anterior?.classificacao_rotulo ?? snapshot.anterior?.classificacao ?? "",
    snapshot.anterior?.funcao ?? "",
  ];
});
if (actionRows.length) {
  actions.getRangeByIndexes(1, 0, actionRows.length, 16).values = actionRows;
  styleData(actions.getRangeByIndexes(1, 0, actionRows.length, 16));
  actions.getRange(`A2:A${actionRows.length + 1}`).format.numberFormat = "yyyy-mm-dd hh:mm:ss";
  actions.getRange(`C2:C${actionRows.length + 1}`).format.numberFormat = "yyyy-mm-dd hh:mm:ss";
  const table = actions.tables.add(`A1:P${actionRows.length + 1}`, true, "LogAcoesTable");
  table.style = "TableStyleMedium3";
  actions.getRange(`A2:A${actionRows.length + 1}`).format.numberFormat = "yyyy-mm-dd hh:mm:ss";
  actions.getRange(`C2:C${actionRows.length + 1}`).format.numberFormat = "yyyy-mm-dd hh:mm:ss";
}
actions.freezePanes.freezeRows(1);
actions.showGridLines = false;
actions.getRange("A:A").format.columnWidth = 24;
actions.getRange("B:B").format.columnWidth = 22;
actions.getRange("C:C").format.columnWidth = 25;
actions.getRange("D:D").format.columnWidth = 38;
actions.getRange("E:G").format.columnWidth = 26;
actions.getRange("H:H").format.columnWidth = 20;
actions.getRange("I:I").format.columnWidth = 30;
actions.getRange("J:L").format.columnWidth = 18;
actions.getRange("M:P").format.columnWidth = 26;

const categoryHeaders = [["Código", "Nome", "Tipo", "Definição operacional"]];
categories.getRange("A1:D1").values = categoryHeaders;
styleHeader(categories.getRange("A1:D1"), "#687B91");
const categoryRows = (payload.categories ?? []).map((item) => [
  item.codigo ?? "",
  item.nome ?? "",
  item.tipo ?? "",
  item.definicao_operacional ?? "",
]);
if (categoryRows.length) {
  categories.getRangeByIndexes(1, 0, categoryRows.length, 4).values = categoryRows;
  styleData(categories.getRangeByIndexes(1, 0, categoryRows.length, 4));
  const table = categories.tables.add(`A1:D${categoryRows.length + 1}`, true, "CategoriasABCTable");
  table.style = "TableStyleMedium2";
}
categories.freezePanes.freezeRows(1);
categories.showGridLines = false;
categories.getRange("A:A").format.columnWidth = 28;
categories.getRange("B:C").format.columnWidth = 24;
categories.getRange("D:D").format.columnWidth = 58;

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

if (previewDir) {
  await fs.mkdir(previewDir, { recursive: true });
  for (const sheetName of ["Resumo", "Acontecimentos ativos", "Log de ações", "Categorias"]) {
    const preview = await workbook.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
    const safeName = sheetName.normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^A-Za-z0-9]+/g, "_");
    await fs.writeFile(path.join(previewDir, `${safeName}.png`), new Uint8Array(await preview.arrayBuffer()));
  }
}
