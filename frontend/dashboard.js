const $ = (selector) => document.querySelector(selector);

function apiBase() {
  return ($("#apiBase")?.value || "http://127.0.0.1:8000").replace(/\/$/, "");
}

function loadConfig() {
  const raw = localStorage.getItem("mdm_frontend_config");
  if (!raw) return;
  const data = JSON.parse(raw);
  if (data.apiBase) $("#apiBase").value = data.apiBase;
}

function saveConfig() {
  const raw = localStorage.getItem("mdm_frontend_config");
  const data = raw ? JSON.parse(raw) : {};
  data.apiBase = $("#apiBase").value;
  localStorage.setItem("mdm_frontend_config", JSON.stringify(data));
}

async function requestJson(url) {
  const response = await fetch(url);
  const body = await response.json();
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return body;
}

function setText(id, value) {
  const el = $(`#${id}`);
  if (el) el.textContent = value ?? 0;
}

function formatRate(value) {
  const number = Number(value || 0);
  return number ? `${(number * 100).toFixed(1)}%` : "0";
}

function clearCanvas(canvas) {
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  return ctx;
}

function drawBarChart(id, rows) {
  const canvas = $(`#${id}`);
  const ctx = clearCanvas(canvas);
  const max = Math.max(1, ...rows.map((row) => Number(row.value || 0)));
  const colors = ["#0f766e", "#2f5aa8", "#b42318", "#7a5c00", "#475467", "#027a48"];
  const left = 132;
  const top = 24;
  const width = canvas.width - left - 34;
  const barHeight = 32;
  const gap = 15;
  ctx.font = "14px Segoe UI, Arial";
  rows.forEach((row, index) => {
    const y = top + index * (barHeight + gap);
    const value = Number(row.value || 0);
    const barWidth = Math.round((value / max) * width);
    ctx.fillStyle = "#eef2f6";
    ctx.fillRect(left, y, width, barHeight);
    ctx.fillStyle = colors[index % colors.length];
    ctx.fillRect(left, y, barWidth, barHeight);
    ctx.fillStyle = "#344054";
    ctx.fillText(String(row.label), 10, y + 21);
    ctx.fillStyle = "#17202a";
    ctx.fillText(String(value), left + Math.min(barWidth + 8, width - 36), y + 21);
  });
}

function drawPieChart(id, rows) {
  const canvas = $(`#${id}`);
  const ctx = clearCanvas(canvas);
  const total = rows.reduce((sum, row) => sum + Number(row.value || 0), 0) || 1;
  const colors = ["#0f766e", "#2f5aa8", "#b42318", "#7a5c00", "#6b7280"];
  let start = -Math.PI / 2;
  const cx = 190;
  const cy = 160;
  const radius = 104;
  rows.forEach((row, index) => {
    const value = Number(row.value || 0);
    const angle = (value / total) * Math.PI * 2;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, radius, start, start + angle);
    ctx.closePath();
    ctx.fillStyle = colors[index % colors.length];
    ctx.fill();
    start += angle;
  });
  ctx.font = "14px Segoe UI, Arial";
  rows.forEach((row, index) => {
    const y = 84 + index * 34;
    const percent = Math.round((Number(row.value || 0) / total) * 100);
    ctx.fillStyle = colors[index % colors.length];
    ctx.fillRect(360, y - 13, 16, 16);
    ctx.fillStyle = "#17202a";
    ctx.fillText(`${row.label}: ${row.value} (${percent}%)`, 386, y);
  });
}

async function refresh() {
  saveConfig();
  const metrics = await requestJson(`${apiBase()}/dashboard-metrics`);
  const q = metrics.quality || {};
  const flow = metrics.input_flow || {};
  const latest = metrics.latest_run || {};
  const evaluation = metrics.evaluation || {};

  setText("tableAInput", flow.table_a_input || 0);
  setText("tableBInput", flow.table_b_input || 0);
  setText("incomingInput", flow.incoming_input || 0);
  setText("masterOutput", flow.master_output || q.golden_records_count || 0);
  setText("f1Score", formatRate(evaluation.f1));
  setText("recallScore", formatRate(evaluation.recall));
  setText("precisionScore", formatRate(evaluation.precision));
  setText("rocAuc", formatRate(evaluation.roc_auc));

  $("#qualityNote").textContent = q.valid
    ? "质量检查通过：来源记录唯一链接，主数据一实体一行"
    : "存在质量风险：请查看覆盖、重复链接或合并效果指标";
  $("#qualityNote").className = q.valid ? "badge" : "badge bad";
  $("#runId").textContent = latest.match_run_id
    ? `match_run_id: ${latest.match_run_id} | 评估来源: ${evaluation.source || "无"}`
    : `评估来源: ${evaluation.source || "无"}`;

  drawBarChart("coverageChart", [
    { label: "Table A 输入", value: flow.table_a_input || 0 },
    { label: "Table A 覆盖", value: flow.table_a_covered || 0 },
    { label: "Table B 输入", value: flow.table_b_input || 0 },
    { label: "Table B 覆盖", value: flow.table_b_covered || 0 },
    { label: "Incoming 输入", value: flow.incoming_input || 0 },
    { label: "Incoming 覆盖", value: flow.incoming_covered || 0 },
  ]);

  drawPieChart("inputPie", [
    { label: "Table A", value: flow.table_a_input || 0 },
    { label: "Table B", value: flow.table_b_input || 0 },
    { label: "Table Incoming", value: flow.incoming_input || 0 },
  ]);

  drawBarChart("routingChart", [
    { label: "总输入", value: flow.total_input || 0 },
    { label: "候选对", value: latest.all_candidate_pairs_count || 0 },
    { label: "自动合并簇", value: latest.auto_merged_cluster_count || 0 },
    { label: "LLM 冲突簇", value: latest.llm_conflict_cluster_count || 0 },
    { label: "低置信独立", value: latest.ignored_low_confidence_cluster_count || 0 },
    { label: "主数据输出", value: flow.master_output || 0 },
  ]);

  drawBarChart("evaluationChart", [
    { label: "TP 合并对了", value: evaluation.tp || 0 },
    { label: "FP 误合并", value: evaluation.fp || 0 },
    { label: "FN 漏合并", value: evaluation.fn || 0 },
  ]);

  $("#rawMetrics").textContent = JSON.stringify(metrics, null, 2);
}

$("#refreshBtn").addEventListener("click", () => refresh().catch((error) => {
  $("#rawMetrics").textContent = `加载失败：${error.message}`;
}));
$("#apiBase").addEventListener("input", saveConfig);
loadConfig();
refresh().catch((error) => {
  $("#rawMetrics").textContent = `加载失败：${error.message}`;
});
