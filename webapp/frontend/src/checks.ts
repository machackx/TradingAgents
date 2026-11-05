import type { NodeState, RunState } from "./runState";

interface Check {
  status?: string;
  issues?: string[];
}

const MESSAGES: Record<string, string> = {
  "Material evidence contradictions or gaps prevent a defensible research rating.": "财务或其他关键证据存在尚未解决的矛盾或缺口，暂不能形成可靠评级。",
  "Research assessment has not passed validation.": "研究经理的评估未通过校验。",
  "Trader proposal has not passed programmatic validation.": "交易提议未通过程序校验。",
};

function check(run: RunState, node: string, key: string): Check | undefined {
  return run.nodes[node]?.update[key] as Check | undefined;
}

function failed(value: Check | undefined, success: string): boolean {
  return Boolean(value?.status && value.status !== success);
}

function issues(value: Check | undefined): string[] {
  return (value?.issues ?? []).map((text) => MESSAGES[text] ?? text);
}

export function researchRejected(node: NodeState | undefined): boolean {
  return failed(node?.update.research_validation as Check | undefined, "validated");
}

export function researchRating(run: RunState): string | undefined {
  const node = run.nodes["Research Manager"];
  if (researchRejected(node)) return "REVIEW";
  const plan = node?.structured.filter((entry) => entry.schema === "research_plan").slice(-1)[0]?.data;
  return plan?.recommendation == null ? undefined : String(plan.recommendation);
}

export function reviewReasons(run: RunState): string[] {
  const quality = check(run, "Evidence check", "data_quality");
  const research = check(run, "Research Manager", "research_validation");
  const trader = check(run, "Trader", "trader_validation");
  const portfolio = check(run, "Portfolio Manager", "portfolio_validation");
  const reasons = [
    ...(failed(quality, "adequate") ? issues(quality) : []),
    ...(failed(research, "validated") ? issues(research) : []),
    ...(failed(trader, "validated") ? issues(trader) : []),
  ];
  if (researchRejected(run.nodes["Research Manager"])) {
    const plan = run.nodes["Research Manager"]?.structured.filter((entry) => entry.schema === "research_plan").slice(-1)[0]?.data;
    if (plan?.evidence_status === "insufficient" && plan.executive_summary) {
      reasons.push(`研究经理：${String(plan.executive_summary)}`);
    }
  }
  if (failed(portfolio, "validated")) {
    // Older saved runs added this error even when upstream checks skipped Trader.
    const skippedTrader = !run.nodes.Trader?.startedAt &&
      (failed(quality, "adequate") || failed(research, "validated"));
    reasons.push(...issues(portfolio).filter((text) =>
      !(skippedTrader && text === MESSAGES["Trader proposal has not passed programmatic validation."])));
  }
  return [...new Set(reasons)];
}

export function skipReason(run: RunState, nodeId: string): string {
  if (run.status === "cancelled") return "分析已取消，流程未进入这一步。";
  if (run.status === "failed" || run.status === "interrupted") return "分析中断，流程未进入这一步。";
  const quality = check(run, "Evidence check", "data_quality");
  if (failed(quality, "adequate")) {
    return `证据检查未通过，流程跳过后续研究、交易与风控，本次结果为“待复核”。${issues(quality).join("；")}`;
  }
  const phase = run.nodes[nodeId]?.phase;
  if ((phase === "trading" || phase === "risk") && researchRejected(run.nodes["Research Manager"])) {
    return `研究经理的评估未通过校验，交易与风控未执行，本次结果为“待复核”。${issues(check(run, "Research Manager", "research_validation")).join("；")}`;
  }
  const trader = check(run, "Trader", "trader_validation");
  if (phase === "risk" && failed(trader, "validated")) {
    return `交易提议未通过程序校验，风险讨论未执行，本次结果为“待复核”。${issues(trader).join("；")}`;
  }
  return "流程未进入这一步。";
}
