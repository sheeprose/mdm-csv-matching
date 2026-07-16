const $ = (selector) => document.querySelector(selector);

const state = {
  records: [],
  lastValidation: null,
};

function apiBase() {
  return ($("#apiBase")?.value || "http://127.0.0.1:8000").replace(/\/$/, "");
}

function workflowInputs(incomingPath = "") {
  return {
    table_a_path: $("#tableAPath").value.trim(),
    table_b_path: $("#tableBPath").value.trim(),
    incoming_csv_path: incomingPath.trim(),
  };
}

function saveConfig() {
  const ids = ["apiBase", "runMode", "difyEndpoint", "difyKey", "tableAPath", "tableBPath", "threshold", "runUser"];
  const data = {};
  ids.forEach((id) => {
    const el = $(`#${id}`);
    if (el) data[id] = el.value;
  });
  data.incomingPaths = [...document.querySelectorAll(".incomingPath")].map((input) => input.value);
  localStorage.setItem("mdm_frontend_config", JSON.stringify(data));
}

function loadConfig() {
  const raw = localStorage.getItem("mdm_frontend_config");
  if (!raw) return;
  const data = JSON.parse(raw);
  Object.entries(data).forEach(([id, value]) => {
    const el = $(`#${id}`);
    if (el && typeof value === "string") el.value = value;
  });
  if (Array.isArray(data.incomingPaths) && data.incomingPaths.length) {
    $("#incomingList").innerHTML = "";
    data.incomingPaths.forEach((value, index) => addIncomingInput(index === 0 ? "Incoming / Table C CSV 路径或 URL" : "后续 CSV 路径或 URL", value));
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

function log(message, payload) {
  const box = $("#runLog");
  if (!box) return;
  const time = new Date().toLocaleTimeString();
  const detail = payload ? `\n${JSON.stringify(payload, null, 2)}` : "";
  box.textContent = `[${time}] ${message}${detail}\n\n${box.textContent}`;
}

function showNotice(type, title, text) {
  const banner = $("#statusBanner");
  banner.className = `notice ${type || ""}`.trim();
  $("#statusTitle").textContent = title;
  $("#statusText").textContent = text;
}

function hideNotice() {
  $("#statusBanner").className = "notice hidden";
}

function setBusy(busy) {
  ["validateBtn", "runBtn", "refreshBtn", "searchBtn"].forEach((id) => {
    const el = $(`#${id}`);
    if (el) el.disabled = busy;
  });
}

function updateSteps(activeStep = 1) {
  document.querySelectorAll(".step").forEach((step) => {
    const index = Number(step.dataset.step);
    step.classList.toggle("active", index === activeStep);
    step.classList.toggle("done", index < activeStep);
  });
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let body = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { raw: text };
  }
  if (!response.ok) {
    const detail = body.detail || body.message || JSON.stringify(body).slice(0, 500);
    throw new Error(`${response.status} ${response.statusText}: ${detail}`);
  }
  return body;
}

function addIncomingInput(label = "后续 CSV 路径或 URL", value = "") {
  const wrapper = document.createElement("label");
  wrapper.innerHTML = `${label}<input class="incomingPath" placeholder="Table C / Table D / 新增 CSV" value="${escapeHtml(value)}">`;
  $("#incomingList").appendChild(wrapper);
}

async function validateBeforeRun(incomingPath = "") {
  const validation = await requestJson(`${apiBase()}/validate-upload`, {
    method: "POST",
    body: JSON.stringify({
      ...workflowInputs(incomingPath),
      threshold: Number($("#threshold").value || 0.59),
    }),
  });
  state.lastValidation = validation;
  if (!validation.valid) {
    const message = validation.message || (validation.errors || []).join("；") || "上传校验失败，无法运行。";
    const detail = (validation.errors || []).filter((item) => item !== message).join("；");
    showNotice("bad", "无法运行", message);
    throw new Error(detail ? `${message}。${detail}` : message);
  }
  showNotice("ok", "预检通过", "数据表类型兼容，可以进行主数据匹配。");
  return validation;
}

async function validateOnly() {
  saveConfig();
  setBusy(true);
  try {
    updateSteps(3);
    const incomingPaths = [...document.querySelectorAll(".incomingPath")].map((input) => input.value.trim()).filter(Boolean);
    const jobs = incomingPaths.length ? incomingPaths : [""];
    for (const incomingPath of jobs) {
      const validation = await validateBeforeRun(incomingPath);
      log("上传预检通过", {
        database_has_data: validation.database_has_data,
        schema_status: validation.schema_profile?.relation_type || "standard",
      });
    }
  } catch (error) {
    log(`预检失败：${error.message}`);
  } finally {
    setBusy(false);
  }
}

async function runDifyWorkflow(incomingPath = "") {
  const endpoint = $("#difyEndpoint").value.trim();
  const key = $("#difyKey").value.trim();
  if (!endpoint || !key) throw new Error("Dify Endpoint 和 API Key 都需要填写。");
  return requestJson(`${apiBase()}/run-dify-workflow`, {
    method: "POST",
    body: JSON.stringify({
      dify_endpoint: endpoint,
      dify_api_key: key,
      inputs: workflowInputs(incomingPath),
      response_mode: "blocking",
      user: $("#runUser").value.trim() || "mdm-web-user",
    }),
  });
}

async function runLocalWorkflow(incomingPath = "") {
  const inputs = workflowInputs(incomingPath);
  const ingest = await requestJson(`${apiBase()}/ingest-and-match`, {
    method: "POST",
    body: JSON.stringify({
      ...inputs,
      threshold: Number($("#threshold").value || 0.59),
    }),
  });
  if (ingest.match_run_id || ingest.status === "completed") {
    const finalQuality = ingest.match_run_id
      ? await requestJson(`${apiBase()}/finalize-match-run`, {
        method: "POST",
        body: JSON.stringify({
          match_run_id: ingest.match_run_id,
          table_a_path: inputs.table_a_path,
          table_b_path: inputs.table_b_path,
          incoming_csv_path: inputs.incoming_csv_path,
          fallback_remaining: true,
          merge_duplicates: true,
        }),
      })
      : null;
    return { ingest_result: ingest, final_quality_result: finalQuality };
  }
  return { ingest_result: ingest };
}

async function runAll() {
  saveConfig();
  setBusy(true);
  try {
    hideNotice();
    updateSteps(3);
    const mode = $("#runMode").value;
    const incomingPaths = [...document.querySelectorAll(".incomingPath")].map((input) => input.value.trim()).filter(Boolean);
    const jobs = incomingPaths.length ? incomingPaths : [""];
    for (const incomingPath of jobs) {
      const label = incomingPath ? `处理新增表：${incomingPath}` : "处理 Table A + Table B 初始化";
      log(label);
      const validation = await validateBeforeRun(incomingPath);
      log("上传预检通过", {
        database_has_data: validation.database_has_data,
        schema_status: validation.schema_profile?.relation_type || "standard",
      });
      const result = mode === "dify" ? await runDifyWorkflow(incomingPath) : await runLocalWorkflow(incomingPath);
      log("流程返回", result);
    }
    await refreshAll();
    updateSteps(4);
    showNotice("ok", "运行完成", "主数据已刷新，可以进行预览、检索或下载。");
  } catch (error) {
    log(`运行失败：${error.message}`);
  } finally {
    setBusy(false);
  }
}

async function checkHealth() {
  const badge = $("#healthBadge");
  if (!badge) return;
  try {
    const health = await requestJson(`${apiBase()}/health`, { headers: {} });
    badge.textContent = health.ok ? "API 已连接" : "API 异常";
    badge.className = health.ok ? "badge" : "badge bad";
  } catch {
    badge.textContent = "API 未连接";
    badge.className = "badge bad";
  }
}

async function refreshRecords() {
  const data = await requestJson(`${apiBase()}/golden-records?limit=100&offset=0&order=asc`, { headers: {} });
  state.records = data.items || [];
  $("#resultCount").textContent = `${data.total || 0} 条`;
  $("#recordBadge").textContent = `主数据 ${data.total || 0} 条`;
  $("#previewMeta").textContent = `共 ${data.total || 0} 条，显示前 ${state.records.length} 条`;
  $("#downloadLink").href = `${apiBase()}/golden-records.csv`;
  renderTable(state.records);
}

async function refreshAll() {
  saveConfig();
  $("#runModeBadge").textContent = $("#runMode").value === "dify" ? "Dify 工作流" : "本地 API 调试";
  await checkHealth();
  try {
    await refreshRecords();
  } catch (error) {
    log(`刷新主数据失败：${error.message}`);
  }
}

function renderTable(records) {
  const body = $("#recordsBody");
  body.innerHTML = records.map((item) => `
    <tr>
      <td>${escapeHtml(item.id)}</td>
      <td>${escapeHtml(item.entity_id)}</td>
      <td>
        <strong>${escapeHtml(item.title || "")}</strong>
        <div class="muted">${escapeHtml((item.aliases || []).slice(0, 3).join(" / "))}</div>
      </td>
      <td>${escapeHtml(item.manufacturer || "")}</td>
      <td>${item.price ?? ""}</td>
      <td><span class="source-pill">${escapeHtml(item.selected_source_table || item.source || "")}</span></td>
      <td>${Number(item.confidence || 0).toFixed(3)}</td>
    </tr>
  `).join("") || `<tr><td colspan="7" class="muted">暂无主数据。先运行 Table A + Table B 初始化，或检查后端数据库。</td></tr>`;
}

async function search() {
  saveConfig();
  const query = $("#searchInput").value.trim();
  if (!query) return;
  setBusy(true);
  try {
    const data = await requestJson(`${apiBase()}/search?q=${encodeURIComponent(query)}&limit=10`, { headers: {} });
    renderSearchResults(data.items || []);
  } catch (error) {
    $("#searchResults").innerHTML = `<div class="result-card">检索失败：${escapeHtml(error.message)}</div>`;
  } finally {
    setBusy(false);
  }
}

function renderSearchResults(items) {
  $("#searchResults").innerHTML = items.map((item) => `
    <article class="result-card">
      <strong>${escapeHtml(item.title || "")}</strong>
      <div class="result-meta">
        <span>推荐分 ${Number(item.match_score || 0).toFixed(3)}</span>
        <span>命中 ${escapeHtml(matchRuleLabel(item.match_rule))}</span>
        <span>置信度 ${Number(item.confidence || 0).toFixed(3)}</span>
        <span>实体 ${escapeHtml(item.entity_id)}</span>
        <span>${escapeHtml(item.manufacturer || "未知厂商")}</span>
      </div>
      <p class="muted">${escapeHtml((item.aliases || []).slice(0, 6).join(" / "))}</p>
    </article>
  `).join("") || `<div class="result-card muted">没有找到候选。可以换一个更短的商品词、品牌词或 SKU。</div>`;
}

function matchRuleLabel(rule) {
  return {
    exact: "完全匹配",
    prefix: "开头匹配",
    contains: "包含匹配",
    initial_prefix: "首字母开头",
    initial_contains: "首字母包含",
    character_contains: "字符包含",
  }[rule] || "规则匹配";
}

function bindEvents() {
  $("#addIncoming")?.addEventListener("click", () => {
    addIncomingInput();
    saveConfig();
  });
  $("#validateBtn")?.addEventListener("click", validateOnly);
  $("#runBtn")?.addEventListener("click", runAll);
  $("#refreshBtn")?.addEventListener("click", refreshAll);
  $("#searchBtn")?.addEventListener("click", search);
  $("#searchInput")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") search();
  });
  document.addEventListener("input", (event) => {
    if (event.target.matches("input, select")) {
      saveConfig();
      $("#runModeBadge").textContent = $("#runMode").value === "dify" ? "Dify 工作流" : "本地 API 调试";
    }
  });
}

loadConfig();
bindEvents();
updateSteps(1);
refreshAll();
