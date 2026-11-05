# TradingAgents 本地体验 Playbook

适用：本机 TradingAgents 0.7.1，核对日期 2026-11-03（Asia/Shanghai）。

已配置 ChatGPT 订阅、GPT-6-Luna（分析师等）和 GPT-6-Sol（研究/组合经理）、中文报告、研究和风险讨论各一轮。原生界面是终端 CLI。

建议顺序：先读已有报告 → 四分析师完整分析 → 持仓对比 → 三个历史日期的回测。加深讨论、多市场和断点恢复可选。

除阅读已有文件外，下面的分析和回测命令都会使用 ChatGPT 订阅额度。这里准备了操作步骤和虚构持仓，没有自动执行这些新增分析或回测。

## 数据从哪里来

| 数据 | 当前实现与来源 | 当前状态和边界 |
|---|---|---|
| 开高低收、成交量 | `yfinance` 读取 Yahoo Finance | 当前主要使用日线；已在 NVDA 示例验证。分析日期与实际最新行情日期可能不同，要读报告中的 `Latest trading row used`。 |
| 均线、RSI、MACD、布林带、ATR 等 | Yahoo 行情，在本地用 stockstats / pandas 计算 | `get_indicators` 和验证快照都已在示例中调用。 |
| 公司概况、行业、估值、市值等 | Yahoo `Ticker.info` | 当前快照；历史日期会主动屏蔽，避免把今天的估值放进过去的分析。 |
| 资产负债表、利润表、现金流量表 | SEC EDGAR Company Facts 优先，工具可用时再回退 Yahoo | SEC 覆盖的申报公司按申报日期取值；当前实现主要读取 US-GAAP。Yahoo 对缺少披露日期的历史报表会屏蔽。 |
| 公司及宏观新闻 | Yahoo 新闻；港股/A 股及已映射中概股额外读取富途新闻、公告检索和东方财富媒体搜索 | 多源补充保留发布时间、原文链接及可用的原始发布方；近期搜索样本不等于完整历史档案。详见 [中文信源说明](CHINESE_SOURCES.zh-CN.md)。 |
| 舆情 | 配置的新闻来源、StockTwits、Reddit；对应中国市场额外读取牛牛社区 | 牛牛帖子标为个人观点，按实际样本分析，不编造点赞或多空比例。接口失败或历史覆盖不足会显示不可用。 |
| 宏观指标 | 圣路易斯联储 FRED | 当前未配置 `FRED_API_KEY`，这部分会报告不可用。 |
| 事件隐含概率 | Polymarket Gamma 接口 | 新闻分析师可以读取；当前实现访问实时赔率，历史日期会主动屏蔽。 |

上次成功的 NVDA 示例仅选择了 `market` 分析师。日志中实际调用的是 `get_stock_data`、`get_indicators`、`get_verified_market_snapshot`；财报、新闻和舆情链路尚未在本机做完整运行验证。

价格口径也要留意：当前 yfinance 日线使用 `auto_adjust=True`，技术指标缓存明确采用这个自动调整口径。与券商价格或其他行情软件对照时，应先核对复权设置。

`Verified market data snapshot` 同样基于 Yahoo 行情，由本地代码计算具体价格和指标，用来约束模型的数值引用。

当前未配置 Alpha Vantage 替代数据源，也未配置 TypeSafe/Jev 社交帖子筛选。ChatGPT 模型负责分析工具返回的数据，行情和财报的抓取由上述数据模块完成。

