import { fmtClock, fmtCount, fmtDuration, shortJson } from "../format";
import {
  ANALYST_LABELS,
  DEBATE_LABELS,
  FIELD_LABELS,
  NODE_STATUS_LABELS,
  PREFETCH_SOURCE_LABELS,
  ROLE_LABELS,
  SCHEMA_LABELS,
  SECTION_LABELS,
  STEP_LABELS,
  nodeLabel,
} from "../labels";
import {
  debateTurns,
  displayStatus,
  nodeElapsed,
  type LlmCall,
  type NodeState,
  type NodeStepRun,
  type RunState,
  type ToolCall,
} from "../runState";
import { NodeStatusDot, RatingBadge } from "./Badges";
import { Markdown } from "./Markdown";
import { ConfidencePill, DivergingScoreBar, StancePill } from "./ViewpointDashboard";
import { extractResearcherTurnViewpoint, parseReportViewpoint } from "../viewpoint";

const DEBATE_OF: Record<string, string> = {
  "Bull Researcher": "investment",
  "Bear Researcher": "investment",
  "Aggressive Analyst": "risk",
  "Conservative Analyst": "risk",
  "Neutral Analyst": "risk",
};
const JUDGE_OF: Record<string, string> = { "Research Manager": "investment", "Portfolio Manager": "risk" };
const SPEAKER_PREFIX = /^(Bull|Bear|Aggressive|Conservative|Neutral) Analyst:\s*/;
const RATING_FIELDS = new Set(["recommendation", "action", "rating"]);

interface Props {
  run: RunState;
  nodeId: string;
  now: number;
}

