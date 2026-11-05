# 港股、A 股与中概股的中文信源

从本次修改起，新启动的分析默认在原有 Yahoo / Reddit / StockTwits 之外加入中文内容，无需配置 API Key 或安装 OpenD。已运行的 Python/CLI 进程需要退出后重新启动才会载入修改。

| 内容 | 接入来源 | 使用方式 |
|---|---|---|
| 个股新闻 | 富途官方公开资讯检索接口 | 转换港股、沪深代码，读取中文标题、发布时间、原文 URL。接口缺少原始发布方时明确标注，不把聚合内容都称为“富途原创”。 |
| 公司公告 | 富途公告检索 | 与新闻分开标注；常见公司按中文名称检索，过滤无法匹配公司的结果。它是聚合检索，重要数字仍需核对发行人或交易所原文。 |
| 财经媒体报道 | 东方财富网页新闻搜索 | 保留返回的原始媒体名称，如财联社、证券时报、人民财讯、界面新闻等；具体媒体和内容取决于每次实际返回结果。 |
| 中文宏观新闻 | 富途、东方财富 | 新闻分析师调用 `get_global_news` 时补充“中国央行”“港股”等查询，可在配置中修改。 |
| 中文投资者讨论 | 富途牛牛社区 | 舆情分析师读取实际帖子样本，去除 HTML、重复和过短文本，按日期过滤。个人观点与权威事实分开；不编造点赞数、多空比例或缺失的帖子链接。 |

这些是**同时读取的补充信源**。原有 `data_vendors["news_data"]` 仍表示依次回退的主供应商链；Yahoo 成功返回也不会跳过已启用的中文补充来源。行情和财报接口继续按原配置工作。

## 默认覆盖范围

- 港股：`0700.HK`、`1810.HK` 等数字代码。
- A 股：`600519.SS`（也接受 `.SH`）、`000001.SZ`、`.BJ` 等。实际资讯覆盖由上游决定。
- 已明确映射的常见中概股：`BABA`、`JD`、`PDD`、`BIDU`、`NTES`、`BILI`、`NIO`、`XPEV`、`LI` 等。
- 其他代码可用 `chinese_news_keywords` 显式指定公司中文名，不依靠模型猜公司身份。

富途新闻接口实测用 `HK.00700`、`SH.600519` 这类格式的结果优于直接传 Yahoo 代码，接入层会转换。公告检索对代码的模糊匹配可能返回其他公司，所以公告优先使用已知中文名称，且再次校验标题；未知名称时宁可报告缺口。

## 使用

重新启动后，选择 `news` 或 `sentiment` 分析师即可使用。只选择 `market` 不会触发新闻抓取。

```sh
./start.command --no-checkpoint --ticker 1810.HK --date 2026-11-04 \
  --analysts news,sentiment --save --no-show --html
```

日期请按实际分析日调整。这条命令会调用你配置的模型并使用对应额度。报告头部会记录中文补充信源配置；具体是否获得文章，以报告引用和工具输出为准。

只保留富途新闻和公告，可以在 `.env` 设置：

```dotenv
TRADINGAGENTS_CHINESE_NEWS_SOURCES=futu
```

独立关闭社区，或关闭全部中文补充：

```dotenv
TRADINGAGENTS_FUTU_COMMUNITY_ENABLED=false
# TRADINGAGENTS_CHINESE_SOURCES_ENABLED=false
```

Python 自定义：

```python
from copy import deepcopy
from tradingagents.default_config import DEFAULT_CONFIG

config = deepcopy(DEFAULT_CONFIG)
config["chinese_news_sources"] = "futu,eastmoney"
config["chinese_news_keywords"] = {"1810.HK": "小米", "NEWADR": "公司中文名称"}
config["chinese_macro_queries"] = ["中国央行", "港股", "人民币汇率"]
config["chinese_news_article_limit"] = 16  # 合并去重后的补充条数
```

## 日期与可用性

中文来源按 `Asia/Shanghai` 的自然日过滤发布时间；历史窗口丢弃无日期和晚于分析日的内容。搜索仅返回近期有限样本，**不保证历史新闻、公告或舆情完整**。没有合适样本、超时或接口变化时明确标注不可用，不把它解释为“公司没有新闻”或“市场没有讨论”。

中文补充来源之间相同标题或原文链接会去重，并分配输出预算，避免单一来源挤占其他来源。媒体对同一事件的不同标题仍可能同时出现，分析提示词要求不把转载视为独立证据。社区只做基础文本清理，不能保证识别所有广告、讽刺、机器人或误导性帖子。

东方财富采用网页公开搜索接口，可能超时、限流或改变结构；富途公开内容接口也不保证持续可用。这两类错误会单独呈现，其他来源可继续返回。

## 接口依据

- [富途官方资讯搜索说明](https://www.futunn.com/skillhub/news_search)及其提供的 [API 契约下载包](https://www.futunn.com/skills/futu.zip)。本项目直接按公开接口契约实现，不需要安装全局 Skills。
- [富途官方社区情绪说明](https://www.futunn.com/skillhub/comment_sentiment)。
- [东方财富网页搜索](https://so.eastmoney.com/)，保留搜索结果返回的媒体归属与原文地址。

本次接入不等于已经接入港交所、巨潮资讯、上交所、深交所、证监会或央行的完整直连公告库；它们的原始披露仍应作为重要事实的优先核验依据。
