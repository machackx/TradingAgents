# 本地运行

项目已安装到本目录的 `.venv`，原生界面为终端 CLI，默认使用 ChatGPT 订阅、中文报告和一轮研究/风险讨论。

首次登录（需要 ChatGPT Plus 或 Pro，并在浏览器里允许使用订阅额度）：

```sh
cd /Users/yangce/Projects/trading-agents
./start.command login chatgpt
```

打开命令打印的登录地址，完成授权后返回终端。凭据由项目保存在
`~/.tradingagents/chatgpt-auth.json`，不会写入代码仓库。

交互运行：双击 `start.command`，或在本目录执行 `./start.command`。

示例（会消耗 ChatGPT 订阅额度，日期按需修改）：

```sh
./start.command --ticker NVDA --date 2026-11-02 --analysts market --save --no-show
```

默认结果保存到 `~/.tradingagents/logs/`。HTML 报告名为 `complete_report.html`。
可在 `.env` 修改模型、输出语言和讨论轮数。实际可用模型取决于登录账户。

本地包含一处订阅兼容处理：默认顺序调用工具。验证时订阅接口的并行工具调用
仅在结束事件中返回参数，而当前 LangChain 流式读取会丢失这些参数；顺序调用
可正常接收参数片段。修改位于 `tradingagents/llm_clients/openai_client.py`。

启动脚本会清除继承的代理变量，因为安装时本机配置的 `127.0.0.1:7890`
代理没有运行，直连已验证可用。以后如需代理，可修改脚本中的 `unset` 行。

退出登录：`./start.command logout chatgpt`。

2026-11-03 已验证：订阅推理成功、20 项订阅相关测试通过、所有依赖兼容，
并从头完成 NVDA（分析日期 2026-11-02）的市场分析流程，未出现工具参数错误。
本次完整报告位于 `~/.tradingagents/logs/reports/NVDA_20261103_200430/complete_report.html`。

本机 `/usr/bin/git` 触发 Xcode 许可提示；可直接使用已有的命令行工具 Git：
`/Library/Developer/CommandLineTools/usr/bin/git`。

## 使用 Antigravity CLI (`agy`) 跑股票分析

项目已内置基于 Google Antigravity CLI (`agy`) 的 LLM Client（[`tradingagents/llm_clients/antigravity_client.py`](tradingagents/llm_clients/antigravity_client.py)），支持分析师工具调用（`bind_tools`）、经理层结构化输出（`with_structured_output`）以及 Token 统计。

1. **安装并登录 `agy` CLI**（如尚未安装）：
   ```sh
   brew install --cask antigravity-cli
   # 或：curl -fsSL https://antigravity.google/cli/install.sh | bash
   agy
   ```
   首次运行 `agy` 完成账号登录授权后即可退出（`Ctrl+D Ctrl+D` 或 `/exit`）。

2. **通过环境变量直接运行分析**（不修改 `.env` 默认配置）：
   ```sh
   TRADINGAGENTS_LLM_PROVIDER=antigravity \
   TRADINGAGENTS_QUICK_THINK_LLM=gemini-3.8-flash \
   TRADINGAGENTS_DEEP_THINK_LLM=gemini-4-argon \
   ./start.command --no-checkpoint --ticker NVDA --date 2026-11-02 --analysts market --save --no-show --html
   ```
   也可以在 `.env` 中将 `TRADINGAGENTS_LLM_PROVIDER` 设为 `antigravity`（或 `agy`），如果 `agy` 不在默认 `PATH` 中，可设置 `ANTIGRAVITY_CLI_PATH=/path/to/agy`。

## 按角色独立选择厂商与模型（跨厂商混用）

项目支持为全部 12 个调研与决策角色单独绑定不同厂商与模型（未单独指定的角色自动跟随默认的快速层/深度层配置）：

- **Web 分析台（下拉框选择）**：双击 `start-web.command`（或执行 `./start-web.command`），在“新建分析”页面中：
  1. 可直接通过下拉框选择 **快速层默认厂商/模型** 与 **深度层默认厂商/模型**；
  2. 在 **“按角色独立选择厂商与模型（跨厂商混用）”** 面板中，为每位分析师（市场、情绪、新闻、基本面）、多/空研究员、研究经理、交易员、风控团队和投资组合经理分别用下拉框选择厂商（`chatgpt`、`antigravity`、`claude-cli`、`codex` 等）及对应模型。
- **终端 CLI 交互菜单或参数**：
  ```sh
  # 在终端弹出按角色选择厂商与模型的交互下拉菜单
  ./start.command --agent-models interactive

  # 或直接通过参数指定角色厂商与模型
  ./start.command --no-checkpoint --ticker NVDA --date 2026-11-03 --analysts market,news \
    --agent-models "market_analyst=antigravity:gemini-3.8-flash,bull_researcher=claude-cli:claude-sonnet-5,portfolio_manager=chatgpt:gpt-6-sol"
  ```
- **`.env` 环境变量配置**：
  - 统一配置：`TRADINGAGENTS_AGENT_MODELS=market_analyst=antigravity:gemini-3.8-flash,bull_researcher=claude-cli:claude-sonnet-5`
  - 单角色变量：`TRADINGAGENTS_MARKET_ANALYST_PROVIDER=antigravity` + `TRADINGAGENTS_MARKET_ANALYST_LLM=gemini-3.8-flash`