export function NodeDetail({ run, nodeId, now }: Props) {
  const n = run.nodes[nodeId];
  if (!n) return null;
  const status = displayStatus(run, n);
  const elapsed = nodeElapsed(n, now);
  const tokensIn = n.llm.reduce((s, c) => s + c.tokensIn, 0);
  const tokensOut = n.llm.reduce((s, c) => s + c.tokensOut, 0);
  const models = [...new Set(n.llm.map((c) => c.model).filter(Boolean))].join("、");
  const debate = DEBATE_OF[nodeId];
  const judged = JUDGE_OF[nodeId];
  const topRuns = n.stepRuns.filter((sr) => sr.step == null);
  const subSteps = n.stepRuns.filter((sr) => sr.step != null);
  const empty =
    !n.liveThinking &&
    !n.liveText &&
    !n.reports.length &&
    !n.turns.length &&
    !n.structured.length &&
    !n.tools.length &&
    !Object.keys(n.update).length;

  return (
    <section className="panel detail" aria-label={`${nodeLabel(nodeId)} 详情`}>
      <header className="detail-head">
        <h2>
          <NodeStatusDot status={status} /> {nodeLabel(nodeId)}
          <span className="muted small"> {nodeId}</span>
        </h2>
        <div className="detail-meta small muted">
          <span>{NODE_STATUS_LABELS[status]}</span>
          {status === "running" && n.step && <span>{STEP_LABELS[n.step] ?? n.step}</span>}
          {topRuns.length > 1 && <span>共执行 {topRuns.length} 轮</span>}
          {elapsed != null && <span>耗时 {fmtDuration(elapsed)}</span>}
          {n.llm.length > 0 && (
            <span>
              模型调用 {n.llm.length} 次 · 输入 {fmtCount(tokensIn)} / 输出 {fmtCount(tokensOut)} tokens
              {models && ` · ${models}`}
            </span>
          )}
        </div>
      </header>

      {n.error && <p className="error">节点出错：{n.error}</p>}
      {status === "pending" && <p className="muted">尚未开始。</p>}
      {status === "skipped" && (
        <p className="muted">
          这一步没有执行。证据检查判定资料不足或交易提议未通过程序校验时，流程会跳过中间环节，直接由投资组合经理给出“待复核”。
        </p>
      )}
      {status === "stopped" && <p className="muted">运行在这一步进行中时结束了（取消或出错）。</p>}

      {n.liveThinking && (
        <article className="live live-thinking">
          <h3>
            正在深度思考 <span className="caret" aria-hidden />
          </h3>
          <pre className="code thinking-stream">{n.liveThinking}</pre>
        </article>
      )}

      {n.liveText && (
        <article className="live">
          <h3>
            正在输出 <span className="caret" aria-hidden />
          </h3>
          <Markdown text={n.liveText} />
        </article>
      )}

      {(subSteps.length > 0 || topRuns.length > 1) && (
        <ExecutionTimeline node={n} steps={subSteps.length > 0 ? subSteps : topRuns} now={now} />
      )}

      {n.structured.map((s, i) => (
        <StructuredCard key={`${s.schema}-${i}`} schema={s.schema} data={s.data} />
      ))}

      {debate && <Conversation run={run} debate={debate} highlight={nodeId} />}

      {!debate &&
        n.reports.map((r) => {
          // A structured agent's report repeats its card above; keep it at hand, folded.
          if (n.structured.length) {
            return (
              <details key={r.section} className="block">
                <summary>{SECTION_LABELS[r.section] ?? r.section}原文</summary>
                <Markdown text={r.content} />
              </details>
            );
          }
          const vp = parseReportViewpoint(r.content);
          const hasSummaryCard = Boolean(vp.stance || vp.score != null || vp.executiveSummary || vp.keyTakeaways.length);
          return (
            <article key={r.section} className="report">
              <h3>{SECTION_LABELS[r.section] ?? r.section}</h3>
              {hasSummaryCard && (
                <div className="vp-detail-summary">
                  <div className="vp-detail-summary-head">
                    <strong>核心观点简述 (Executive Summary)</strong>
                    <div className="vp-card-badges">
                      {vp.stance && <StancePill stance={vp.stance} />}
                      <ConfidencePill confidence={vp.confidence} />
                    </div>
                  </div>
                  {vp.score != null && <DivergingScoreBar score={vp.score} stance={vp.stance} label="维度信号评分" />}
                  {vp.executiveSummary && <p className="vp-exec-text">{vp.executiveSummary}</p>}
                  {vp.keyTakeaways.length > 0 && (
                    <div className="vp-takeaways">
                      <div className="vp-box-label">核心要点</div>
                      <ul>
                        {vp.keyTakeaways.map((pt, i) => (
                          <li key={i}>{pt}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
              <Markdown text={hasSummaryCard && vp.bodyMarkdown ? vp.bodyMarkdown : r.content} />
            </article>
          );
        })}

      {judged && debateTurns(run, judged).length > 0 && (
        <details className="block">
          <summary>{DEBATE_LABELS[judged]}全文（{debateTurns(run, judged).length} 次发言）</summary>
          <Conversation run={run} debate={judged} />
        </details>
      )}

      {n.agentMessages.length > 0 && (
        <details className="block" open={n.agentMessages.some((m) => !!m.reasoning)}>
          <summary>调用工具时的思考与说明（{n.agentMessages.length}）</summary>
          <div className="agent-messages">
            {n.agentMessages.map((m, i) => (
              <div key={i} className="agent-message-item">
                <div className="muted small">
                  第 {i + 1} 次说明 · {fmtClock(m.ts)}
                  {m.step && ` · ${STEP_LABELS[m.step] ?? m.step}`}
                </div>
                {m.reasoning && (
                  <details className="thinking-box" open>
                    <summary className="small">思考链（Reasoning）</summary>
                    <pre className="code">{m.reasoning}</pre>
                  </details>
                )}
                {m.content && <p className="small">{m.content}</p>}
              </div>
            ))}
          </div>
        </details>
      )}

      {n.tools.length > 0 && <ToolList tools={n.tools} />}
      <UpdateView node={n} />
      {n.llm.length > 0 && <LlmTable node={n} />}
      {empty && status === "running" && <p className="muted">正在等待第一步的结果…</p>}
    </section>
  );
}

/** Step-by-step execution trace inside an analyst subgraph or multi-round debater. */
function ExecutionTimeline({ node, steps, now }: { node: NodeState; steps: NodeStepRun[]; now: number }) {
  return (
    <details className="block step-timeline-panel" open={node.status === "running" || steps.some((s) => s.step === "wrap_up")}>
      <summary>
        执行步骤时间线（{steps.length} 步）
        {steps.some((s) => s.step === "wrap_up") && <span className="tag tag-unusable">触发工具轮数上限收尾</span>}
      </summary>
      <ol className="step-timeline">
        {steps.map((sr, i) => {
          const running = sr.finishedAt == null && node.status === "running";
          const dur = sr.duration ?? (running ? Math.max(0, now - sr.startedAt) : sr.finishedAt ? sr.finishedAt - sr.startedAt : null);
          const endTs = sr.finishedAt ?? Infinity;
          // Match tools and LLM calls initiated within this step's window
          const stepTools = node.tools.filter(
            (t) => (sr.step == null || t.step === sr.step || (sr.step === "tools" && t.step === "agent")) &&
              t.ts >= sr.startedAt - 0.05 && t.ts <= endTs + 0.05,
          );
          const stepLlms = node.llm.filter(
            (c) => (sr.step == null || c.step === sr.step) && c.ts >= sr.startedAt - 0.05 && c.ts <= endTs + 0.05,
          );
          const stepMsgs = node.agentMessages.filter(
            (m) => (sr.step == null || m.step === sr.step) && m.ts >= sr.startedAt - 0.05 && m.ts <= endTs + 0.05,
          );
          const stepLabel = sr.step ? STEP_LABELS[sr.step] ?? sr.step : `第 ${i + 1} 轮发言`;

          return (
            <li key={sr.taskId || i} className={`step-item${running ? " step-running" : ""}${sr.error ? " step-error" : ""}`}>
              <div className="step-head">
                <span className="step-badge">{i + 1}</span>
                <strong>{stepLabel}</strong>
                {sr.step && <code className="muted">{sr.step}</code>}
                <span className="muted small">{fmtClock(sr.startedAt)}</span>
                {dur != null && <span className="pill">{fmtDuration(dur)}</span>}
                {running && <span className="pill pill-running">进行中</span>}
                {sr.error && <span className="tag tag-error">{sr.error}</span>}
              </div>
              {sr.step === "wrap_up" && (
                <p className="small warn">
                  已达到单节点工具调用最大轮数上限，系统注入收尾指令要求智能体停止调用工具并立即撰写最终报告。
                </p>
              )}
              {(stepLlms.length > 0 || stepTools.length > 0 || stepMsgs.length > 0) && (
                <div className="step-body small">
                  {stepLlms.map((c, idx) => (
                    <div key={idx} className="step-meta-line muted">
                      模型 <code>{c.model ?? "default"}</code> · 输入 {fmtCount(c.tokensIn)} / 输出 {fmtCount(c.tokensOut)} tokens
                      {c.duration != null && ` · ${fmtDuration(c.duration)}`}
                      {c.reasoning && <span className="tag tag-ok">含思考链</span>}
                    </div>
                  ))}
                  {stepMsgs.map((m, idx) =>
                    m.content ? (
                      <div key={idx} className="step-msg">
                        💬 {m.content}
                      </div>
                    ) : null,
                  )}
                  {stepTools.length > 0 && (
                    <div className="step-tools">
                      🔧 调用：
                      {stepTools.map((t, idx) => (
                        <code key={idx} className="step-tool-chip">
                          {t.name}
                          {t.duration != null ? ` (${fmtDuration(t.duration)})` : ""}
                        </code>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </details>
  );
}

function StructuredCard({ schema, data }: { schema: string; data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(
    ([, v]) => v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && v.length === 0),
  );
  const short = entries.filter(([, v]) => !Array.isArray(v) && (typeof v !== "string" || v.length <= 60));
  const lists = entries.filter(([, v]) => Array.isArray(v));
  const long = entries.filter(([, v]) => typeof v === "string" && v.length > 60);
  const scoreVal = typeof data.overall_score === "number" ? data.overall_score : null;
  const bandVal = typeof data.overall_band === "string" ? data.overall_band : null;
  return (
    <article className="structured">
      <h3>{SCHEMA_LABELS[schema] ?? schema}</h3>
      {scoreVal != null && <DivergingScoreBar score={scoreVal} stance={bandVal as any} label="情绪量化分" />}
      <dl className="fields">
        {short.map(([key, value]) => (
          <div key={key}>
            <dt>{FIELD_LABELS[key] ?? key}</dt>
            <dd>
              {RATING_FIELDS.has(key) ? (
                <RatingBadge rating={String(value)} />
              ) : key === "overall_band" ? (
                <StancePill stance={String(value)} />
              ) : (
                String(value)
              )}
            </dd>
          </div>
        ))}
      </dl>
      {lists.map(([key, value]) => (
        <div key={key} className="field-long">
          <h4>{FIELD_LABELS[key] ?? key}</h4>
          <ul className="small">
            {(value as unknown[]).map((item, idx) => (
              <li key={idx}>{String(item)}</li>
            ))}
          </ul>
        </div>
      ))}
      {long.map(([key, value]) => (
        <div key={key} className="field-long">
          <h4>{FIELD_LABELS[key] ?? key}</h4>
          <Markdown text={String(value)} />
        </div>
      ))}
    </article>
  );
}

function Conversation({ run, debate, highlight }: { run: RunState; debate: string; highlight?: string }) {
  const turns = debateTurns(run, debate);
  if (!turns.length) return null;
  return (
    <article className="conversation">
      <h3>{DEBATE_LABELS[debate] ?? debate}</h3>
      {turns.map((t) => {
        const vp = debate === "investment" ? extractResearcherTurnViewpoint(t) : null;
        return (
          <div
            key={t.seq}
            className={`turn turn-${t.node.split(" ")[0].toLowerCase()}${t.node === highlight ? " mine" : ""}`}
          >
            <div className="turn-head small">
              <strong>{nodeLabel(t.node)}</strong>
              {vp?.stance && <StancePill stance={vp.stance} />}
              {vp?.conviction != null && <span className="pill">信心 {vp.conviction.toFixed(1)}/10</span>}
              <span className="muted">
                第 {t.turn} 次发言 · {fmtClock(t.ts)}
              </span>
            </div>
            {vp && (vp.executiveSummary || vp.keyArguments.length > 0) && (
              <div className="vp-turn-summary">
                {vp.executiveSummary && (
                  <p className="vp-exec-text">
                    <strong>核心观点：</strong>
                    {vp.executiveSummary}
                  </p>
                )}
                {vp.keyArguments.length > 0 && (
                  <ul className="small">
                    {vp.keyArguments.map((arg, idx) => (
                      <li key={idx}>{arg}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
            <Markdown text={vp?.bodyMarkdown ? vp.bodyMarkdown : t.content.replace(SPEAKER_PREFIX, "")} />
          </div>
        );
      })}
    </article>
  );
}

function ToolList({ tools }: { tools: ToolCall[] }) {
  return (
    <article className="block">
      <h3>工具调用（{tools.length}）</h3>
      <ul className="tools">
        {tools.map((t, i) => {
          const state = !t.result ? "pending" : t.result.status === "error" ? "error" : t.result.usable ? "ok" : "unusable";
          const label = { pending: "等待返回", error: "出错", ok: "可用", unusable: "无可用数据" }[state];
          return (
            <li key={`${t.callId}-${i}`}>
              <details>
                <summary>
                  <span className="muted small">#{i + 1}</span>
                  <code>{t.name}</code>
                  <span className="muted small args">{shortJson(t.args, 70)}</span>
                  <span className={`tag tag-${state}`}>{label}</span>
                  {t.duration != null && <span className="muted small">{fmtDuration(t.duration)}</span>}
                  {t.result && <span className="muted small">{fmtCount(t.result.chars)} 字</span>}
                  <span className="muted small">{fmtClock(t.ts)}</span>
                </summary>
                <div className="tool-detail-body">
                  <div className="small muted">调用参数：</div>
                  <pre className="code">{JSON.stringify(t.args, null, 2)}</pre>
                  {t.result && (
                    <>
                      <div className="small muted">
                        返回内容（{fmtClock(t.result.ts)}，共 {fmtCount(t.result.chars)} 字）：
                      </div>
                      <pre className="code result">{t.result.content}</pre>
                      {t.result.truncated && <p className="muted small">内容过长，只保留了前面部分。</p>}
                    </>
                  )}
                </div>
              </details>
            </li>
          );
        })}
      </ul>
    </article>
  );
}

function UpdateView({ node }: { node: NodeState }) {
  const u = node.update as Record<string, any>;
  const keys = Object.keys(u).filter((k) => k !== "final_rating");
  if (!keys.length) return null;
  return (
    <article className="block">
      <h3>写入运行状态</h3>
      {keys.map((key) => {
        const value = u[key];
        if (key === "market_data") return <MarketData key={key} data={value} />;
        if (key === "data_quality") return <DataQuality key={key} data={value} />;
        if (key === "analyst_evidence") return <Evidence key={key} data={value} />;
        if (key === "trader_validation") return <Validation key={key} data={value} />;
        if (key === "memory_settlement") return <MemorySettlementView key={key} data={value} />;
        if (key === "memory_note")
          return value ? (
            <p key={key} className="warn">
              {value}
            </p>
          ) : null;
        if (key === "past_context")
          return (
            <details key={key}>
              <summary>历史经验（供投资组合经理参考）</summary>
              {value ? <pre className="code">{value}</pre> : <p className="muted">这只股票还没有已结算的历史决策。</p>}
            </details>
          );
        return (
          <details key={key}>
            <summary>
              <code>{key}</code>
            </summary>
            <pre className="code">{JSON.stringify(value, null, 2)}</pre>
          </details>
        );
      })}
    </article>
  );
}

function MarketData({ data }: { data: Record<string, any> }) {
  if (!data || data.status !== "available") {
    return <p className="warn">行情快照不可用{data?.reason ? `（${data.reason}）` : ""}。</p>;
  }
  const records: Record<string, any>[] = data.records ?? [];
  const last = records[records.length - 1];
  return (
    <dl className="fields">
      <div>
        <dt>代码</dt>
        <dd>{data.symbol}</dd>
      </div>
      <div>
        <dt>最新日线</dt>
        <dd>
          {data.latest_bar_date}
          {last?.Close != null && ` · 收盘 ${Number(last.Close).toFixed(2)} ${data.quote_currency ?? ""}`}
        </dd>
      </div>
      <div>
        <dt>日线条数</dt>
        <dd>{records.length}</dd>
      </div>
      <div>
        <dt>数据来源</dt>
        <dd>{data.source}</dd>
      </div>
      <div>
        <dt>快照 ID</dt>
        <dd>
          <code>{data.snapshot_id}</code>
        </dd>
      </div>
      <div>
        <dt>K 线状态</dt>
        <dd>{data.bar_status}</dd>
      </div>
    </dl>
  );
}

const COVERAGE: Record<string, string> = { available: "充足", missing: "缺失", not_applicable: "不适用" };

function DataQuality({ data }: { data: Record<string, any> }) {
  if (!data) return null;
  return (
    <div>
      <p className={data.status === "adequate" ? "ok" : "warn"}>
        {data.status === "adequate" ? "每位分析师都拿到了可用的数据。" : "资料不足，本次不会给出可执行的评级。"}
      </p>
      <dl className="fields">
        {Object.entries(data.coverage ?? {}).map(([key, value]) => (
          <div key={key}>
            <dt>{ANALYST_LABELS[key] ?? key}</dt>
            <dd className={value === "missing" ? "bad" : ""}>{COVERAGE[String(value)] ?? String(value)}</dd>
          </div>
        ))}
      </dl>
      {(data.issues ?? []).length > 0 && (
        <ul className="small">
          {data.issues.map((issue: string) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface EvidenceEntry {
  attempted: number;
  usable: number;
  chars?: number;
  content?: string;
  truncated?: boolean;
}

function Evidence({ data }: { data: Record<string, Record<string, EvidenceEntry>> }) {
  const rows = Object.entries(data ?? {}).flatMap(([analyst, tools]) =>
    Object.entries(tools).map(([tool, counts]) => ({ analyst, tool, ...counts })),
  );
  if (!rows.length) return null;
  const withContent = rows.filter((r) => typeof r.content === "string" && r.content.length > 0);
  return (
    <div>
      <table className="table small">
        <thead>
          <tr>
            <th>数据来源</th>
            <th>尝试</th>
            <th>可用</th>
            {withContent.length > 0 && <th>字数</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.analyst}-${r.tool}`} className={r.usable ? "" : "bad"}>
              <td>
                <code>{r.tool}</code>
                {PREFETCH_SOURCE_LABELS[r.tool] && <span className="muted"> · {PREFETCH_SOURCE_LABELS[r.tool]}</span>}
              </td>
              <td>{r.attempted}</td>
              <td>{r.usable}</td>
              {withContent.length > 0 && <td>{r.chars != null ? `${fmtCount(r.chars)} 字` : "—"}</td>}
            </tr>
          ))}
        </tbody>
      </table>

      {withContent.length > 0 && (
        <div className="prefetched-sources">
          <h4>预取语料与 Jev 筛选明细（{withContent.length} 项）</h4>
          <ul className="tools">
            {withContent.map((r) => {
              const firstLine = (r.content ?? "").split("\n")[0] ?? "";
              const jevNote = firstLine.startsWith("Screened by Jev:") || firstLine.startsWith("<Jev screening") ? firstLine : null;
              return (
                <li key={`corpus-${r.analyst}-${r.tool}`}>
                  <details>
                    <summary>
                      <code>{r.tool}</code>
                      <span>{PREFETCH_SOURCE_LABELS[r.tool] ?? r.tool}</span>
                      <span className={`tag tag-${r.usable ? "ok" : "unusable"}`}>{r.usable ? "可用" : "无可用数据"}</span>
                      {r.chars != null && <span className="muted small">{fmtCount(r.chars)} 字</span>}
                      {jevNote && <span className="pill">{jevNote}</span>}
                    </summary>
                    <pre className="code result">{r.content}</pre>
                    {r.truncated && <p className="muted small">内容过长，只保留了前面部分。</p>}
                  </details>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

function Validation({ data }: { data: Record<string, any> }) {
  if (!data) return null;
  const label = { validated: "通过程序校验", invalid: "未通过程序校验", unparsed: "无法解析" }[data.status as string] ?? data.status;
  return (
    <div>
      <p className={data.status === "validated" ? "ok" : "warn"}>交易提议{label}。</p>
      {(data.issues ?? []).length > 0 && (
        <ul className="small bad">
          {data.issues.map((x: string) => (
            <li key={x}>{x}</li>
          ))}
        </ul>
      )}
      {(data.notes ?? []).length > 0 && (
        <ul className="small muted">
          {data.notes.map((x: string) => (
            <li key={x}>{x}</li>
          ))}
        </ul>
      )}
      {data.raw_proposal && (
        <details className="block">
          <summary>查看被程序校验拦截前的模型原始提议</summary>
          <Markdown text={String(data.raw_proposal)} />
        </details>
      )}
    </div>
  );
}

function MemorySettlementView({ data }: { data: Record<string, any> }) {
  if (!data) return null;
  const settled: Record<string, any>[] = data.settled ?? [];
  const failed: Record<string, any>[] = data.failed ?? [];
  if (!settled.length && !failed.length && !data.error) {
    return <p className="muted small">本次运行没有需要新结算的到期历史决策。</p>;
  }
  return (
    <div className="memory-settlement">
      {data.error && <p className="warn small">结算出错：{data.error}</p>}
      {settled.length > 0 && (
        <details open>
          <summary>本次新结算的历史决策（{settled.length} 条）</summary>
          <ul className="tools">
            {settled.map((item, idx) => (
              <li key={`${item.ticker}-${item.date}-${idx}`}>
                <details open={settled.length === 1}>
                  <summary>
                    <strong>{item.ticker}</strong>
                    <span className="muted small">{item.date}</span>
                    {item.rating && <RatingBadge rating={String(item.rating)} />}
                    {item.raw_return != null && (
                      <span className="small">
                        {item.holding_days ?? 5} 日收益 {(Number(item.raw_return) * 100).toFixed(2)}%
                      </span>
                    )}
                    {item.alpha_return != null && (
                      <span className="pill">
                        Alpha (vs {item.benchmark ?? "SPY"}): {(Number(item.alpha_return) * 100).toFixed(2)}%
                      </span>
                    )}
                  </summary>
                  {item.reflection && <pre className="code">{item.reflection}</pre>}
                </details>
              </li>
            ))}
          </ul>
        </details>
      )}
      {failed.length > 0 && (
        <details open>
          <summary className="bad">未能结算的决策（{failed.length} 条，保持待结算）</summary>
          <ul className="small bad">
            {failed.map((f, idx) => (
              <li key={`${f.ticker}-${f.date}-${idx}`}>
                <strong>{f.ticker}</strong> ({f.date})：{f.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function LlmTable({ node }: { node: NodeState }) {
  return (
    <details className="block">
      <summary>模型调用与 Prompt 明细（{node.llm.length}）</summary>
      <table className="table small">
        <thead>
          <tr>
            <th>#</th>
            <th>结束时间</th>
            <th>步骤</th>
            <th>模型</th>
            <th>输入</th>
            <th>输出</th>
            <th>耗时</th>
          </tr>
        </thead>
        <tbody>
          {node.llm.map((c, i) => (
            <LlmRow key={i} index={i} call={c} />
          ))}
        </tbody>
      </table>
    </details>
  );
}

function LlmRow({ index, call: c }: { index: number; call: LlmCall }) {
  const hasInspect = (c.prompts && c.prompts.length > 0) || !!c.reasoning;
  return (
    <>
      <tr className={c.error ? "bad" : ""}>
        <td>{index + 1}</td>
        <td>{fmtClock(c.ts)}</td>
        <td>{c.step ? STEP_LABELS[c.step] ?? c.step : "—"}</td>
        <td>
          <code>{c.model ?? "—"}</code>
          {c.reasoning && <span className="tag tag-ok">思考链</span>}
        </td>
        <td>{fmtCount(c.tokensIn)}</td>
        <td>{fmtCount(c.tokensOut)}</td>
        <td>{c.error ? c.error : fmtDuration(c.duration)}</td>
      </tr>
      {hasInspect && (
        <tr className="llm-inspect-row">
          <td colSpan={7}>
            <div className="llm-inspect-box">
              {c.reasoning && (
                <details className="thinking-box">
                  <summary className="small">查看模型深度思考过程（Reasoning / CoT，{fmtCount(c.reasoning.length)} 字）</summary>
                  <pre className="code">{c.reasoning}</pre>
                </details>
              )}
              {c.prompts && c.prompts.length > 0 && (
                <details>
                  <summary className="small">查看发送给模型的完整 Prompt（{c.prompts.length} 条消息）</summary>
                  <div className="prompt-messages">
                    {c.prompts.map((pm, idx) => (
                      <div key={idx} className="prompt-msg">
                        <div className="prompt-msg-head small">
                          <span className="pill">{ROLE_LABELS[pm.role] ?? pm.role}</span>
                          <span className="muted">{fmtCount(pm.chars ?? pm.content.length)} 字</span>
                          {pm.truncated && <span className="muted">（已截断）</span>}
                        </div>
                        <pre className="code">{pm.content}</pre>
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
