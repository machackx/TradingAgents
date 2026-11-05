# 评级与固定材料对照

## 统一的评级口径

新运行按 `relative-alpha-v1` 形成研究评级：相对于该市场的收益基准，在指定期限内预期跑赢还是跑输。美股默认 SPY、港股默认恒生指数；可通过原有 `benchmark_ticker`、`benchmark_map` 覆盖。

评级期限与收益复盘共用 `holding_period_days`，默认 5 个交易日。入场价是分析日期之后的第一个收盘价，退出价是入场之后再过指定数量交易日的收盘价。长期观点可以另写，但不能替代这次评级的期限。基准按相同的起止日期计算收益；这不是汇率对冲后的跨币种组合收益。

`expected_alpha_pct` 是股票预期收益减基准预期收益，单位为**百分点**，不是小数比例，也不是上涨概率。无法合理估计时留空，不能为满足格式编造收益预测。

默认档位如下，边界在程序中校验：

| 评级 | 预期超额收益 |
|---|---|
| Buy | ≥ +5 个百分点 |
| Overweight | > +2 且 < +5 个百分点 |
| Hold | -2 至 +2 个百分点，含边界 |
| Underweight | > -5 且 < -2 个百分点 |
| Sell | ≤ -5 个百分点 |

这些是可配置的工程初始值，**尚未经回测校准**，不代表模型能够准确估计这些收益。无数值预测时，经理必须解释相对收益方向和强度的证据；材料可用但没有明确方向时可选 Hold。重大数据矛盾导致无法判断时应输出 `evidence_status=insufficient`，最终进入 REVIEW。数值预测与评级冲突也进入 REVIEW，不由程序静默改成另一档。

`confidence` 独立标示 high / medium / low。数据缺失、等待确认、没有选择某位分析师或高波动本身，不能直接充当负超额收益证据。基本面分析须明确币种、期间、单位及每股／代表份额口径；无法勾稽的字段应说明影响范围。这一语义检查仍依赖模型，不等于对原始财报进行了自动审计。

研究评级与账户订单分开。未提供标准仓位时，不应编造“标准配置的 75%”等目标；可提供 `rating_standard_allocation_pct` 指定个人标准权重。真实买卖仍取决于已知持仓、目标、现金和风险约束。

## 减少初始建议的影响

默认两位经理各先进行一次只读分析师报告的独立判断，再阅读研究辩论、已有推荐或风险辩论，并说明改变判断的事实依据。独立判断随状态、断点和报告保存：

- `2_research/independent.md`
- `5_portfolio/independent.md`

每个完整运行因此增加两次经理模型调用，可用 `rating_independent_review=false` 关闭以做对照。激进、保守、中立风险分析师均可反对交易员的买卖方向；激进分析师不再被要求为交易员已有决定辩护。

## 技术指标修复

简单移动平均线要求完整窗口：不足 200 根日线时，`close_200_sma` 返回明确的 N/A，不再把已有日线的平均值称作 200 日均线。50 日均线及其他 `close_N_sma` 同样处理。批量、单日和核验快照共用计算入口；每个历史输出日期分别检查窗口，窗口内非有限的收盘价也不能组成完整均线。

## 配置示例

```dotenv
TRADINGAGENTS_HOLDING_PERIOD_DAYS=5
TRADINGAGENTS_RATING_NEUTRAL_ALPHA_PCT=2.0
TRADINGAGENTS_RATING_STRONG_ALPHA_PCT=5.0
TRADINGAGENTS_RATING_INDEPENDENT_REVIEW=true
TRADINGAGENTS_RESEARCH_DEBATE_ORDER=bull_first
# 可选；没有个人标准权重时保持未设置
#TRADINGAGENTS_RATING_STANDARD_ALLOCATION_PCT=5
```

中性阈值必须非负、严格小于强信号阈值；期限必须为正整数。Python 配置使用对应的小写键名。可将 `research_debate_order` 设为 `bear_first` 检查相反发言顺序。

评级规则、基准和期限写入报告及 `analysis_checks.json`。新决策的基准和期限还单独存入交易记忆的程序元数据；以后改变配置，旧决策仍按当时口径结算。没有元数据的旧记录保留原有结算行为。

重新启动 Web 服务或 CLI 后生效。新规则改变了断点签名，旧规则的断点不会混入新分析；已有报告和历史评级不重写。

## 固定材料对照工具

先用已保存的 `full_states_log_*.json` 规划实验：

```bash
python -m tradingagents.rating_audit \
  --output reports/rating_audit_plan.json \
  ~/.tradingagents/logs/AAPL/TradingAgentsStrategy_logs/full_states_log_2026-11-05.json
```

加 `--execute` 才会调用该报告所记录的研究经理模型。每个案例比较两种独立判断设置与两种辩论顺序，共四组、六次模型调用。交换的是每轮两个既有发言的呈现顺序，不重新生成论点，适合检查经理对材料顺序的敏感性，不能测量研究员因真实先后发言而产生的全部差异。

工具保存分析师材料的 SHA-256、明确的评测口径、每组完整决定及失败信息。它不请求行情、结算历史决策、覆盖原报告或写入用户交易记忆。历史评级只是参考，不是正确答案。该实验只重放研究经理这一最早出现低配的环节，不声称验证了完整新流程的投资表现。

评估改善时，应扩大到不同趋势、行业和市场阶段的样本，比较顺序敏感性、置信度校准和同期限实际收益。评级分布变得均匀本身不是成功标准。
