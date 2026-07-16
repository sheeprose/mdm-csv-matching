import fs from "node:fs/promises";
import path from "node:path";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const ROOT = path.resolve(process.cwd(), "../../..");
const OUT = path.join(ROOT, "主数据管理_中期汇报_更新版.pptx");
const QA = path.join(ROOT, "work/presentations/mdm_midterm/scoring_qa");
const MEDIA = path.join(ROOT, "work/presentations/mdm_midterm/assets/ppt/media");
const W = 1280;
const H = 720;

const C = {
  bg: "#F5FAFC",
  ink: "#102033",
  muted: "#5E7188",
  blue: "#1456A0",
  cyan: "#06A7C7",
  teal: "#0E8F86",
  green: "#1F9D55",
  amber: "#F59E0B",
  red: "#DC2626",
  white: "#FFFFFF",
  line: "#D5E6EE",
  soft: "#E8F4F8",
  dark: "#102033",
};

async function readImage(name) {
  const bytes = await fs.readFile(path.join(MEDIA, name));
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
}

async function writeBlob(filePath, blob) {
  await fs.writeFile(filePath, new Uint8Array(await blob.arrayBuffer()));
}

function text(slide, value, x, y, w, h, style = {}) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    position: { left: x, top: y, width: w, height: h },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  shape.text = value;
  shape.text.style = {
    fontSize: style.fontSize ?? 18,
    bold: style.bold ?? false,
    color: style.color ?? C.ink,
    alignment: style.alignment ?? "left",
  };
  return shape;
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
  text(slide, section, 64, 36, 560, 26, { fontSize: 14, bold: true, color: C.cyan });
  text(slide, title, 64, 72, 1000, 56, { fontSize: 35, bold: true, color: C.ink });
  rule(slide, 64, 142, 92);
  if (subtitle) text(slide, subtitle, 64, 160, 1020, 42, { fontSize: 17, color: C.muted });
}

function footer(slide, page) {
  text(slide, "基于 Dify 的客户/商品主数据自动化匹配项目", 64, 674, 690, 24, { fontSize: 13, color: "#71859A" });
  text(slide, String(page).padStart(2, "0"), 1162, 672, 56, 26, { fontSize: 15, bold: true, color: C.blue, alignment: "right" });
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
    text(slide, item.text, x + 22, top, w - 22, item.h ?? 32, {
      fontSize: item.size ?? 17,
      bold: item.bold ?? false,
      color: item.textColor ?? color,
    });
  });
}

function metric(slide, label, value, note, x, y, w, h, accent = C.blue) {
  box(slide, x, y, w, h, C.white, C.line, "rounded-xl");
  slide.shapes.add({
    geometry: "rect",
    position: { left: x, top: y, width: 6, height: h },
    fill: accent,
    line: { style: "solid", fill: accent, width: 0 },
  });
  text(slide, value, x + 22, y + 16, w - 44, 44, { fontSize: 31, bold: true, color: C.ink });
  text(slide, label, x + 22, y + 66, w - 44, 25, { fontSize: 16, bold: true, color: C.blue });
  text(slide, note, x + 22, y + 94, w - 44, 38, { fontSize: 13.5, color: C.muted });
}

function card(slide, title, body, x, y, w, h, accent = C.blue) {
  box(slide, x, y, w, h, C.white, C.line, "rounded-xl");
  text(slide, title, x + 24, y + 20, w - 48, 28, { fontSize: 19, bold: true, color: accent });
  text(slide, body, x + 24, y + 60, w - 48, h - 72, { fontSize: 15.5, color: C.muted });
}

function processStep(slide, n, title, body, x, y, w, color) {
  box(slide, x, y, w, 108, C.white, C.line, "rounded-xl");
  slide.shapes.add({
    geometry: "ellipse",
    position: { left: x + 20, top: y + 20, width: 34, height: 34 },
    fill: color,
    line: { style: "solid", fill: color, width: 0 },
  });
  text(slide, n, x + 20, y + 26, 34, 20, { fontSize: 15, bold: true, color: C.white, alignment: "center" });
  text(slide, title, x + 66, y + 18, w - 86, 26, { fontSize: 17, bold: true, color: C.ink });
  text(slide, body, x + 66, y + 50, w - 86, 44, { fontSize: 13.5, color: C.muted });
}

