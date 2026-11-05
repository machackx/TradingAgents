
from typing import Any

from .base_client import BaseLLMClient


def create_llm_client(
    provider: str,
    model: str,
    base_url: str | None = None,
    **kwargs,
) -> BaseLLMClient:
    """Create an LLM client for the specified provider.

    Provider modules are imported lazily so that simply importing this
    factory (e.g. during test collection) does not pull in heavy LLM SDKs
    or fail when their API keys are absent.

    Args:
        provider: LLM provider name
        model: Model name/identifier
        base_url: Optional base URL for API endpoint
        **kwargs: Additional provider-specific arguments

    Returns:
        Configured BaseLLMClient instance

    Raises:
        ValueError: If provider is not supported
    """
    provider_lower = provider.lower()

    # Native (non-OpenAI) APIs are matched first so their string check doesn't
    # import the OpenAI client. Everything else is OpenAI-compatible and routes
    # through the provider registry (single source of truth).
    if provider_lower == "anthropic":
        from .anthropic_client import AnthropicClient
        return AnthropicClient(model, base_url, **kwargs)

    if provider_lower == "google":
        from .google_client import GoogleClient
        return GoogleClient(model, base_url, **kwargs)

    if provider_lower == "azure":
        from .azure_client import AzureOpenAIClient
        return AzureOpenAIClient(model, base_url, **kwargs)

    if provider_lower == "bedrock":
        from .bedrock_client import BedrockClient
        return BedrockClient(model, base_url, **kwargs)

    if provider_lower in ("antigravity", "agy"):
        from .antigravity_client import AntigravityClient
        return AntigravityClient(model, base_url, **kwargs)

    if provider_lower in ("claude-cli", "claude_cli", "claude-code"):
        from .claude_cli_client import ClaudeCLIClient
        return ClaudeCLIClient(model, base_url, **kwargs)

    if provider_lower in ("codex", "codex-cli", "codex_cli"):
        from .codex_cli_client import CodexCLIClient
        return CodexCLIClient(model, base_url, **kwargs)

    from .openai_client import OpenAIClient, is_openai_compatible
    if is_openai_compatible(provider_lower):
        return OpenAIClient(model, base_url, provider=provider_lower, **kwargs)

    raise ValueError(f"Unsupported LLM provider: {provider}")


def _coerce_max_retries(value):
    """Validate an ``llm_max_retries`` value to a non-negative int.

    Accepts an int or a numeric string (env vars arrive as strings). Rejects
    booleans and negatives loudly so a misconfiguration fails at startup rather
    than silently disabling retries.
    """
    if isinstance(value, bool):
        raise ValueError(f"llm_max_retries must be an integer, not a boolean: {value!r}")
    try:
        n = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"llm_max_retries must be an integer, got {value!r}") from exc
    if n < 0:
        raise ValueError(f"llm_max_retries must be >= 0, got {n}")
    return n


def _coerce_max_tokens(value):
    """Validate a ``max_tokens`` value to a positive int (env vars are strings)."""
    if isinstance(value, bool):
        raise ValueError(f"max_tokens must be an integer, not a boolean: {value!r}")
    try:
        n = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"max_tokens must be an integer, got {value!r}") from exc
    if n <= 0:
        raise ValueError(f"max_tokens must be > 0, got {n}")
    return n


# Providers that take OpenAI's ``openai_reasoning_effort``.
REASONING_EFFORT_PROVIDERS = ("openai", "chatgpt")


def build_llm_kwargs(config: dict) -> dict[str, Any]:
    """Keyword arguments for ``create_llm_client`` from a TradingAgents config."""
    kwargs = {}
    provider = config.get("llm_provider", "").lower()

    if provider == "google":
        thinking_level = config.get("google_thinking_level")
        if thinking_level:
            kwargs["thinking_level"] = thinking_level

    elif provider in ("antigravity", "agy"):
        thinking_level = (
            config.get("google_thinking_level")
            or config.get("openai_reasoning_effort")
            or config.get("anthropic_effort")
        )
        if thinking_level:
            kwargs["thinking_level"] = thinking_level

    elif provider in ("claude-cli", "claude_cli", "claude-code"):
        effort = (
            config.get("anthropic_effort")
            or config.get("google_thinking_level")
            or config.get("openai_reasoning_effort")
        )
        if effort:
            kwargs["effort"] = effort

    elif provider in ("codex", "codex-cli", "codex_cli"):
        reasoning_effort = (
            config.get("openai_reasoning_effort")
            or config.get("google_thinking_level")
            or config.get("anthropic_effort")
        )
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort

    elif provider in REASONING_EFFORT_PROVIDERS:
        reasoning_effort = config.get("openai_reasoning_effort")
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort

    elif provider == "anthropic":
        effort = config.get("anthropic_effort")
        if effort:
            kwargs["effort"] = effort

    # Sampling temperature is cross-provider: forward it whenever set.
    # float() here so a value coming from a TRADINGAGENTS_TEMPERATURE env
    # string ("0.2") works the same as a programmatic float.
    temperature = config.get("temperature")
    if temperature is not None and temperature != "":
        kwargs["temperature"] = float(temperature)

    # SDK retry budget is cross-provider. Forward it only when explicitly set
    # so each provider keeps its own default (usually 2) otherwise (#1091).
    max_retries = config.get("llm_max_retries")
    if max_retries is not None and max_retries != "":
        kwargs["max_retries"] = _coerce_max_retries(max_retries)

    # Output-token cap is cross-provider, but Gemini names it
    # ``max_output_tokens``; forward under the right key when set (#1204).
    max_tokens = config.get("max_tokens")
    if max_tokens is not None and max_tokens != "":
        key = "max_output_tokens" if provider == "google" else "max_tokens"
        kwargs[key] = _coerce_max_tokens(max_tokens)

    return kwargs


