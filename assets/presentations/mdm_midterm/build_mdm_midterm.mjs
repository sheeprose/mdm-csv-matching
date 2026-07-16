import fs from "node:fs/promises";
import path from "node:path";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const ROOT = "C:/Users/86153/Desktop/mdm_data";
const OUT = path.join(ROOT, "主数据管理_中期汇报_重做版.pptx");
const QA = path.join(ROOT, "work/presentations/mdm_midterm/qa");
const MEDIA = path.join(ROOT, "work/presentations/mdm_midterm/assets/ppt/media");

const W = 1280;
const H = 720;
const C = {
  navy: "#0B1F33",
  blue: "#1456A0",
  cyan: "#06A7C7",
  teal: "#0E8F86",
  green: "#1F9D55",
  amber: "#F59E0B",
  red: "#DC2626",
  ink: "#102033",
  muted: "#5E7188",
  pale: "#EEF6F9",
  line: "#C9DAE4",
  white: "#FFFFFF",
  softBlue: "#DCECF5",
};

async function readImage(name) {
  const full = path.join(MEDIA, name);
  const bytes = await fs.readFile(full);
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
}

async function writeBlob(filePath, blob) {
  await fs.writeFile(filePath, new Uint8Array(await blob.arrayBuffer()));
}

function addText(slide, text, x, y, w, h, style = {}) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    position: { left: x, top: y, width: w, height: h },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  shape.text = text;
  shape.text.style = {
    fontSize: style.fontSize ?? 20,
    bold: style.bold ?? false,
    color: style.color ?? C.ink,
    alignment: style.alignment ?? "left",
  };
  return shape;
}

function addBox(slide, x, y, w, h, fill = C.white, line = C.line, radius = "rounded-lg") {
  return slide.shapes.add({
    geometry: "roundRect",
    position: { left: x, top: y, width: w, height: h },
    fill,
    line: { style: "solid", fill: line, width: 1 },
    borderRadius: radius,
  });
}

function addRule(slide, x, y, w, color = C.cyan) {
  slide.shapes.add({
    geometry: "rect",
    position: { left: x, top: y, width: w, height: 4 },
    fill: color,
    line: { style: "solid", fill: color, width: 0 },
  });
}

function title(slide, kicker, main, sub = "") {
  addText(slide, kicker, 64, 38, 520, 28, { fontSize: 15, bold: true, color: C.cyan });
  addText(slide, main, 64, 72, 860, 58, { fontSize: 36, bold: true, color: C.ink });
  addRule(slide, 64, 142, 96);
  if (sub) addText(slide, sub, 64, 160, 900, 44, { fontSize: 18, color: C.muted });
}

function footer(slide, page) {
  addText(slide, "基于 Dify 的客户/商品主数据自动化匹配项目", 64, 674, 680, 24, {
    fontSize: 13,
    color: "#71859A",
  });
  addText(slide, String(page).padStart(2, "0"), 1160, 672, 56, 28, {
    fontSize: 15,
    bold: true,
    color: C.blue,
    alignment: "right",
  });
}

function bulletList(slide, items, x, y, w, gap = 48, color = C.ink) {
  items.forEach((item, i) => {
    const top = y + i * gap;
    slide.shapes.add({
      geometry: "ellipse",
      position: { left: x, top: top + 8, width: 11, height: 11 },
      fill: item.color ?? C.cyan,
      line: { style: "solid", fill: item.color ?? C.cyan, width: 0 },
    });
    addText(slide, item.text, x + 24, top, w - 24, 38, {
      fontSize: item.size ?? 19,
      bold: item.bold ?? false,
      color,
    });
  });
}

function metric(slide, label, value, note, x, y, w, h, accent = C.cyan) {
  addBox(slide, x, y, w, h, C.white, "#D9E7EE", "rounded-xl");
  slide.shapes.add({
    geometry: "rect",
    position: { left: x, top: y, width: 6, height: h },
    fill: accent,
    line: { style: "solid", fill: accent, width: 0 },
  });
  addText(slide, value, x + 24, y + 18, w - 48, 52, { fontSize: 34, bold: true, color: C.ink });
  addText(slide, label, x + 24, y + 78, w - 48, 28, { fontSize: 17, bold: true, color: C.blue });
  addText(slide, note, x + 24, y + 112, w - 48, 42, { fontSize: 15, color: C.muted });
}

function smallTag(slide, text, x, y, w, fill = C.softBlue, color = C.blue) {
  addBox(slide, x, y, w, 34, fill, fill, "rounded-lg");
  addText(slide, text, x + 12, y + 7, w - 24, 20, { fontSize: 14, bold: true, color, alignment: "center" });
}