来源说明：[yfinance 官方文档](https://ranaroussi.github.io/yfinance/)说明它通过 Yahoo 的公开接口读取数据，是独立开源客户端；[SEC API 文档](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)说明公开财报 API 无需密钥；[FRED 密钥文档](https://fred.stlouisfed.org/docs/api/api_key.html)说明调用需要 API Key。Polymarket 接口以本地 `tradingagents/dataflows/vendors/polymarket.py` 为准。

## 0. 准备终端与阅读已有结果

所有命令先进入项目目录：

```sh
cd /Users/yangce/Projects/trading-agents
```

已有成功报告：

`/Users/yangce/.tradingagents/logs/reports/NVDA_20261103_200430/complete_report.html`

在 Finder 中打开这个文件，先看：市场分析 → 多空研究及研究经理结论 → 交易计划 → 风险讨论 → 组合经理最终评级。

最终评级有 Buy / Overweight / Hold / Underweight / Sell 五档；无法读取有效评级的结果记为 REVIEW。

默认输出位置：

- 完整 HTML / Markdown 报告：`~/.tradingagents/logs/reports/<代码_运行时间>/`
- 按代码和分析日期记录的工具日志：`~/.tradingagents/logs/<代码>/<分析日期>/message_tool.log`
- 日常决策与复盘：`~/.tradingagents/memory/trading_memory.md`
- 历史决策回测：`~/.tradingagents/logs/backtest/<run_id>/`

报告头部会记录模型、数据配置和日期。数据配置表示可使用的入口；实际调用情况以工具日志及各报告里的来源说明为准。舆情来源在模型调用前预先抓取，不一定以 `[Tool Call]` 出现在日志中。

## 1. 完整体验：四位分析师 + 多空讨论 + 风险评估

```sh
./start.command --no-checkpoint \
  --ticker NVDA --date 2026-11-03 \
  --analysts market,fundamentals,news,sentiment \
  --save --no-show --html
```

这里的 `2026-11-03` 是本次编写当天；以后体验时，请把这一步的日期改成当天，以便读取当前公司概况和估值。美股当日尚未收盘时，应检查实际使用的行情日期。

体验重点：

1. 四位分析师分别提供技术、财报、新闻和舆情证据。
2. 看多和看空研究员怎样解读同一组证据，研究经理怎样取舍。
3. Trader 如何把研究结论写成行动方案。
4. 激进、保守、中性风险角色怎样修正方案，组合经理最终如何评级。
5. 新闻的发布方和链接、财报的披露日期、舆情的可用性说明能否支持报告中的论据。

数据缺失时，重点看报告是否明确披露缺口。FRED 当前未配置；StockTwits、Reddit 也可能受网络或接口限制。每次分析由模型选择需要调用的工具，所有可选入口不一定都会被调用。

## 2. 持仓对比：空仓与已有持仓的建议有什么不同

已准备两个虚构账户：

- `examples/portfolios/flat.demo.json`：现金 25,000 USD，持仓为空。
- `examples/portfolios/nvda.demo.json`：现金 25,000 USD，持有 100 股 NVDA，平均成本 180 USD。

依次运行，保持标的、分析日期和分析师相同：

```sh
./start.command --no-checkpoint \
  --ticker NVDA --date 2026-11-02 --analysts market \
  --portfolio examples/portfolios/flat.demo.json \
  --save --no-show --html
```

```sh
./start.command --no-checkpoint \
  --ticker NVDA --date 2026-11-02 --analysts market \
  --portfolio examples/portfolios/nvda.demo.json \
  --save --no-show --html
```

比较 Trader 和组合经理如何处理现金、当前持股和仓位建议。模型输出会有随机变化，应比较理由和持仓约束，而不是要求两份技术分析逐字一致。

“未提供持仓”“明确空仓”“已有持仓”是三种不同输入。持仓信息来自你传入的 JSON；当前本地使用方式输出分析报告与建议，没有接入券商订单执行。

## 3. 加深讨论：一轮与两轮有什么区别（可选）

上次已有的一轮 NVDA 技术分析可作为对照。下面仅对这次进程增加讨论轮数，`.env` 保持原值：

```sh
TRADINGAGENTS_MAX_DEBATE_ROUNDS=2 \
TRADINGAGENTS_MAX_RISK_ROUNDS=2 \
./start.command --no-checkpoint \
  --ticker NVDA --date 2026-11-02 --analysts market \
  --save --no-show --html
```

观察第二轮是否回应了第一轮的反驳、是否补充了风险条件、最终评级为何变化或保持不变。增加轮数会增加模型调用；轮数多不等于结论必然更准确。

## 4. 多市场：任选一个体验（可选）

先使用 `market` 分析师观察代码识别、行情和技术分析。数据覆盖取决于 Yahoo。

港股：

```sh
./start.command --no-checkpoint --ticker 0700.HK --date 2026-11-03 \
  --analysts market --save --no-show --html
```

A 股：

```sh
./start.command --no-checkpoint --ticker 600519.SS --date 2026-11-03 \
  --analysts market --save --no-show --html
```

加密资产：

```sh
./start.command --no-checkpoint --ticker BTC-USD --date 2026-11-03 \
  --analysts market --save --no-show --html
```

留意币种、交易日和价格单位。非 SEC 覆盖公司的财报通常依赖 Yahoo；加密资产可使用市场、新闻和舆情分析。当前日期请按实际体验当天修改。

## 5. 小规模历史决策回测：一只股票 × 三个日期

```sh
./start.command backtest NVDA \
  --start 2026-09-01 --end 2026-09-15 --every 7 \
  --analysts market
```

日期网格为 2026-09-01、2026-09-08、2026-09-15，共三个分析任务，另有事后结算和复盘调用。先只用市场分析，可以减少历史新闻/社交覆盖不足对体验的干扰。

终端重点看：

- `Resolved / pending / unscored`：已结算、尚未结算、需要复核的决策数。
- 各评级的样本数、方向命中率、平均 alpha。
- `run_id` 与 `Log` 路径。

默认从分析日之后第一个收盘价起，观察后续 5 个交易日的变化；NVDA 默认与 SPY 比较。方向命中率以 alpha 方向和评级方向是否一致计算，Hold 不声称方向。这里按独立决策评分，未建模手续费、滑点及组合资金曲线；三个日期用于体验流程，统计意义有限。

继续同一次回测时，复制实际输出的 ID，替换下面的 `YOUR_RUN_ID`：

```sh
./start.command backtest NVDA \
  --start 2026-09-01 --end 2026-09-15 --every 7 \
  --analysts market --run-id YOUR_RUN_ID
```

同一个 `run_id` 会跳过已记入该次日志的分析，并尝试结算仍未完成的决策。回测使用自己的记忆日志，结果可在打印的 `Log` 文件中查看。CLI 的这条回测命令主要保存决策日志和统计，不会为每个日期自动输出第 1 步那样的完整 HTML 报告。

## 6. 记忆与断点恢复（可选）

记忆：打开日常或回测的 `trading_memory.md`，查看评级、理由、实现收益、alpha 和复盘。持有观察窗口走完后，后续运行会尝试结算旧决策，并把相关历史决策与复盘作为组合经理的上下文；这里的记忆是日志检索与提示上下文。

断点恢复可以用下面这个独立示例：

```sh
./start.command --checkpoint --ticker AAPL --date 2026-11-03 \
  --analysts market --save --no-show --html
```

等一个分析节点完成后按 Ctrl+C，再执行完全相同的命令，观察是否出现 `Resuming the saved run`。尚未保存任何节点时，中断后会从头开始。成功完成后会清理该次断点，下次运行会重新分析。前面的对比实验使用 `--no-checkpoint`，便于明确从头比较。

## 如何判断体验成功

- 最低可行体验：读已有报告，并完成一次四分析师分析。
- 主要能力体验：再做持仓对比和三个日期的回测。
- 阅读每份报告时，先核对日期和证据来源，再看多空分歧、仓位约束和最终评级。
- 历史行情会按日期裁剪，SEC 财报按披露时间筛选；近期新闻和社交源缺少完整历史档案，历史估值快照会被屏蔽。模型自身的历史知识也可能包含后续事件，因此历史演示适合观察决策流程和数据约束。

实现入口：`tradingagents/default_config.py`（数据配置）、`tradingagents/dataflows/router.py`（实际路由）、`tradingagents/dataflows/vendors/`（抓取实现）、`tradingagents/backtest.py`（历史决策评分）、`tradingagents/portfolio.py`（持仓输入）。