def tier_provider(config: dict, tier: str) -> str:
    """The provider of the ``quick`` or ``deep`` model tier: its own, else ``llm_provider``."""
    return (config.get(f"{tier}_think_provider") or config["llm_provider"]).lower()


def create_tier_client(config: dict, tier: str, **extra) -> BaseLLMClient:
    """The client for the ``quick`` or ``deep`` model tier (#1440).

    A tier on its own provider gets that provider's settings (thinking level,
    reasoning effort, the name of the output-token cap) and its own endpoint,
    else the provider's default: ``backend_url`` belongs to ``llm_provider``.
    """
    provider = tier_provider(config, tier)
    if provider == config["llm_provider"].lower():
        base_url = config.get(f"{tier}_think_backend_url") or config.get("backend_url")
    else:
        base_url = config.get(f"{tier}_think_backend_url")
    kwargs = build_llm_kwargs({**config, "llm_provider": provider})
    return create_llm_client(provider=provider, model=config[f"{tier}_think_llm"], base_url=base_url,
                             **kwargs, **extra)


# Canonical metadata for every agent role in the TradingAgents pipeline.
AGENT_ROLE_SPECS: tuple[dict[str, str], ...] = (
    {"key": "market_analyst",       "node": "Market Analyst",       "tier": "quick", "group": "analysts",  "label": "Market Analyst",       "zh_label": "市场分析师"},
    {"key": "social_analyst",       "node": "Sentiment Analyst",    "tier": "quick", "group": "analysts",  "label": "Sentiment Analyst",    "zh_label": "情绪分析师"},
    {"key": "news_analyst",         "node": "News Analyst",         "tier": "quick", "group": "analysts",  "label": "News Analyst",         "zh_label": "新闻分析师"},
    {"key": "fundamentals_analyst", "node": "Fundamentals Analyst", "tier": "quick", "group": "analysts",  "label": "Fundamentals Analyst", "zh_label": "基本面分析师"},
    {"key": "bull_researcher",      "node": "Bull Researcher",      "tier": "quick", "group": "research",  "label": "Bull Researcher",      "zh_label": "多头研究员"},
    {"key": "bear_researcher",      "node": "Bear Researcher",      "tier": "quick", "group": "research",  "label": "Bear Researcher",      "zh_label": "空头研究员"},
    {"key": "research_manager",     "node": "Research Manager",     "tier": "deep",  "group": "research",  "label": "Research Manager",     "zh_label": "研究经理"},
    {"key": "trader",               "node": "Trader",               "tier": "quick", "group": "trading",   "label": "Trader",               "zh_label": "交易员"},
    {"key": "aggressive_debator",   "node": "Aggressive Analyst",   "tier": "quick", "group": "risk",      "label": "Aggressive Analyst",   "zh_label": "激进风控"},
    {"key": "conservative_debator", "node": "Conservative Analyst", "tier": "quick", "group": "risk",      "label": "Conservative Analyst", "zh_label": "保守风控"},
    {"key": "neutral_debator",      "node": "Neutral Analyst",      "tier": "quick", "group": "risk",      "label": "Neutral Analyst",      "zh_label": "中性风控"},
    {"key": "portfolio_manager",    "node": "Portfolio Manager",    "tier": "deep",  "group": "portfolio", "label": "Portfolio Manager",    "zh_label": "投资组合经理"},
)

AGENT_ROLE_BY_KEY: dict[str, dict[str, str]] = {spec["key"]: spec for spec in AGENT_ROLE_SPECS}

