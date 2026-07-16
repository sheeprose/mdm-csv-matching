import fs from "node:fs/promises";
import path from "node:path";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const ROOT = "C:/Users/86153/Desktop/mdm_data";
const OUT = path.join(ROOT, "主数据管理_中期汇报_正式版.pptx");
const QA = path.join(ROOT, "work/presentations/mdm_midterm/formal_qa");
const MEDIA = path.join(ROOT, "work/presentations/mdm_midterm/assets/ppt/media");
const W = 1280;
const H = 720;

const C = {
  navy: "#0B1F33",
  ink: "#102033",
  muted: "#5E7188",
  blue: "#1456A0",
  cyan: "#06A7C7",
  teal: "#0E8F86",
  green: "#1F9D55",
  amber: "#F59E0B",
  red: "#DC2626",
  bg: "#F5FAFC",
  soft: "#E8F4F8",
  line: "#D5E6EE",
  white: "#FFFFFF",
};

async function readImage(name) {
  const bytes = await fs.readFile(path.join(MEDIA, name));
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
}

async function writeBlob(filePath, blob) {
  await fs.writeFile(filePath, new Uint8Array(await blob.arrayBuffer()));
}

function text(slide, value, x, y, w, h, style = {}) {
  const s = slide.shapes.add({
    geometry: "textbox",
    position: { left: x, top: y, width: w, height: h },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  s.text = value;
  s.text.style = {
    fontSize: style.fontSize ?? 18,
    bold: style.bold ?? false,
    color: style.color ?? C.ink,
    alignment: style.alignment ?? "left",
  };
  return s;
}

function box(slide, x, y, w, h, fill = C.white, line = C.line, radius = "rounded-lg") {
  return slide.shapes.add({
    geometry: "roundRect",
    position: { left: x, top: y, width: w, height: h },
    fill,
    line: { style: "solid", fill: line, width: 1 },
    borderRadius: radius,
  });
}

function rule(slide, x, y, w, color = C.cyan) {
  slide.shapes.add({
    geometry: "rect",
    position: { left: x, top: y, width: w, height: 4 },
    fill: color,
    line: { style: "solid", fill: color, width: 0 },
  });
}

function header(slide, section, title, subtitle = "") {
  text(slide, section, 64, 36, 520, 26, { fontSize: 14, bold: true, color: C.cyan });
  text(slide, title, 64, 72, 940, 56, { fontSize: 35, bold: true, color: C.ink });
  rule(slide, 64, 142, 92);
  if (subtitle) text(slide, subtitle, 64, 160, 980, 42, { fontSize: 17, color: C.muted });
}

function footer(slide, page) {
  text(slide, "基于 Dify 的客户/商品主数据自动化匹配项目", 64, 674, 690, 24, { fontSize: 13, color: "#71859A" });
  text(slide, String(page).padStart(2, "0"), 1162, 672, 56, 26, { fontSize: 15, bold: true, color: C.blue, alignment: "right" });
}

function metric(slide, label, value, note, x, y, w, h, accent = C.blue) {
  box(slide, x, y, w, h, C.white, C.line, "rounded-xl");
  slide.shapes.add({
    geometry: "rect",
    position: { left: x, top: y, width: 6, height: h },
    fill: accent,
    line: { style: "solid", fill: accent, width: 0 },
  });
  text(slide, value, x + 24, y + 18, w - 48, 48, { fontSize: 32, bold: true, color: C.ink });
  text(slide, label, x + 24, y + 72, w - 48, 25, { fontSize: 16, bold: true, color: C.blue });
  text(slide, note, x + 24, y + 102, w - 48, 42, { fontSize: 14, color: C.muted });
}

function bullet(slide, items, x, y, w, gap = 42, color = C.ink) {
  items.forEach((item, i) => {
    const top = y + i * gap;
    slide.shapes.add({
      geometry: "ellipse",
      position: { left: x, top: top + 8, width: 10, height: 10 },
      fill: item.color ?? C.cyan,
      line: { style: "solid", fill: item.color ?? C.cyan, width: 0 },
    });
    text(slide, item.text, x + 23, top, w - 23, item.h ?? 32, {
      fontSize: item.size ?? 17,
      bold: item.bold ?? false,
      color: item.textColor ?? color,
    });
  });
}

function processStep(slide, n, title, body, x, y, w, color) {
  box(slide, x, y, w, 112, C.white, C.line, "rounded-xl");
  slide.shapes.add({
    geometry: "ellipse",
    position: { left: x + 20, top: y + 20, width: 36, height: 36 },
    fill: color,
    line: { style: "solid", fill: color, width: 0 },
  });
  text(slide, n, x + 20, y + 27, 36, 20, { fontSize: 15, bold: true, color: C.white, alignment: "center" });
  text(slide, title, x + 68, y + 18, w - 88, 28, { fontSize: 17, bold: true, color: C.ink });
  text(slide, body, x + 68, y + 50, w - 88, 46, { fontSize: 13.5, color: C.muted });
}

function pill(slide, label, x, y, w, fill, color) {
  box(slide, x, y, w, 32, fill, fill, "rounded-lg");
  text(slide, label, x + 10, y + 6, w - 20, 20, { fontSize: 13, bold: true, color, alignment: "center" });
}

async function image(slide, name, x, y, w, h, fit = "cover") {
  const ext = name.split(".").pop().toLowerCase();
  slide.images.add({
    blob: await readImage(name),
    contentType: ext === "jpg" || ext === "jpeg" ? "image/jpeg" : "image/png",
    alt: name,
    fit,
    position: { left: x, top: y, width: w, height: h },
    geometry: "roundRect",
    borderRadius: "rounded-xl",
  });
}

function bar(slide, label, value, max, x, y, w, color, display = String(value)) {
  text(slide, label, x, y, 220, 26, { fontSize: 15.5, bold: true, color: C.ink });
  box(slide, x + 230, y + 5, w, 16, "#E5EEF3", "#E5EEF3", "rounded-lg");
  box(slide, x + 230, y + 5, Math.max(4, (value / max) * w), 16, color, color, "rounded-lg");
  text(slide, display, x + 240 + w, y - 1, 90, 26, { fontSize: 15.5, bold: true, color });
}

async function main() {
  await fs.mkdir(QA, { recursive: true });
  const deck = Presentation.create({ slideSize: { width: W, height: H } });

  // 1
  {
    const s = deck.slides.add();
    await image(s, "image1.jpeg", 0, 0, W, H, "cover");
    s.shapes.add({ geometry: "rect", position: { left: 0, top: 0, width: W, height: H }, fill: "#06182799", line: { style: "solid", fill: "none", width: 0 } });
    text(s, "中期汇报", 70, 58, 200, 32, { fontSize: 18, bold: true, color: "#A7E7F2" });
    text(s, "基于 Dify 的客户/商品主数据\n自动化匹配与智能清洗系统", 70, 142, 840, 150, { fontSize: 49, bold: true, color: C.white });
    text(s, "机器学习候选匹配、LLM 冲突判定与主数据质量审计的一体化实现", 74, 326, 820, 34, { fontSize: 21, color: "#D8F3FA" });
    box(s, 74, 562, 586, 72, "#0B3B5FBB", "#246E99", "rounded-xl");
    text(s, "14组  李昱霖 / 蒋昀洲 / 李波涛", 102, 578, 520, 24, { fontSize: 18, bold: true, color: C.white });
    text(s, "2026年7月 · 中期进展汇报", 102, 604, 520, 24, { fontSize: 15, color: "#CAE7F0" });
  }

  // 2
  {
    const s = deck.slides.add();
    s.background.fill = C.bg;
    header(s, "01  研究任务", "面向多源商品数据的实体解析与主数据归并", "项目目标是在存在脏数据、缺失字段和异构命名的情况下，构建可追溯的唯一实体主数据。");
    metric(s, "输入规模", "4,589", "tableA 1,363 条；tableB 3,226 条", 74, 246, 250, 146, C.blue);
    metric(s, "标准字段", "4", "id、title、manufacturer、price", 360, 246, 250, 146, C.teal);
    metric(s, "核心约束", "唯一归属", "每条来源记录只链接一个实体", 646, 246, 250, 146, C.amber);
    metric(s, "交付形态", "主数据表", "entities、golden_records、source_record_links", 932, 246, 250, 146, C.green);
    bullet(s, [
      { text: "商品标题是主要匹配信号，但存在大小写、版本、套装、授权类型等差异。" },
      { text: "制造商字段可以提供强约束，但在 tableB 中存在较多缺失，不能作为唯一 blocking 条件。" },
      { text: "最终结果需要同时满足匹配效果和数据库质量约束，避免形成不可追溯的合并结果。" },
    ], 92, 474, 1020, 46);
    footer(s, 2);
  }

  // 3
  {
    const s = deck.slides.add();
    s.background.fill = "#F7FBFC";
    header(s, "02  系统架构", "工作流编排与确定性计算解耦", "Dify 负责流程编排和 LLM 调用，FastAPI 负责预处理、模型评分、候选缓存和数据库事务。");
    const items = [
      ["CSV 输入", "tableA / tableB 或 incoming"],
      ["FastAPI 服务", "校验、预处理、候选生成"],
      ["ML 匹配模型", "XGBoost + MLP + 词法相似度"],
      ["Dify + LLM", "冲突簇批处理与结构化判定"],
      ["SQLite 主库", "实体表、黄金记录和来源链接"],
    ];
    items.forEach((it, i) => {
      const x = 70 + i * 240;
      processStep(s, String(i + 1), it[0], it[1], x, 260, 190, [C.blue, C.teal, C.cyan, C.amber, C.green][i]);
      if (i < items.length - 1) text(s, "→", x + 198, 300, 44, 36, { fontSize: 28, bold: true, color: C.muted, alignment: "center" });
    });
    box(s, 118, 480, 1044, 90, C.soft, "#CFE3EB", "rounded-xl");
    text(s, "设计依据", 150, 502, 140, 28, { fontSize: 21, bold: true, color: C.blue });
    text(s, "模型推理、事务写入和质量检查具有确定性要求，放在后端更便于调试和复现；LLM 仅处理需要语义判断的冲突簇。", 292, 501, 805, 38, { fontSize: 18, color: C.ink });
    footer(s, 3);
  }

  // 4
  {
    const s = deck.slides.add();
    s.background.fill = C.bg;
    header(s, "03  数据预处理", "字段标准化为后续特征工程提供一致输入", "预处理将异构 CSV 映射到统一字段，并降低文本噪声对模型特征的干扰。");
    const rows = [
      ["字段映射", "支持 product_id、sku、name、brand 等常见别名映射到标准字段"],
      ["文本规范化", "Unicode NFKC、小写化、HTML 清理、标点与重复空格处理"],
      ["制造商归一化", "品牌别名进入统一空间，例如 Microsoft 与 MS 的归一化"],
      ["价格处理", "转换为 float，用于冲突检测和价格相似度特征"],
    ];
    rows.forEach((r, i) => {
      box(s, 74, 236 + i * 76, 510, 58, C.white, C.line, "rounded-lg");
      text(s, r[0], 98, 249 + i * 76, 150, 28, { fontSize: 18, bold: true, color: C.blue });
      text(s, r[1], 258, 249 + i * 76, 290, 30, { fontSize: 15.5, color: C.muted });
    });
    box(s, 660, 228, 480, 326, "#102033", "#102033", "rounded-xl");
    text(s, "示例", 692, 252, 80, 28, { fontSize: 18, bold: true, color: "#9EE7F1" });
    text(s, "清洗前", 692, 300, 90, 26, { fontSize: 17, bold: true, color: C.white });
    text(s, "Microsoft Office 2007 Professional UPGRADE\nMS Office Pro 2007 Upgrade", 790, 300, 270, 58, { fontSize: 18, color: C.white });
    rule(s, 692, 386, 360, C.cyan);
    text(s, "清洗后", 692, 426, 90, 26, { fontSize: 17, bold: true, color: C.white });
    text(s, "manufacturer = microsoft\ntitle tokens = office / 2007 / professional / upgrade", 790, 426, 300, 58, { fontSize: 18, color: C.white });
    footer(s, 4);
  }

  // 5
  {
    const s = deck.slides.add();
    s.background.fill = "#F7FBFC";
    header(s, "04  候选生成", "倒排索引显著降低候选对计算规模", "候选生成阶段以召回为优先目标，在避免全量笛卡尔积的同时保留绝大多数参考匹配对。");
    metric(s, "全量比较规模", "≈4.4M", "tableA × tableB 双重循环", 90, 240, 300, 150, C.red);
    metric(s, "优化后候选数", "22,208", "标题 token 倒排索引与制造商辅助召回", 490, 240, 300, 150, C.blue);
    metric(s, "候选覆盖率", "98.11%", "参考合并对覆盖 1145 / 1167", 890, 240, 300, 150, C.green);
    bar(s, "全量笛卡尔积", 4400000, 4400000, 150, 476, 610, C.red, "≈4,400,000");
    bar(s, "优化后候选", 22208, 4400000, 150, 526, 610, C.blue, "22,208");
    text(s, "候选召回不完全依赖制造商字段，因此在 tableB 制造商缺失较多的情况下仍可通过标题 token 保持覆盖。", 150, 586, 900, 30, { fontSize: 18, color: C.ink });
    footer(s, 5);
  }

  // 6
  {
    const s = deck.slides.add();
    s.background.fill = C.bg;
    header(s, "05  机器学习模型", "候选对匹配被建模为二分类问题", "模型输入是候选记录对的结构化相似度特征，输出同一实体的置信度。");
    const models = [
      ["XGBoost", "180 棵树，max_depth=4，learning_rate=0.04", "用于捕捉文本、品牌、价格特征之间的非线性交互。", C.blue],
      ["MLP", "StandardScaler + 隐层 (24, 12)，solver=lbfgs", "作为神经网络分支，提供另一种非线性决策边界。", C.teal],
      ["Lexical Baseline", "char / word TF-IDF + token Jaccard", "在模型版本不稳定或样本稀疏时提供稳定兜底信号。", C.amber],
    ];
    models.forEach((m, i) => {
      const x = 78 + i * 386;
      box(s, x, 246, 326, 230, C.white, C.line, "rounded-xl");
      pill(s, m[0], x + 26, 270, 140, m[3] + "22", m[3]);
      text(s, m[1], x + 26, 322, 260, 44, { fontSize: 18, bold: true, color: C.ink });
      text(s, m[2], x + 26, 388, 260, 54, { fontSize: 16, color: C.muted });
    });
    box(s, 148, 530, 984, 74, C.soft, "#CFE3EB", "rounded-xl");
    text(s, "训练产物", 178, 552, 120, 26, { fontSize: 20, bold: true, color: C.blue });
    text(s, "artifacts/mdm_matcher.joblib 保存特征构造器、XGBoost、MLP、特征列和验证集指标，服务启动时直接加载用于在线评分。", 306, 550, 770, 32, { fontSize: 17.5, color: C.ink });
    footer(s, 6);
  }

  // 7
  {
    const s = deck.slides.add();
    s.background.fill = "#F7FBFC";
    header(s, "06  特征工程", "特征体系覆盖文本相似、品牌约束和价格差异", "匹配判断不是只看标题字符串，而是综合多类可解释特征。");
    const feats = [
      ["字符 TF-IDF 余弦", "char_wb 3-5 gram，适合捕捉版本号、缩写和拼写差异"],
      ["词级 TF-IDF 余弦", "word 1-2 gram，关注标题中的产品名、版本和授权词"],
      ["Token Jaccard", "衡量标题词集合重叠程度，作为粗筛和评分共同信号"],
      ["制造商相似度", "品牌一致给强正信号；缺失时返回中性值而非直接判负"],
      ["价格相似度", "以相对差异衡量价格一致性，同时识别明显冲突"],
      ["长度比例与首词一致", "补充标题结构相近程度和主品牌/品类信号"],
    ];
    feats.forEach((f, i) => {
      const x = i % 2 === 0 ? 86 : 660;
      const y = 238 + Math.floor(i / 2) * 104;
      box(s, x, y, 500, 74, C.white, C.line, "rounded-lg");
      text(s, f[0], x + 24, y + 14, 185, 26, { fontSize: 17.5, bold: true, color: C.blue });
      text(s, f[1], x + 222, y + 12, 240, 40, { fontSize: 14.5, color: C.muted });
    });
    footer(s, 7);
  }

  // 8
  {
    const s = deck.slides.add();
    s.background.fill = C.bg;
    header(s, "07  评分融合", "多模型融合提升候选对排序的稳定性", "最终置信度同时利用模型概率、词法相似度和批内排名，降低单一模型误差对结果的影响。");
    box(s, 118, 240, 1044, 92, "#102033", "#102033", "rounded-xl");
    text(s, "confidence = 0.45 × rank_fusion + 0.35 × XGBoost + 0.10 × MLP + 0.10 × lexical", 158, 274, 960, 34, {
      fontSize: 25,
      bold: true,
      color: C.white,
      alignment: "center",
    });
    metric(s, "ROC-AUC", "0.941", "验证集整体区分能力", 96, 410, 250, 142, C.blue);
    metric(s, "Average Precision", "0.626", "正样本稀疏场景下的排序质量", 378, 410, 250, 142, C.teal);
    metric(s, "Precision@0.9", "0.875", "高阈值下误合并风险较低", 660, 410, 250, 142, C.green);
    metric(s, "Best F1 Proxy", "0.610", "离线阈值扫描的参考结果", 942, 410, 250, 142, C.amber);
    footer(s, 8);
  }

  // 9
  {
    const s = deck.slides.add();
    s.background.fill = "#F7FBFC";
    header(s, "08  分层决策策略", "候选评分转化为自动合并、LLM 判定与保守保留三类路径", "部署阶段不采用单一阈值直接合并，而是根据置信度、竞争关系和字段冲突进行路由。");
    const rules = [
      ["0.94", "高置信自动合并", "一对一、无竞争、无制造商或价格冲突", C.green],
      ["0.78", "LLM 冲突簇保留", "达到保留线但存在竞争或不满足安全合并条件", C.blue],
      ["0.60", "互为最佳补充合并", "双方均为最高分候选，且分差不低于 0.04", C.amber],
    ];
    rules.forEach((r, i) => {
      const x = 88 + i * 390;
      box(s, x, 242, 322, 184, C.white, C.line, "rounded-xl");
      text(s, r[0], x + 26, 262, 112, 54, { fontSize: 39, bold: true, color: r[3] });
      text(s, r[1], x + 26, 326, 250, 30, { fontSize: 19, bold: true, color: C.ink });
      text(s, r[2], x + 26, 368, 260, 44, { fontSize: 15.5, color: C.muted });
    });
    box(s, 126, 500, 1028, 80, C.soft, "#CFE3EB", "rounded-xl");
    text(s, "策略意义", 158, 522, 120, 28, { fontSize: 20, bold: true, color: C.blue });
    text(s, "高阈值保证自动合并精度，互为最佳规则补充召回，LLM 只处理存在竞争关系的实体簇，从而兼顾效率、可解释性和风险控制。", 286, 520, 800, 34, { fontSize: 18, color: C.ink });
    footer(s, 9);
  }

  // 10
  {
    const s = deck.slides.add();
    s.background.fill = C.bg;
    header(s, "09  Dify 工作流", "冲突簇分批判定并结构化写回主数据服务", "Dify 不承担模型训练与数据库事务，而是通过 Loop 节点完成 LLM 判定的编排。");
    await image(s, "image4.png", 86, 224, 700, 318, "contain");
    box(s, 828, 232, 340, 300, C.white, C.line, "rounded-xl");
    bullet(s, [
      { text: "Start 节点接收 CSV 路径与阈值。", size: 16.5 },
      { text: "/ingest-and-match 返回 match_run_id 与路由摘要。", size: 16.5 },
      { text: "/candidate-batch 分批拉取冲突簇。", size: 16.5 },
      { text: "LLM 按结构化 JSON 输出实体分组。", size: 16.5 },
      { text: "/commit-llm-batch 与 /finalize-match-run 完成写回和收尾。", size: 16.5 },
    ], 856, 264, 270, 48);
    footer(s, 10);
  }

  // 11
  {
    const s = deck.slides.add();
    s.background.fill = "#F7FBFC";
    header(s, "10  数据库与质量审计", "表结构约束保证来源记录、实体与黄金记录的一致性", "模型结果最终必须通过数据库约束和质量检查，才能作为主数据输出。");
    const tables = [
      ["entities", "唯一实体表，保存 canonical_title、manufacturer、price 与状态"],
      ["golden_records", "展示与下游使用的黄金记录，与 entities 保持一实体一行"],
      ["source_record_links", "来源记录到实体的唯一映射，约束一条记录只归属一个实体"],
      ["match_decisions", "记录自动合并、LLM 判定和最终收尾等决策过程"],
    ];
    tables.forEach((t, i) => {
      const y = 232 + i * 78;
      box(s, 86, y, 548, 58, C.white, C.line, "rounded-lg");
      text(s, t[0], 110, y + 14, 180, 26, { fontSize: 19, bold: true, color: C.blue });
      text(s, t[1], 300, y + 13, 285, 30, { fontSize: 14.5, color: C.muted });
    });
    box(s, 714, 248, 392, 274, "#102033", "#102033", "rounded-xl");
    text(s, "质量审计指标", 746, 272, 210, 30, { fontSize: 23, bold: true, color: C.white });
    bullet(s, [
      { text: "source_record_links = 4,589", textColor: C.white, color: C.cyan, size: 17 },
      { text: "tableA 与 tableB 覆盖率均为 100%", textColor: C.white, color: C.green, size: 17 },
      { text: "duplicate_source_link_count = 0", textColor: C.white, color: C.amber, size: 17 },
      { text: "golden_records 与 entities 数量一致", textColor: C.white, color: C.cyan, size: 17 },
    ], 748, 326, 310, 48, C.white);
    footer(s, 11);
  }

  // 12
  {
    const s = deck.slides.add();
    s.background.fill = C.bg;
    header(s, "11  阶段结果与后续计划", "当前系统已完成端到端验证，并形成可量化评估结果", "以下结果来自当前 artifacts/mdm.sqlite3 与参考 master.csv 的离线比较。");
    metric(s, "A 覆盖率", "100%", "1,363 / 1,363", 72, 238, 250, 132, C.green);
    metric(s, "B 覆盖率", "100%", "3,226 / 3,226", 356, 238, 250, 132, C.green);
    metric(s, "A-B Precision", "0.784", "tp=638, fp=176", 640, 238, 250, 132, C.blue);
    metric(s, "A-B Recall", "0.547", "fn=529", 924, 238, 250, 132, C.amber);
    box(s, 104, 440, 470, 104, C.white, C.line, "rounded-xl");
    text(s, "已完成工作", 132, 462, 150, 28, { fontSize: 20, bold: true, color: C.green });
    bullet(s, [
      { text: "Dify + FastAPI + SQLite 主流程跑通。", size: 15.5, color: C.green },
      { text: "候选生成性能优化并保持 98.11% 候选召回。", size: 15.5, color: C.green },
      { text: "模型评分、分层路由和质量审计已形成闭环。", size: 15.5, color: C.green },
    ], 136, 500, 380, 28);
    box(s, 700, 440, 470, 104, "#FFF8E8", "#F6D99B", "rounded-xl");
    text(s, "后续计划", 728, 462, 150, 28, { fontSize: 20, bold: true, color: "#B45309" });
    bullet(s, [
      { text: "划分调参集与测试集，降低标准答案校准带来的偏差。", size: 15.5, color: C.amber },
      { text: "固定 sklearn / xgboost 版本，减少模型反序列化警告。", size: 15.5, color: C.amber },
      { text: "扩展增量 incoming CSV 与 MySQL 部署验证。", size: 15.5, color: C.amber },
    ], 732, 500, 370, 28);
    text(s, "阶段结论：项目已具备可运行、可评估、可解释的中期原型，下一阶段重点转向泛化验证和部署稳定性。", 134, 610, 1010, 34, { fontSize: 22, bold: true, color: C.ink, alignment: "center" });
    footer(s, 12);
  }

  for (const [index, slide] of deck.slides.items.entries()) {
    const stem = `slide-${String(index + 1).padStart(2, "0")}`;
    await writeBlob(path.join(QA, `${stem}.png`), await deck.export({ slide, format: "png", scale: 1 }));
    await fs.writeFile(path.join(QA, `${stem}.layout.json`), await (await slide.export({ format: "layout" })).text());
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