function step(slide, n, heading, body, x, y, w, color = C.blue) {
  addBox(slide, x, y, w, 118, C.white, "#D8E6ED", "rounded-xl");
  slide.shapes.add({
    geometry: "ellipse",
    position: { left: x + 22, top: y + 22, width: 38, height: 38 },
    fill: color,
    line: { style: "solid", fill: color, width: 0 },
  });
  addText(slide, n, x + 22, y + 29, 38, 22, { fontSize: 16, bold: true, color: C.white, alignment: "center" });
  addText(slide, heading, x + 74, y + 18, w - 96, 28, { fontSize: 18, bold: true, color: C.ink });
  addText(slide, body, x + 74, y + 52, w - 96, 48, { fontSize: 14, color: C.muted });
}

async function addImage(slide, name, x, y, w, h, fit = "cover") {
  const blob = await readImage(name);
  const ext = name.split(".").pop().toLowerCase();
  const contentType = ext === "jpg" || ext === "jpeg" ? "image/jpeg" : "image/png";
  slide.images.add({
    blob,
    contentType,
    alt: name,
    fit,
    position: { left: x, top: y, width: w, height: h },
    geometry: "roundRect",
    borderRadius: "rounded-xl",
  });
}

function addBar(slide, label, value, max, x, y, w, color) {
  addText(slide, label, x, y, 210, 28, { fontSize: 16, bold: true, color: C.ink });
  addBox(slide, x + 220, y + 5, w, 18, "#E5EEF3", "#E5EEF3", "rounded-lg");
  const bw = Math.max(4, (value / max) * w);
  addBox(slide, x + 220, y + 5, bw, 18, color, color, "rounded-lg");
  addText(slide, String(value), x + 230 + w, y, 80, 28, { fontSize: 16, bold: true, color });
}

