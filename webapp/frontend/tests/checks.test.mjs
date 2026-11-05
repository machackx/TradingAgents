import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

// Exercise the same TypeScript helpers used by the page, without a browser dependency.
const source = await readFile(new URL("../src/checks.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
});
const { reviewReasons, researchRating, skipReason } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`
);

const researchIssue = "Material evidence contradictions or gaps prevent a defensible research rating.";
const phantomTraderIssue = "Trader proposal has not passed programmatic validation.";
function fixture() {
  return {
    status: "finished",
    nodes: {
      "Evidence check": { update: { data_quality: { status: "adequate", issues: [] } } },
      "Research Manager": {
        update: { research_validation: { status: "invalid", issues: [researchIssue] } },
        structured: [{ schema: "research_plan", data: {
          recommendation: "Hold", evidence_status: "insufficient", executive_summary: "收入与毛利口径冲突。",
        } }],
      },
      Trader: { phase: "trading", update: {} },
      "Aggressive Analyst": { phase: "risk", update: {} },
      "Portfolio Manager": { update: { portfolio_validation: {
        status: "invalid", issues: [phantomTraderIssue, researchIssue],
      } } },
    },
  };
}

test("research conflicts explain skipped trading and risk without blaming passed evidence checks", () => {
  const run = fixture();
  for (const node of ["Trader", "Aggressive Analyst"]) {
    const reason = skipReason(run, node);
    assert.match(reason, /研究经理的评估未通过校验/);
    assert.doesNotMatch(reason, /证据检查未通过|交易提议未通过/);
  }
  assert.equal(researchRating(run), "REVIEW");
  assert.deepEqual(reviewReasons(run), [
    "财务或其他关键证据存在尚未解决的矛盾或缺口，暂不能形成可靠评级。",
    "研究经理：收入与毛利口径冲突。",
  ]);
});

test("evidence failure and actual trader failure report their own causes", () => {
  const run = fixture();
  run.nodes["Evidence check"].update.data_quality = { status: "insufficient_data", issues: ["news missing"] };
  assert.match(skipReason(run, "Trader"), /证据检查未通过.*news missing/);
  run.nodes["Evidence check"].update.data_quality = { status: "adequate" };
  run.nodes["Research Manager"].update.research_validation = { status: "validated" };
  run.nodes.Trader.startedAt = 1;
  run.nodes.Trader.update.trader_validation = { status: "invalid", issues: ["cash insufficient"] };
  assert.match(skipReason(run, "Aggressive Analyst"), /交易提议未通过程序校验.*cash insufficient/);
  assert.ok(reviewReasons(run).includes("cash insufficient"));
  assert.ok(reviewReasons(run).includes("交易提议未通过程序校验。"));
  assert.equal(researchRating(run), "Hold");
});

test("cancelled and failed runs do not claim a review verdict from evidence checks", () => {
  const run = fixture();
  run.status = "cancelled";
  assert.equal(skipReason(run, "Trader"), "分析已取消，流程未进入这一步。");
  run.status = "failed";
  assert.equal(skipReason(run, "Trader"), "分析中断，流程未进入这一步。");
});

test("portfolio-only validation issues remain visible", () => {
  const run = fixture();
  run.nodes["Research Manager"].update.research_validation = { status: "validated" };
  run.nodes["Portfolio Manager"].update.portfolio_validation.issues = ["Invalid price target"];
  assert.deepEqual(reviewReasons(run), ["Invalid price target"]);
});