function bar(slide, label, value, max, x, y, w, color, display) {
  text(slide, label, x, y, 210, 26, { fontSize: 15.5, bold: true, color: C.ink });
  box(slide, x + 220, y + 5, w, 16, "#E5EEF3", "#E5EEF3", "rounded-lg");
  box(slide, x + 220, y + 5, Math.max(4, (value / max) * w), 16, color, color, "rounded-lg");
  text(slide, display, x + 230 + w, y - 1, 100, 26, { fontSize: 15.5, bold: true, color });
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

async function main() {
  await fs.mkdir(QA, { recursive: true });
  const deck = Presentation.create({ slideSize: { width: W, height: H } });

  // 1
  {
    const s = deck.slides.add();
    await image(s, "image1.jpeg", 0, 0, W, H, "cover");
    s.shapes.add({ geometry: "rect", position: { left: 0, top: 0, width: W, height: H }, fill: "#06182799", line: { style: "solid", fill: "none", width: 0 } });
    text(s, "中期汇报", 70, 58, 200, 32, { fontSize: 18, bold: true, color: "#A7E7F2" });
    text(s, "基于 Dify 的客户/商品主数据\n自动化匹配与智能清洗系统", 70, 142, 860, 150, { fontSize: 49, bold: true, color: C.white });
    text(s, "围绕研究任务、Baseline、实验结果、改进方案与对比验证展开", 74, 326, 840, 34, { fontSize: 21, color: "#D8F3FA" });
    box(s, 74, 562, 586, 72, "#0B3B5FBB", "#246E99", "rounded-xl");
    text(s, "14组  李昱霖 / 蒋昀洲 / 李波涛", 102, 578, 520, 24, { fontSize: 18, bold: true, color: C.white });
    text(s, "2026年7月 · 中期进展汇报", 102, 604, 520, 24, { fontSize: 15, color: "#CAE7F0" });
  }

  // 2
  {
    const s = deck.slides.add();
    s.background.fill = C.bg;
    header(s, "01  研究任务", "面向多源商品数据的实体解析与主数据归并", "研究对象是存在命名差异、字段缺失和重复记录的商品主数据，目标是生成可追溯、可审计的唯一实体表。");
    metric(s, "输入规模", "4,589", "tableA 1,363 条；tableB 3,226 条", 74, 246, 250, 146, C.blue);
    metric(s, "标准字段", "4", "id、title、manufacturer、price", 360, 246, 250, 146, C.teal);
    metric(s, "实体约束", "唯一归属", "每条来源记录只链接一个实体", 646, 246, 250, 146, C.amber);
    metric(s, "交付结果", "主数据表", "entities、golden_records、source_record_links", 932, 246, 250, 146, C.green);
    bullet(s, [
      { text: "做什么：自动识别 tableA 与 tableB 中指向同一商品实体的记录，并生成黄金主数据。" },
      { text: "难点：标题写法不一致、制造商字段缺失、价格不完全可靠、版本与授权类型容易混淆。" },
      { text: "验收目标：既要提升匹配效果，也要保证来源记录覆盖、实体唯一性和决策可追溯。" },
    ], 92, 474, 1020, 46);
    footer(s, 2);
  }

  // 3
  {
    const s = deck.slides.add();
    s.background.fill = "#F7FBFC";
    header(s, "02  应用场景与项目价值", "主数据统一能够降低重复维护成本并提升下游数据可信度", "项目不只是做实体匹配算法，而是面向数据治理流程提供可落库、可审计的主数据清洗能力。");
    card(s, "企业主数据治理", "整合 ERP、CRM、PIM、采购系统中的客户或商品记录，减少重复建档和统计口径不一致。", 78, 248, 330, 154, C.blue);
    card(s, "电商商品去重", "识别标题不同但实际相同的商品，支持商品库清洗、价格比对、库存聚合和搜索归并。", 475, 248, 330, 154, C.teal);
    card(s, "数据质量审计", "通过 source_record_links、match_decisions 与 golden_records 保留来源映射和判定过程。", 872, 248, 330, 154, C.green);
    box(s, 130, 494, 1020, 82, C.soft, "#CFE3EB", "rounded-xl");
    text(s, "项目价值", 164, 516, 120, 28, { fontSize: 21, bold: true, color: C.blue });
    text(s, "用自动化匹配替代大规模人工比对，并把模型判定结果转化为可验证的主数据库，服务后续报表、检索、推荐和数据治理流程。", 298, 515, 780, 34, { fontSize: 18, color: C.ink });
    footer(s, 3);
  }

  // 4
  {
    const s = deck.slides.add();
    s.background.fill = C.bg;
    header(s, "03  国内外研究现状", "实体匹配正在从规则匹配走向机器学习与大模型协同", "现有方法大体经历了规则驱动、监督学习、深度语义匹配和 LLM 辅助数据治理几个阶段。");
    card(s, "规则与字符串相似度", "早期方法依赖精确匹配、编辑距离、Jaccard、Soundex 等规则，优点是可解释，缺点是对复杂别名和语序变化不够稳健。", 74, 246, 250, 230, C.blue);
    card(s, "传统机器学习实体匹配", "将候选记录对转化为特征向量，用逻辑回归、随机森林、梯度提升树等模型判断是否同实体，是工业场景常用 baseline。", 360, 246, 250, 230, C.teal);
    card(s, "深度语义匹配", "使用 BERT、Sentence-BERT 等模型理解文本语义，能处理更多语义差异，但训练和推理成本更高。", 646, 246, 250, 230, C.amber);
    card(s, "LLM 辅助数据治理", "近年趋势是用 LLM 处理复杂冲突、解释判定原因和生成结构化结果，但仍需要确定性系统做约束和验收。", 932, 246, 250, 230, C.green);
    text(s, "本项目定位：以机器学习实体匹配作为 Baseline，以 Dify + LLM 处理竞争冲突，以数据库质量审计保证最终主数据可用。", 126, 558, 1000, 32, { fontSize: 22, bold: true, color: C.ink, alignment: "center" });
    footer(s, 4);
  }

  // 5
  {
    const s = deck.slides.add();
    s.background.fill = "#F7FBFC";
    header(s, "04  技术路线", "机器学习匹配、Dify 编排与数据库验收形成闭环", "Dify 负责流程编排和 LLM 调用，FastAPI 负责预处理、模型评分、候选缓存和数据库事务。");
    const items = [
      ["CSV 输入", "tableA / tableB 或 incoming"],
      ["预处理与候选生成", "字段映射、清洗、blocking"],
      ["Baseline 模型评分", "XGBoost + MLP + 词法基线"],
      ["Dify + LLM", "冲突簇结构化判定"],
      ["主数据质量审计", "覆盖率、唯一性、黄金记录一致性"],
    ];
    items.forEach((it, i) => {
      const x = 70 + i * 240;
      processStep(s, String(i + 1), it[0], it[1], x, 260, 190, [C.blue, C.teal, C.cyan, C.amber, C.green][i]);
      if (i < items.length - 1) text(s, "→", x + 198, 300, 44, 36, { fontSize: 28, bold: true, color: C.muted, alignment: "center" });
    });
    box(s, 118, 480, 1044, 90, C.soft, "#CFE3EB", "rounded-xl");
    text(s, "技术特点", 150, 502, 140, 28, { fontSize: 21, bold: true, color: C.blue });
    text(s, "把高频、可确定的计算放在后端，把需要语义判断的竞争簇交给 LLM，并用数据库约束对最终结果进行质量闭环。", 292, 501, 805, 38, { fontSize: 18, color: C.ink });
    footer(s, 5);
  }

  // 6
  {
    const s = deck.slides.add();
    s.background.fill = C.bg;
    header(s, "05  Baseline 完成情况", "已完成可训练、可加载、可在线评分的机器学习匹配基线", "Baseline 将候选记录对建模为二分类任务，输入结构化相似度特征，输出同一实体置信度。");
    card(s, "XGBoost", "180 棵树，max_depth=4，learning_rate=0.04，用于捕捉文本、品牌、价格特征之间的非线性交互。", 78, 248, 326, 196, C.blue);
    card(s, "MLP", "StandardScaler + 隐层 (24, 12)，solver=lbfgs，提供另一种非线性决策边界。", 477, 248, 326, 196, C.teal);
    card(s, "Lexical Baseline", "字符/词级 TF-IDF 余弦 + Token Jaccard，作为稳定兜底和可解释的词法相似度信号。", 876, 248, 326, 196, C.amber);
    box(s, 140, 520, 1000, 74, C.soft, "#CFE3EB", "rounded-xl");
    text(s, "完成产物", 172, 542, 120, 28, { fontSize: 20, bold: true, color: C.blue });
    text(s, "artifacts/mdm_matcher.joblib 保存特征构造器、XGBoost、MLP、特征列和验证指标；FastAPI 服务启动后直接加载用于在线评分。", 302, 540, 780, 32, { fontSize: 17.5, color: C.ink });
    footer(s, 6);
  }

  // 7
  {
    const s = deck.slides.add();
    s.background.fill = "#F7FBFC";
    header(s, "06  特征工程", "Baseline 特征覆盖文本相似、品牌约束和价格差异", "特征体系强调可解释性，便于分析误匹配原因并与后续改进方案衔接。");
    const feats = [
      ["字符 TF-IDF 余弦", "char_wb 3-5 gram，捕捉版本号、缩写和拼写差异"],
      ["词级 TF-IDF 余弦", "word 1-2 gram，关注产品名、版本和授权词"],
      ["Token Jaccard", "衡量标题词集合重叠程度，作为粗筛和评分共同信号"],
      ["制造商相似度", "品牌一致给强正信号，缺失时返回中性值"],
      ["价格相似度", "以相对差异衡量价格一致性并识别明显冲突"],
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
    header(s, "07  Baseline 实验结果与问题分析", "Baseline 具备较强排序能力，但高阈值召回不足", "验证集指标表明模型能区分相似与不相似候选，但直接用单阈值落库会产生召回与误合并之间的矛盾。");
    metric(s, "ROC-AUC", "0.941", "整体区分能力较好", 84, 248, 246, 132, C.blue);
    metric(s, "Average Precision", "0.626", "正样本稀疏场景下的排序质量", 372, 248, 246, 132, C.teal);
    metric(s, "Precision@0.9", "0.875", "高阈值下误合并风险较低", 660, 248, 246, 132, C.green);
    metric(s, "Recall@0.9", "0.030", "高阈值导致大量真匹配未召回", 948, 248, 246, 132, C.red);
    box(s, 118, 460, 1044, 100, "#FFF6E5", "#F2D28A", "rounded-xl");
    text(s, "Baseline 主要问题", 150, 486, 190, 30, { fontSize: 21, bold: true, color: "#B45309" });
    text(s, "单一阈值策略难以同时兼顾精度与召回；候选对逐对判断无法处理一对多、多对一竞争关系；全量候选生成存在计算规模压力；制造商缺失会削弱 blocking 与字段约束。", 350, 482, 720, 42, { fontSize: 17, color: C.ink });
    footer(s, 8);
  }

  // 9
  {
    const s = deck.slides.add();
    s.background.fill = "#F7FBFC";
    header(s, "08  改进方案与项目创新", "在 Baseline 基础上引入候选优化、分层决策和 LLM 冲突簇判定", "改进目标是提升召回和端到端可用性，同时控制误合并风险和资源消耗。");
    card(s, "候选生成优化", "标题 token 倒排索引替代全量笛卡尔积，将候选规模从约 440 万次比较降至 22,208 个候选。", 74, 246, 250, 220, C.blue);
    card(s, "分层决策策略", "0.94 高置信自动合并，0.78 保留冲突簇，0.60 互为最佳补充合并，避免单阈值直接落库。", 360, 246, 250, 220, C.teal);
    card(s, "LLM 冲突簇判定", "Dify Loop 分批处理一对多、多对一和字段冲突候选，由 LLM 输出结构化实体分组。", 646, 246, 250, 220, C.amber);
    card(s, "质量审计闭环", "通过 source_record_links、match_decisions 和 golden_records 检查覆盖率、唯一性和可追溯性。", 932, 246, 250, 220, C.green);
    text(s, "创新点：不是只追求模型分数，而是将机器学习、LLM 和数据库质量约束组合成可运行的主数据治理流程。", 126, 550, 1000, 32, { fontSize: 22, bold: true, color: C.ink, alignment: "center" });
    footer(s, 9);
  }

  // 10
  {
    const s = deck.slides.add();
    s.background.fill = C.bg;
    header(s, "09  阶段性实验结果", "改进后系统已完成端到端运行并形成可量化结果", "以下结果来自当前 artifacts/mdm.sqlite3 与参考 master.csv 的离线比较。");
    metric(s, "A 覆盖率", "100%", "1,363 / 1,363", 72, 238, 250, 132, C.green);
    metric(s, "B 覆盖率", "100%", "3,226 / 3,226", 356, 238, 250, 132, C.green);
    metric(s, "A-B Precision", "0.784", "tp=638, fp=176", 640, 238, 250, 132, C.blue);
    metric(s, "A-B Recall", "0.547", "fn=529", 924, 238, 250, 132, C.amber);
    bar(s, "全量笛卡尔比较", 4400000, 4400000, 150, 452, 610, C.red, "≈4,400,000");
    bar(s, "优化后候选", 22208, 4400000, 150, 502, 610, C.blue, "22,208");
    text(s, "阶段结果说明：当前系统实现了来源记录全覆盖和候选规模压缩，A-B 链接 F1 达到 0.644，后续重点是进一步降低误合并并提高召回。", 150, 582, 900, 30, { fontSize: 18, color: C.ink });
    footer(s, 10);
  }

  // Legacy closing slides retained for reference only.
  if (false) {
  // 11
  {
    const s = deck.slides.add();
    s.background.fill = "#F7FBFC";
    header(s, "10  Dify 工作流与项目工作量", "已完成从工作流编排到后端服务与质量评估的完整原型", "中期阶段的工作量不仅包括模型训练，还包括 Dify 工作流、FastAPI 接口、数据库表设计和评估脚本。");
    await image(s, "image4.png", 78, 230, 660, 300, "contain");
    box(s, 780, 230, 360, 300, C.white, C.line, "rounded-xl");
    bullet(s, [
      { text: "FastAPI 接口：/ingest-and-match、/candidate-batch、/commit-llm-batch、/finalize-match-run。", size: 15.5, h: 40 },
      { text: "Dify 工作流：Start、HTTP 调用、Loop 批处理、LLM 结构化输出、最终收尾。", size: 15.5, h: 40 },
      { text: "数据库：entities、golden_records、source_record_links、match_decisions。", size: 15.5, h: 40 },
      { text: "实验工具：候选覆盖、参考答案比较、阈值分析和数据库形态统计脚本。", size: 15.5, h: 40 },
    ], 812, 260, 300, 62);
    footer(s, 11);
  }

  // 12
  {
    const s = deck.slides.add();
    s.background.fill = C.bg;
    header(s, "11  后续对比验证方法", "围绕 Baseline 建立可复现的改进效果评估", "后续将使用相同数据划分和相同评估脚本，对 Baseline 与改进方案进行横向比较。");
    const rows = [
      ["Baseline", "Pairwise ML 匹配 + 单阈值判定", "验证模型基础排序能力与单阈值策略的局限"],
      ["改进方案 A", "倒排索引 blocking + Baseline 打分", "验证候选生成对效率和召回的影响"],
      ["改进方案 B", "互为最佳自动合并 + 分层路由", "验证 precision / recall / F1 的变化"],
      ["改进方案 C", "Dify + LLM 冲突簇判定 + 质量审计", "验证复杂冲突处理、鲁棒性和用户体验提升"],
    ];
    rows.forEach((r, i) => {
      const y = 236 + i * 78;
      box(s, 88, y, 1090, 58, C.white, C.line, "rounded-lg");
      text(s, r[0], 112, y + 15, 150, 26, { fontSize: 18, bold: true, color: C.blue });
      text(s, r[1], 286, y + 15, 360, 26, { fontSize: 16, bold: true, color: C.ink });
      text(s, r[2], 680, y + 15, 420, 26, { fontSize: 15.5, color: C.muted });
    });
    text(s, "对比指标：Precision、Recall、F1、候选数、运行时间、重复来源链接数、LLM 调用量、人工审核量。", 126, 578, 1000, 32, { fontSize: 20, bold: true, color: C.ink, alignment: "center" });
    footer(s, 12);
  }

  // 13
  {
    const s = deck.slides.add();
    s.background.fill = "#F7FBFC";
    header(s, "12  预期提升与阶段结论", "下一阶段目标是提升精度、效率、鲁棒性和部署稳定性", "改进方案将围绕 Baseline 的召回不足、候选规模大和复杂冲突处理能力弱三个问题展开。");
    card(s, "精度与召回", "通过互为最佳规则和 LLM 冲突簇判定提升召回，同时用字段冲突检测控制误合并。", 78, 246, 250, 190, C.blue);
    card(s, "效率与资源", "候选生成避免全量笛卡尔积，减少模型打分次数和 LLM 处理规模。", 360, 246, 250, 190, C.teal);
    card(s, "鲁棒性", "对制造商缺失、标题别名、版本差异和价格冲突分别设置特征与规则，减少单一信号失效影响。", 646, 246, 250, 190, C.amber);
    card(s, "用户体验", "Dify 工作流降低使用门槛，质量审计报告让结果可检查、可追溯、可复现。", 932, 246, 250, 190, C.green);
    box(s, 128, 520, 1024, 82, C.soft, "#CFE3EB", "rounded-xl");
    text(s, "中期结论", 160, 542, 140, 28, { fontSize: 21, bold: true, color: C.blue });
    text(s, "项目已经完成 Baseline 训练、端到端原型、Dify 工作流和阶段性评估；下一阶段将通过标准化对比实验验证改进方案的实际收益。", 306, 540, 790, 34, { fontSize: 18, color: C.ink });
    footer(s, 13);
  }

  }

  // 11 - 前端量化展示
  {
    const s = deck.slides.add();
    s.background.fill = "#F7FBFC";
    header(s, "10  前端量化展示", "从数据接入到主数据结果，关键状态在一个界面内可量化、可追踪", "前端围绕处理规模、匹配质量、运行状态和结果审计组织信息，让主数据治理从黑盒任务变为可观察流程。");
    metric(s, "来源记录覆盖", "100%", "A 1,363 条 + B 3,226 条均完成入库与归属", 72, 236, 250, 132, C.green);
    metric(s, "候选压缩率", "99.5%", "约 440 万全量组合压缩为 22,208 个候选", 356, 236, 250, 132, C.cyan);
    metric(s, "跨表匹配精度", "0.784", "638 条正确链接，结果可回查到来源记录", 640, 236, 250, 132, C.blue);
    metric(s, "统一实体视图", "4,589", "来源记录、黄金记录与决策日志联动展示", 924, 236, 250, 132, C.teal);
    card(s, "运行看板", "展示批次进度、处理规模、成功率与异常状态，快速判断任务是否正常完成。", 78, 426, 326, 132, C.blue);
    card(s, "匹配结果", "支持按实体、来源表和置信度筛选，直观看到自动合并、待审与未匹配记录。", 477, 426, 326, 132, C.teal);
    card(s, "质量追踪", "覆盖率、唯一性、Precision / Recall 与候选规模集中呈现，便于横向比较。", 876, 426, 326, 132, C.green);
    footer(s, 11);
  }

  // 12 - 万能匹配
  {
    const s = deck.slides.add();
    s.background.fill = C.bg;
    header(s, "11  万能匹配", "无需预先固定表结构，让任意两张业务表完成字段理解、候选生成与实体归并", "系统把字段语义识别、标准化映射、机器学习评分和 LLM 冲突判定串成同一条可复用流水线。");
    const items = [
      ["任意表输入", "上传 CSV / Excel，自动读取字段、类型与样例"],
      ["字段语义映射", "识别名称、品牌、价格、地址等同义字段"],
      ["候选召回与评分", "Blocking 压缩规模，Baseline 输出匹配置信度"],
      ["冲突簇判定", "LLM 处理一对多、多对一和缺失字段"],
      ["统一结果输出", "生成黄金记录、来源链接和可审计决策"],
    ];
    items.forEach((it, i) => {
      const x = 70 + i * 240;
      processStep(s, String(i + 1), it[0], it[1], x, 242, 190, [C.blue, C.teal, C.cyan, C.amber, C.green][i]);
      if (i < items.length - 1) text(s, "→", x + 198, 282, 44, 36, { fontSize: 28, bold: true, color: C.muted, alignment: "center" });
    });
    card(s, "结构通用", "字段名不同、列顺序不同、部分字段缺失，均通过 schema 映射转为统一匹配语义。", 78, 410, 326, 142, C.blue);
    card(s, "策略通用", "确定性规则处理高置信样本，机器学习负责排序，LLM 只进入复杂冲突簇。", 477, 410, 326, 142, C.amber);
    card(s, "结果通用", "统一输出实体、黄金记录、来源映射和判定日志，可接入客户、商品等主数据域。", 876, 410, 326, 142, C.green);
    text(s, "核心价值：一次配置完成从“任意输入表”到“可信主数据”的端到端匹配闭环。", 126, 586, 1000, 32, { fontSize: 21, bold: true, color: C.ink, alignment: "center" });
    footer(s, 12);
  }

  // 13 - 总结
  {
    const s = deck.slides.add();
    s.background.fill = "#F7FBFC";
    header(s, "12  总结", "已完成可运行、可量化、可扩展的主数据自动匹配原型", "中期成果覆盖算法、工作流、后端服务、前端展示与质量审计，验证了技术路线和工程落地的可行性。");
    card(s, "已经完成", "Baseline 模型、候选压缩、分层决策、Dify 工作流、FastAPI 服务、主数据表结构与可视化前端已形成端到端闭环。", 78, 238, 326, 190, C.blue);
    card(s, "阶段价值", "4,589 条来源记录实现 100% 覆盖；候选规模压缩至 22,208；结果具备来源映射、质量指标和决策追踪。", 477, 238, 326, 190, C.teal);
    card(s, "下一步", "围绕误合并与漏匹配调优阈值和特征，补充多数据集对比实验，并完善人工复核与生产部署能力。", 876, 238, 326, 190, C.green);
    box(s, 128, 500, 1024, 90, C.soft, "#CFE3EB", "rounded-xl");
    text(s, "结论", 160, 524, 120, 28, { fontSize: 21, bold: true, color: C.blue });
    text(s, "项目已从“匹配模型”推进为“主数据治理系统”：既能处理复杂匹配，又能量化效果、解释结果并持续迭代。", 286, 522, 810, 42, { fontSize: 19, bold: true, color: C.ink });
    footer(s, 13);
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