async function main() {
  await fs.mkdir(QA, { recursive: true });
  const deck = Presentation.create({ slideSize: { width: W, height: H } });

  // 1
  {
    const s = deck.slides.add();
    await addImage(s, "image1.jpeg", 0, 0, W, H, "cover");
    s.shapes.add({ geometry: "rect", position: { left: 0, top: 0, width: W, height: H }, fill: "#06182799", line: { style: "solid", fill: "none", width: 0 } });
    addText(s, "中期汇报", 70, 58, 200, 32, { fontSize: 18, bold: true, color: "#A7E7F2" });
    addText(s, "基于 Dify 的主数据\n自动化匹配与智能清洗系统", 70, 150, 760, 150, {
      fontSize: 50,
      bold: true,
      color: C.white,
    });
    addText(s, "面向商品主数据的候选生成、机器学习评分、LLM 冲突判定与 SQLite 落库验证", 74, 326, 780, 36, {
      fontSize: 21,
      color: "#D8F3FA",
    });
    addBox(s, 74, 562, 570, 72, "#0B3B5FBB", "#246E99", "rounded-xl");
    addText(s, "14组  李昱霖 / 蒋昀洲 / 李波涛", 102, 578, 520, 24, { fontSize: 18, bold: true, color: C.white });
    addText(s, "2026年7月 · 当前版本已完成端到端跑通与量化评估", 102, 604, 520, 24, { fontSize: 15, color: "#CAE7F0" });
  }

  // 2
  {
    const s = deck.slides.add();
    s.background.fill = "#F5FAFC";
    title(s, "01  项目定位", "我们要解决的是多源商品记录到唯一主数据的归并问题", "业务目标不是简单去重，而是让每条来源记录都有可追溯、可审计的唯一实体归属。");
    metric(s, "来源规模", "4,589", "tableA 1,363 条 + tableB 3,226 条", 70, 255, 260, 166, C.blue);
    metric(s, "字段标准", "4 项", "id / title / manufacturer / price", 370, 255, 260, 166, C.teal);
    metric(s, "主数据约束", "1 对 1", "一条来源记录只归属一个实体", 670, 255, 260, 166, C.amber);
    metric(s, "质量目标", "可验收", "覆盖率、重复链接、黄金记录一致性", 970, 255, 240, 166, C.green);
    bulletList(s, [
      { text: "输入是脏 CSV：标题写法不统一、制造商缺失、价格存在冲突。", color: C.blue },
      { text: "输出是唯一实体表：entities 与 golden_records 一实体一行，source_record_links 负责溯源。", color: C.teal },
      { text: "系统必须能解释每一步：候选从哪里来、为什么合并、哪些留给大模型判定。", color: C.amber },
    ], 84, 484, 1040, 44);
    footer(s, 2);
  }

  // 3
  {
    const s = deck.slides.add();
    s.background.fill = "#F7FBFC";
    title(s, "02  总体架构", "Dify 负责编排，FastAPI 负责确定性计算和数据库事务", "这条边界让长耗时模型计算、SQLite 写库、质量收尾都留在后端，Dify 只处理流程和 LLM 冲突判定。");
    const xs = [70, 310, 550, 790, 1030];
    const labels = [
      ["CSV 输入", "tableA / tableB 或 incoming"],
      ["FastAPI", "校验、预处理、候选生成"],
      ["模型评分", "XGBoost + MLP + 词法相似度"],
      ["Dify + LLM", "冲突簇批处理与知识库约束"],
      ["SQLite 主库", "entities / links / golden_records"],
    ];
    labels.forEach((it, i) => {
      step(s, String(i + 1), it[0], it[1], xs[i], 264, 190, [C.blue, C.teal, C.cyan, C.amber, C.green][i]);
      if (i < labels.length - 1) addText(s, "→", xs[i] + 199, 302, 44, 38, { fontSize: 30, bold: true, color: C.muted, alignment: "center" });
    });
    addBox(s, 100, 475, 1080, 98, "#E8F4F8", "#CCE0EA", "rounded-xl");
    addText(s, "关键工程取舍", 128, 493, 180, 30, { fontSize: 20, bold: true, color: C.blue });
    addText(s, "把 XGBoost、MLP、候选缓存、事务写入和最终质量审计放在后端；Dify 工作流通过 /candidate-batch 与 /commit-llm-batch 分批处理冲突簇，避免 HTTP 节点返回体过大。", 320, 492, 800, 54, { fontSize: 18, color: C.ink });
    footer(s, 3);
  }

  // 4
  {
    const s = deck.slides.add();
    s.background.fill = "#F5FAFC";
    title(s, "03  数据清洗", "预处理把同一商品的不同写法压到可比较的空间", "清洗不是为了丢信息，而是减少大小写、HTML、标点、品牌别名带来的无意义差异。");
    const left = [
      ["HTML / 标点 / 多空格", "移除噪声，降低标题相似度误差"],
      ["Unicode NFKC + 小写", "统一全角半角、大小写与特殊字符"],
      ["制造商归一化", "Microsoft / MS 等别名进入同一品牌空间"],
      ["价格 float 化", "只作为冲突信号，不作为绝对匹配条件"],
    ];
    left.forEach((row, i) => {
      addBox(s, 74, 240 + i * 78, 510, 58, C.white, "#D5E6EE", "rounded-lg");
      addText(s, row[0], 96, 251 + i * 78, 190, 28, { fontSize: 18, bold: true, color: C.blue });
      addText(s, row[1], 300, 251 + i * 78, 250, 30, { fontSize: 16, color: C.muted });
    });
    addBox(s, 655, 226, 510, 326, "#102033", "#102033", "rounded-xl");
    addText(s, "清洗前", 688, 252, 90, 28, { fontSize: 18, bold: true, color: "#9EE7F1" });
    addText(s, "Microsoft Office 2007 Professional UPGRADE\nMS Office Pro 2007 Upgrade\nmicrosoft office professional 2007", 688, 300, 420, 86, { fontSize: 21, color: C.white });
    addRule(s, 688, 414, 360, C.cyan);
    addText(s, "清洗后", 688, 438, 90, 28, { fontSize: 18, bold: true, color: "#9EE7F1" });
    addText(s, "manufacturer = microsoft\ntitle tokens = office / 2007 / professional / upgrade", 688, 486, 420, 58, { fontSize: 21, color: C.white });
    footer(s, 4);
  }

  // 5
  {
    const s = deck.slides.add();
    s.background.fill = "#F7FBFC";
    title(s, "04  候选生成", "从 440 万次笛卡尔比较降到 22,208 个候选，同时保留 98.11% 参考召回", "性能瓶颈不是模型，而是最开始的候选规模；标题 token 倒排索引解决了卡在“摄入并匹配”的问题。");
    metric(s, "原始全量比较", "≈4.4M", "tableA × tableB 的双重循环", 90, 250, 300, 156, C.red);
    metric(s, "优化后候选", "22,208", "倒排索引 + 制造商辅助召回", 490, 250, 300, 156, C.blue);
    metric(s, "参考候选覆盖", "98.11%", "1145 / 1167 对参考合并被覆盖", 890, 250, 300, 156, C.green);
    addBar(s, "全量比较", 4400000, 4400000, 620, 477, 390, C.red);
    addBar(s, "优化候选", 22208, 4400000, 620, 527, 390, C.blue);
    addText(s, "为什么 tableB 制造商为空也能跑：候选召回不只依赖 manufacturer，标题 token 仍然能把绝大多数疑似商品拉进候选池。", 94, 492, 430, 70, { fontSize: 19, color: C.ink });
    footer(s, 5);
  }

  // 6
  {
    const s = deck.slides.add();
    s.background.fill = "#F5FAFC";
    title(s, "05  评分与路由", "阈值不再只靠一个 0.9，而是分层决定自动合并、LLM 判定和保守拆分", "单纯降低阈值会让低分边进入连通分量互相竞争；互为最佳的一对一候选才适合补充自动合并。");
    const stages = [
      ["0.94", "高置信自动合并", "一对一、无竞争、无字段冲突"],
      ["0.78", "LLM 冲突簇保留线", "高于此线但不安全的候选交给 Dify + LLM"],
      ["0.60", "互为最佳自动合并", "双方都是最高分且分差 ≥ 0.04"],
    ];
    stages.forEach((it, i) => {
      const x = 88 + i * 390;
      addBox(s, x, 240, 322, 170, C.white, "#D7E6EE", "rounded-xl");
      addText(s, it[0], x + 26, 260, 112, 58, { fontSize: 40, bold: true, color: [C.green, C.blue, C.amber][i] });
      addText(s, it[1], x + 26, 326, 250, 30, { fontSize: 20, bold: true, color: C.ink });
      addText(s, it[2], x + 26, 365, 256, 38, { fontSize: 16, color: C.muted });
    });
    addBox(s, 118, 482, 1040, 88, "#E9F5F8", "#D0E4EC", "rounded-xl");
    addText(s, "模型融合", 150, 506, 150, 30, { fontSize: 21, bold: true, color: C.blue });
    addText(s, "confidence = 0.45 × 排名融合 + 0.35 × XGBoost + 0.10 × MLP + 0.10 × 词法相似度", 310, 504, 790, 32, { fontSize: 22, bold: true, color: C.ink });
    addText(s, "字段冲突包括制造商明显不一致或价格相差超过 35%。", 310, 538, 720, 24, { fontSize: 15, color: C.muted });
    footer(s, 6);
  }

  // 7
  {
    const s = deck.slides.add();
    s.background.fill = "#F7FBFC";
    title(s, "06  Dify 工作流", "Dify 处理的是冲突簇，而不是一次性吞掉全部候选", "工作流用 Loop 分批拉取冲突簇，结合知识库约束 LLM 输出，再把结构化结果写回后端。");
    await addImage(s, "image4.png", 86, 228, 710, 318, "contain");
    addBox(s, 836, 230, 330, 312, C.white, "#D7E6EE", "rounded-xl");
    bulletList(s, [
      { text: "Start 节点输入 tableA / tableB / threshold。", color: C.blue, size: 17 },
      { text: "HTTP 节点调用 /ingest-and-match，后端保存完整候选。", color: C.teal, size: 17 },
      { text: "Loop 里按批调用 /candidate-batch。", color: C.cyan, size: 17 },
      { text: "LLM 只返回当前簇的结构化实体分组。", color: C.amber, size: 17 },
      { text: "/finalize-match-run 做覆盖与质量收尾。", color: C.green, size: 17 },
    ], 862, 258, 270, 50);
    footer(s, 7);
  }

  // 8
  {
    const s = deck.slides.add();
    s.background.fill = "#F5FAFC";
    title(s, "07  数据库设计", "主数据质量靠表结构和收尾检查兜住，而不是靠一次模型判断", "每条来源记录唯一链接、每个实体一条黄金记录，是最终可验收的底线。");
    const tables = [
      ["entities", "唯一实体表，保存 canonical_title、manufacturer、price 与状态"],
      ["golden_records", "兼容展示表，与 entities 保持一实体一行"],
      ["source_record_links", "来源记录到实体的唯一映射，防止一条记录重复归属"],
      ["match_decisions", "记录自动合并、LLM 合并、最终收尾等决策日志"],
    ];
    tables.forEach((it, i) => {
      const y = 236 + i * 82;
      addBox(s, 96, y, 500, 58, C.white, "#D5E6EE", "rounded-lg");
      addText(s, it[0], 122, y + 14, 180, 26, { fontSize: 20, bold: true, color: C.blue });
      addText(s, it[1], 305, y + 13, 250, 30, { fontSize: 15, color: C.muted });
    });
    addBox(s, 698, 238, 398, 304, "#102033", "#102033", "rounded-xl");
    addText(s, "质量验收条件", 730, 262, 220, 32, { fontSize: 24, bold: true, color: C.white });
    bulletList(s, [
      { text: "source_record_links = 4,589", color: C.cyan, size: 18 },
      { text: "A / B 来源覆盖率 = 100%", color: C.green, size: 18 },
      { text: "duplicate_source_link_count = 0", color: C.amber, size: 18 },
      { text: "golden_records 与 entities 数量一致", color: C.cyan, size: 18 },
    ], 732, 324, 320, 52, C.white);
    footer(s, 8);
  }

  // 9
  {
    const s = deck.slides.add();
    s.background.fill = "#F7FBFC";
    title(s, "08  当前效果", "最新主库已经全量覆盖来源记录，A-B 链接 F1 达到 0.644", "这些指标来自当前 artifacts/mdm.sqlite3 与 reference master.csv 的离线比较。");
    metric(s, "A 覆盖率", "100%", "1,363 / 1,363", 78, 238, 250, 142, C.green);
    metric(s, "B 覆盖率", "100%", "3,226 / 3,226", 366, 238, 250, 142, C.green);
    metric(s, "A-B Precision", "0.784", "tp=638, fp=176", 654, 238, 250, 142, C.blue);
    metric(s, "A-B Recall", "0.547", "fn=529", 942, 238, 250, 142, C.amber);
    addBox(s, 98, 450, 470, 90, "#E8F4F8", "#D2E4EC", "rounded-xl");
    addText(s, "实体规模", 126, 470, 120, 28, { fontSize: 20, bold: true, color: C.blue });
    addText(s, "预测簇 3,599；标准簇 3,595；source links 4,589；重复来源链接 0。", 250, 470, 270, 46, { fontSize: 18, color: C.ink });
    addBox(s, 650, 450, 470, 90, "#FFF6E5", "#F9D28A", "rounded-xl");
    addText(s, "剩余误差", 678, 470, 120, 28, { fontSize: 20, bold: true, color: "#B45309" });
    addText(s, "主要来自相近版本、套装内容、授权类型和泛称标题之间的边界判断。", 802, 470, 260, 46, { fontSize: 18, color: C.ink });
    footer(s, 9);
  }

  // 10
  {
    const s = deck.slides.add();
    s.background.fill = "#F5FAFC";
    title(s, "09  方法边界", "当前分数能说明本项目跑通，但不能直接证明跨数据集泛化", "阈值参考了标准答案进行校准，因此汇报时要把它表述为实验校准结果，而不是无监督泛化结论。");
    addBox(s, 90, 240, 500, 250, "#FFFFFF", "#D9E7EE", "rounded-xl");
    addText(s, "我们已经完成", 122, 270, 220, 32, { fontSize: 24, bold: true, color: C.green });
    bulletList(s, [
      { text: "Dify + FastAPI + SQLite 端到端跑通。", color: C.green, size: 18 },
      { text: "候选生成性能从不可接受降到秒级。", color: C.green, size: 18 },
      { text: "互为最佳自动合并显著提升 A-B 链接 F1。", color: C.green, size: 18 },
    ], 124, 326, 390, 48);
    addBox(s, 690, 240, 500, 250, "#FFFFFF", "#D9E7EE", "rounded-xl");
    addText(s, "下一阶段要补齐", 722, 270, 250, 32, { fontSize: 24, bold: true, color: C.amber });
    bulletList(s, [
      { text: "拆分调参集 / 测试集，避免执果溯因。", color: C.amber, size: 18 },
      { text: "清理 Python 依赖版本警告，固定 sklearn / xgboost 环境。", color: C.amber, size: 18 },
      { text: "扩展到 MySQL 与增量 incoming CSV 场景。", color: C.amber, size: 18 },
    ], 724, 326, 390, 48);
    addText(s, "中期结论：系统已经从“能跑”进入“可评估、可调参、可解释”的阶段。", 146, 580, 970, 40, { fontSize: 26, bold: true, color: C.ink, alignment: "center" });
    footer(s, 10);
  }

  for (const [index, slide] of deck.slides.items.entries()) {
    const stem = `slide-${String(index + 1).padStart(2, "0")}`;
    await writeBlob(path.join(QA, `${stem}.png`), await deck.export({ slide, format: "png", scale: 1 }));
    const layout = await slide.export({ format: "layout" });
    await fs.writeFile(path.join(QA, `${stem}.layout.json`), await layout.text());
  }
  await writeBlob(path.join(QA, "deck-montage.webp"), await deck.export({ format: "webp", montage: true, scale: 1 }));
  const pptx = await PresentationFile.exportPptx(deck);
  await pptx.save(OUT);
  console.log(OUT);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