_ROLE_ALIASES: dict[str, str] = {
    "market": "market_analyst",
    "social": "social_analyst",
    "sentiment": "social_analyst",
    "sentiment_analyst": "social_analyst",
    "news": "news_analyst",
    "fundamentals": "fundamentals_analyst",
    "bull": "bull_researcher",
    "bear": "bear_researcher",
    "aggressive": "aggressive_debator",
    "aggressive_analyst": "aggressive_debator",
    "conservative": "conservative_debator",
    "conservative_analyst": "conservative_debator",
    "neutral": "neutral_debator",
    "neutral_analyst": "neutral_debator",
}
for _spec in AGENT_ROLE_SPECS:
    _ROLE_ALIASES[_spec["key"]] = _spec["key"]
    _ROLE_ALIASES[_spec["node"].lower()] = _spec["key"]
    _ROLE_ALIASES[_spec["node"].lower().replace(" ", "_")] = _spec["key"]


def normalize_role_key(role: str) -> str:
    """Resolve a role name or alias to its canonical key in ``AGENT_ROLE_BY_KEY``."""
    cleaned = (role or "").strip().lower()
    canonical = _ROLE_ALIASES.get(cleaned)
    if not canonical:
        raise ValueError(f"Unknown agent role: {role!r}. Valid roles: {', '.join(AGENT_ROLE_BY_KEY)}")
    return canonical


def agent_role_override(config: dict, role: str) -> dict[str, str] | None:
    """Return the explicit ``{provider, model, backend_url}`` override for ``role``, or None."""
    canonical = normalize_role_key(role)
    raw_map = config.get("agent_models")
    entry: dict[str, Any] = {}
    if isinstance(raw_map, dict):
        for k, v in raw_map.items():
            try:
                if normalize_role_key(str(k)) == canonical and isinstance(v, dict):
                    entry.update(v)
            except ValueError:
                continue

    env_provider = config.get(f"{canonical}_provider")
    env_model = config.get(f"{canonical}_llm")
    env_url = config.get(f"{canonical}_backend_url")
    if env_provider:
        entry.setdefault("provider", env_provider)
    if env_model:
        entry.setdefault("model", env_model)
    if env_url:
        entry.setdefault("backend_url", env_url)

    provider = (entry.get("provider") or "").strip().lower()
    model = (entry.get("model") or entry.get("llm") or "").strip()
    backend_url = (entry.get("backend_url") or "").strip() or None
    if not provider and not model and not backend_url:
        return None
    out: dict[str, str] = {}
    if provider:
        out["provider"] = provider
    if model:
        out["model"] = model
    if backend_url:
        out["backend_url"] = backend_url
    return out


def resolve_agent_spec(config: dict, role: str) -> dict[str, Any]:
    """Resolve the effective provider, model, and base_url for ``role``."""
    canonical = normalize_role_key(role)
    spec = AGENT_ROLE_BY_KEY[canonical]
    tier = spec["tier"]
    default_provider = tier_provider(config, tier)
    default_model = config.get(f"{tier}_think_llm")
    if default_provider == (config.get("llm_provider") or "").lower():
        default_url = config.get(f"{tier}_think_backend_url") or config.get("backend_url")
    else:
        default_url = config.get(f"{tier}_think_backend_url")

    override = agent_role_override(config, canonical)
    if not override:
        return {
            "role": canonical,
            "tier": tier,
            "provider": default_provider,
            "model": default_model,
            "base_url": default_url,
            "overridden": False,
        }

    provider = (override.get("provider") or default_provider).lower()
    model = override.get("model")
    if not model:
        if provider == default_provider:
            model = default_model
        else:
            from .model_catalog import get_model_options
            try:
                opts = [val for _, val in get_model_options(provider, tier) if val != "custom"]
                model = opts[0] if opts else default_model
            except KeyError:
                model = default_model

    if override.get("backend_url"):
        base_url = override["backend_url"]
    elif provider == default_provider:
        base_url = default_url
    elif provider == (config.get("llm_provider") or "").lower():
        base_url = config.get("backend_url")
    else:
        base_url = None

    return {
        "role": canonical,
        "tier": tier,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "overridden": True,
    }


def create_agent_client(config: dict, role: str, **extra) -> BaseLLMClient:
    """Create the LLM client for a specific agent role, honoring per-role overrides."""
    resolved = resolve_agent_spec(config, role)
    provider = resolved["provider"]
    kwargs = build_llm_kwargs({**config, "llm_provider": provider})
    return create_llm_client(
        provider=provider,
        model=resolved["model"],
        base_url=resolved["base_url"],
        **kwargs,
        **extra,
    )


def configured_agent_models(config: dict) -> dict[str, dict[str, str]]:
    """Return ``{role_key: {provider, model}}`` for all roles with an explicit override."""
    result: dict[str, dict[str, str]] = {}
    for spec in AGENT_ROLE_SPECS:
        key = spec["key"]
        resolved = resolve_agent_spec(config, key)
        if resolved["overridden"]:
            result[key] = {
                "provider": str(resolved["provider"] or ""),
                "model": str(resolved["model"] or ""),
            }
    return result
