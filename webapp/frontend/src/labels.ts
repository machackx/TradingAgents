// Chinese names for what the events call things by their graph names.

export const NODE_LABELS: Record<string, string> = {
  "Prepare market data": "准备行情数据",
  "Market Analyst": "市场分析师",
  "Sentiment Analyst": "情绪分析师",
  "News Analyst": "新闻分析师",
  "Fundamentals Analyst": "基本面分析师",
  "Memory Log": "历史决策复盘",
  "Evidence check": "证据检查",
  "Bull Researcher": "多头研究员",
  "Bear Researcher": "空头研究员",
  "Research Manager": "研究经理",
  Trader: "交易员",
  "Aggressive Analyst": "激进风控",
  "Conservative Analyst": "保守风控",
  "Neutral Analyst": "中性风控",
  "Portfolio Manager": "投资组合经理",
};

export const PHASE_LABELS: Record<string, string> = {
  data: "数据",
  analysts: "分析师团队",
  evidence: "证据",
  research: "研究辩论",
  trading: "交易",
  risk: "风险讨论",
  portfolio: "最终决策",
  other: "其他",
};

export const STEP_LABELS: Record<string, string> = {
  agent: "思考中",
  tools: "调用工具",
  wrap_up: "工具轮数用尽，收尾",
  finish: "整理证据",
};

export const SECTION_LABELS: Record<string, string> = {
  market_report: "市场技术报告",
  sentiment_report: "情绪报告",
  news_report: "新闻报告",
  fundamentals_report: "基本面报告",
  investment_plan: "投资计划",
  trader_investment_plan: "交易建议",
  final_trade_decision: "最终决策",
};

export const RUN_STATUS_LABELS: Record<string, string> = {
  running: "运行中",
  finished: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};

export const NODE_STATUS_LABELS: Record<string, string> = {
  pending: "等待",
  running: "进行中",
  done: "完成",
  failed: "出错",
  stopped: "中断",
  skipped: "未执行",
};

export const RATING_LABELS: Record<string, string> = {
  Buy: "买入",
  Overweight: "增持",
  Hold: "持有",
  Underweight: "低配",
  Sell: "卖出",
  REVIEW: "待复核",
};

export const RATING_TONE: Record<string, "up" | "down" | "level" | "warn"> = {
  Buy: "up",
  Overweight: "up",
  Hold: "level",
  Underweight: "down",
  Sell: "down",
  REVIEW: "warn",
};

export const SCHEMA_LABELS: Record<string, string> = {
  research_plan: "研究计划",
  trader_proposal: "交易提议",
  portfolio_decision: "组合决策",
  sentiment_report: "情绪评分",
};

export const FIELD_LABELS: Record<string, string> = {
  recommendation: "建议",
  rationale: "理由",
  strategic_actions: "执行要点",
  action: "方向",
  reasoning: "理由",
  entry_price: "入场价",
  stop_loss: "止损价",
  position_sizing: "仓位",
  position_size_pct: "仓位占比 %",
  order_quantity: "数量",
  rating: "评级",
  executive_summary: "摘要",
  investment_thesis: "投资逻辑",
  price_target: "目标价",
  time_horizon: "周期",
  overall_band: "情绪区间",
  overall_score: "情绪分 (0–10)",
  confidence: "置信度",
  narrative: "说明",
};

export const ANALYST_LABELS: Record<string, string> = {
  market: "市场",
  social: "情绪",
  news: "新闻",
  fundamentals: "基本面",
};

export const DEBATE_LABELS: Record<string, string> = {
  investment: "多空辩论",
  risk: "风险讨论",
};

export const nodeLabel = (id: string | null | undefined) => (id ? NODE_LABELS[id] ?? id : "");
export const ratingLabel = (rating: string | null | undefined) =>
  rating ? RATING_LABELS[rating] ?? rating : "";
