"""Shared model catalog for CLI selections and validation."""

from __future__ import annotations

ModelOption = tuple[str, str]
ProviderModeOptions = dict[str, dict[str, list[ModelOption]]]

# Providers that serve many / frequently-changing models: offer only "Custom
# model ID" rather than a list that goes stale.
_CUSTOM_ONLY: dict[str, list[ModelOption]] = {
    "quick": [("Custom model ID", "custom")],
    "deep": [("Custom model ID", "custom")],
}


# Shared model list for GLM via Z.AI (international) and BigModel (China).
# Source: docs.z.ai (GLM Coding Plan supported models + LLM guides).
# All GLM 4.7+ entries support thinking mode via thinking={"type":"enabled"}.
_GLM_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("GLM-5.3-Flash - Fast, cost-efficient, 1M ctx", "glm-5.3-flash"),
        ("GLM-5-Turbo - Fast, switchable thinking modes", "glm-5-turbo"),
        ("GLM-4.5-Air - Lightweight, cost-efficient", "glm-4.5-air"),
        ("Custom model ID", "custom"),
    ],
    "deep": [
        ("GLM-5.3 - Latest flagship, 1M ctx", "glm-5.3"),
        ("GLM-5.2 - 744B, 1M ctx", "glm-5.2"),
        ("GLM-5.1 - 745B, 200K ctx", "glm-5.1"),
        ("GLM-4.7 - Previous-gen flagship", "glm-4.7"),
        ("Custom model ID", "custom"),
    ],
}


# Shared model list for Qwen's global (dashscope-intl) and CN (dashscope) endpoints.
# Source: modelstudio.console.alibabacloud.com (Featured Models — Flagship + Cost-optimized).
#
# Only versioned IDs are exposed in the dropdown. The version-less aliases
# (qwen-plus, qwen-flash) are documented by Alibaba as auto-upgrading
# pointers ("backbone, latest, and snapshot ... have been upgraded to the
# Qwen3 series"), which means their behavior shifts when Alibaba rotates
# the backing model. Users who want a specific generation pick it
# explicitly; users who really want auto-latest can enter the alias via
# "Custom model ID".
_QWEN_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("Qwen 3.8 Flash - Latest fast model, 1M ctx", "qwen3.8-flash"),
        ("Custom model ID", "custom"),
    ],
    "deep": [
        ("Qwen 3.8 Max - Latest flagship", "qwen3.8-max"),
        ("Qwen 3.8 Flash - Fast alternative, 1M ctx", "qwen3.8-flash"),
        ("Custom model ID", "custom"),
    ],
}


# Shared model list for MiniMax's global and CN endpoints (same IDs).
# Full official lineup per platform.minimax.io/docs/api-reference/text-openai-api.
# M3 carries a 1M-token context window; the M2.x line is 204,800 tokens.
# Kimi (Moonshot). Source: platform.kimi.ai/docs/models. "Custom model ID" stays
# available for models newer than this list. The k2.7-code variants are omitted:
# they are coding specialists, not analysis models.
_KIMI_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("Kimi K2.6 - 256K ctx, thinking modes, agent tasks", "kimi-k2.6"),
        ("Kimi K3 - Flagship, 1M ctx", "kimi-k3"),
        ("Custom model ID", "custom"),
    ],
    "deep": [
        ("Kimi K3 - Flagship, 1M ctx, native visual understanding", "kimi-k3"),
        ("Kimi K2.6 - 256K ctx, thinking modes, agent tasks", "kimi-k2.6"),
        ("Custom model ID", "custom"),
    ],
}


_MINIMAX_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("MiniMax-M3 - Latest, 1M ctx, native multimodal", "MiniMax-M3"),
        ("MiniMax-M2.7-highspeed - Fast M2.7, 204K ctx, ~100 TPS", "MiniMax-M2.7-highspeed"),
        ("MiniMax-M2.5-highspeed - Previous-gen highspeed, 204K ctx", "MiniMax-M2.5-highspeed"),
        ("Custom model ID", "custom"),
    ],
    "deep": [
        ("MiniMax-M3 - Latest flagship, 1M ctx, multimodal coding/agent", "MiniMax-M3"),
        ("MiniMax-M2.7 - Previous flagship, 204K ctx", "MiniMax-M2.7"),
        ("MiniMax-M2.7-highspeed - Same quality as M2.7, ~100 TPS", "MiniMax-M2.7-highspeed"),
        ("MiniMax-M2.5 - Earlier flagship, 204K ctx", "MiniMax-M2.5"),
        ("Custom model ID", "custom"),
    ],
}


_ANTIGRAVITY_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("Gemini 3.8 Flash - Fast, low latency", "gemini-3.8-flash"),
        ("Gemini 3.8 Flash (High) - Deep reasoning", "gemini-3.8-flash-high"),
        ("Gemini 3.7 Flash - General tasks", "gemini-3.7-flash"),
        ("Claude Opus 4.6 (Thinking) - Complex reasoning", "claude-opus-4.6-think"),
        ("Custom model ID", "custom"),
    ],
    "deep": [
        ("Gemini 4 Argon - Frontier coding and reasoning", "gemini-4-argon"),
        ("Gemini 3.8 Flash (High) - Deep reasoning", "gemini-3.8-flash-high"),
        ("Claude Opus 4.6 (Thinking) - Complex reasoning", "claude-opus-4.6-think"),
        ("Gemini 3.8 Flash - Fast alternative", "gemini-3.8-flash"),
        ("Custom model ID", "custom"),
    ],
}

# Model IDs verified against https://learn.chatgpt.com/docs/models (2026-11-05).
_OPENAI_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("GPT-6 Luna - Fast, high-volume and cost-efficient", "gpt-6-luna"),
        ("GPT-6.1 Terra - Fast, efficient visual reasoning and writing", "gpt-6.1-terra"),
        ("GPT-5.6 Luna - Previous-gen fast tier", "gpt-5.6-luna"),
        ("GPT-5.6 Terra - Balances intelligence and cost", "gpt-5.6-terra"),
        ("Custom model ID", "custom"),
    ],
    "deep": [
        ("GPT-6.1 Sol - Near-Astra performance, balanced cost", "gpt-6.1-sol"),
        ("GPT-6 Sol - Complex reasoning and analysis", "gpt-6-sol"),
        ("GPT-6 Astra - Hardest multi-step reasoning", "gpt-6-astra"),
        ("GPT-5.6 - Previous-gen reasoning (Sol)", "gpt-5.6"),
        ("GPT-5.5 - Earlier frontier, 1M context", "gpt-5.5"),
        ("Custom model ID", "custom"),
    ],
}

_CLAUDE_CLI_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("Claude Sonnet 5 - Best speed and intelligence balance", "claude-sonnet-5"),
        ("Claude Sonnet 4.6 - Fast agentic & coding", "claude-sonnet-4-6"),
        ("Claude Haiku 4.5 - Fastest with near-frontier intelligence", "claude-haiku-4-5"),
        ("Custom model ID", "custom"),
    ],
    "deep": [
        ("Claude Opus 5.5 - Frontier agentic and enterprise work", "claude-opus-5-5"),
        ("Claude Fable 5.1 - Most capable, demanding long-horizon reasoning", "claude-fable-5-1"),
        ("Claude Opus 4.7 - Deep reasoning", "claude-opus-4-7"),
        ("Claude Sonnet 5 - Near-frontier intelligence at Sonnet cost", "claude-sonnet-5"),
        ("Custom model ID", "custom"),
    ],
}

_CODEX_CLI_MODELS: dict[str, list[ModelOption]] = {
    "quick": [
        ("GPT-6 Luna - Fast, high-volume and cost-efficient", "gpt-6-luna"),
        ("GPT-6.1 Terra - Fast, efficient visual reasoning and writing", "gpt-6.1-terra"),
        ("Codex Mini Latest - Fast Codex model", "codex-mini-latest"),
        ("GPT-5.6 Luna - Previous-gen fast tier", "gpt-5.6-luna"),
        ("GPT-5.6 Terra - Balances intelligence and cost", "gpt-5.6-terra"),
        ("Custom model ID", "custom"),
    ],
    "deep": [
        ("GPT-6.1 Sol - Near-Astra performance, balanced cost", "gpt-6.1-sol"),
        ("GPT-6 Sol - Complex reasoning and analysis", "gpt-6-sol"),
        ("GPT-6 Astra - Hardest multi-step reasoning", "gpt-6-astra"),
        ("GPT-5.6 - Previous-gen reasoning (Sol)", "gpt-5.6"),
        ("GPT-5.5 - Earlier frontier, 1M context", "gpt-5.5"),
        ("Custom model ID", "custom"),
    ],
}


MODEL_OPTIONS: ProviderModeOptions = {
    "openai": _OPENAI_MODELS,
    "anthropic": {
        "quick": [
            ("Claude Sonnet 5 - Best speed and intelligence balance", "claude-sonnet-5"),
            ("Claude Haiku 4.5 - Fastest with near-frontier intelligence", "claude-haiku-4-5"),
            ("Custom model ID", "custom"),
        ],
        "deep": [
            ("Claude Opus 5.5 - Frontier agentic and enterprise work", "claude-opus-5-5"),
            ("Claude Fable 5.1 - Most capable, demanding long-horizon reasoning", "claude-fable-5-1"),
            ("Claude Sonnet 5 - Near-frontier intelligence at Sonnet cost", "claude-sonnet-5"),
            ("Custom model ID", "custom"),
        ],
    },
    "google": {
        "quick": [
            ("Gemini 3.8 Flash - Most capable Flash", "gemini-3.8-flash"),
            ("Gemini 3.5 Flash Lite - Fast and cost-efficient", "gemini-3.5-flash-lite"),
            ("Gemini 3.1 Flash Lite - Most cost-efficient", "gemini-3.1-flash-lite"),
            ("Custom model ID", "custom"),
        ],
        "deep": [
            ("Gemini 3.8 Flash - Most capable Flash, 1M context", "gemini-3.8-flash"),
            ("Gemini 3.1 Pro - Reasoning-first, complex workflows (preview)", "gemini-3.1-pro-preview"),
            ("Gemini 3.5 Flash - Previous Flash, strong agentic + coding", "gemini-3.5-flash"),
            ("Custom model ID", "custom"),
        ],
    },
    "xai": {
        "quick": [
            ("Grok 4.6 - Latest flagship, fastest, 500K ctx", "grok-4.6"),
            ("Grok Build 0.1 - Coding-specialized, 256K ctx", "grok-build-0.1"),
            ("Custom model ID", "custom"),
        ],
        "deep": [
            ("Grok 4.6 - Latest flagship, 500K ctx", "grok-4.6"),
            ("Grok 4.5 - Previous flagship, coding and agentic", "grok-4.5"),
            ("Grok 4.3 - Older generation, 1M ctx", "grok-4.3"),
            ("Custom model ID", "custom"),
        ],
    },
    # DeepSeek: the deepseek-chat / deepseek-reasoner aliases are deprecated
    # (2026-07-24) and now map to V4 Flash; expose the V4 IDs directly. V4 Flash
    # serves both non-thinking and thinking modes (the DeepSeekChatOpenAI client
    # handles the reasoning_content round-trip).
    "deepseek": {
        "quick": [
            ("DeepSeek Flash - V4.1 Flash, fast, 1M ctx", "deepseek-flash"),
            ("Custom model ID", "custom"),
        ],
        "deep": [
            ("DeepSeek V4 Pro - Flagship", "deepseek-v4-pro"),
            ("DeepSeek Flash - V4.1 Flash, fast, 1M ctx", "deepseek-flash"),
            ("Custom model ID", "custom"),
        ],
    },
    # Qwen: same model IDs across global (dashscope-intl) and China
    # (dashscope) endpoints, so the two provider keys share one model list.
    "qwen": _QWEN_MODELS,
    "qwen-cn": _QWEN_MODELS,
    # GLM: Z.AI (international) and BigModel (China) host the same model
    # IDs; the two provider keys share one model list.
    "glm": _GLM_MODELS,
    "glm-cn": _GLM_MODELS,
    # MiniMax: same model IDs across global (.io) and China (.com) regions,
    # so the two provider keys share one model list.
    "kimi": _KIMI_MODELS,
    "minimax": _MINIMAX_MODELS,
    "minimax-cn": _MINIMAX_MODELS,
    # OpenRouter: fetched dynamically. Azure: any deployed model name.
    # Ollama display labels intentionally omit a "local" marker — the
    # endpoint is now configurable via OLLAMA_BASE_URL, so the same labels
    # apply whether the user runs ollama-serve on localhost or against a
    # remote host. The actual resolved endpoint is surfaced separately by
    # cli.prompts.confirm_ollama_endpoint() right after provider selection.
    # "Custom model ID" lets users pick any model they have pulled via
    # `ollama pull` beyond the three suggested defaults.
    "ollama": {
        "quick": [
            ("Qwen3:latest (8B)", "qwen3:latest"),
            ("GPT-OSS:latest (20B)", "gpt-oss:latest"),
            ("GLM-4.7-Flash:latest (30B)", "glm-4.7-flash:latest"),
            ("Custom model ID", "custom"),
        ],
        "deep": [
            ("GLM-4.7-Flash:latest (30B)", "glm-4.7-flash:latest"),
            ("GPT-OSS:latest (20B)", "gpt-oss:latest"),
            ("Qwen3:latest (8B)", "qwen3:latest"),
            ("Custom model ID", "custom"),
        ],
    },
    # Generic OpenAI-compatible endpoint: the model is whatever the user's
    # server serves, so only "Custom model ID" is offered.
    "openai_compatible": _CUSTOM_ONLY,
    # Hosted OpenAI-compatible providers that serve many (and frequently
    # changing) models — offer "Custom model ID" rather than a list that goes
    # stale. The endpoint + key are wired by the provider; the user picks the
    # model their account has access to.
    "mistral": {
        "quick": [
            ("Mistral Small 4 - Fast, 262K ctx", "mistral-small-2603"),
            ("Custom model ID", "custom"),
        ],
        "deep": [
            ("Mistral Medium 3.5 - 262K ctx", "mistral-medium-2604"),
            ("Mistral Small 4 - Fast, 262K ctx", "mistral-small-2603"),
            ("Custom model ID", "custom"),
        ],
    },
    "groq": _CUSTOM_ONLY,
    "nvidia": _CUSTOM_ONLY,
    # Bedrock model IDs / cross-region inference profile IDs are user-specified.
    "bedrock": _CUSTOM_ONLY,
    # The plan's models are listed live from OpenAI in the CLI; fallback catalog for Web UI dropdowns.
    "chatgpt": _OPENAI_MODELS,
    # Google Antigravity CLI (`agy`) models.
    "antigravity": _ANTIGRAVITY_MODELS,
    "agy": _ANTIGRAVITY_MODELS,
    # Anthropic Claude Code CLI (`claude`) models.
    "claude-cli": _CLAUDE_CLI_MODELS,
    "claude_cli": _CLAUDE_CLI_MODELS,
    "claude-code": _CLAUDE_CLI_MODELS,
    # OpenAI Codex CLI (`codex`) models.
    "codex": _CODEX_CLI_MODELS,
    "codex-cli": _CODEX_CLI_MODELS,
    "codex_cli": _CODEX_CLI_MODELS,
}


# Canonical list of providers surfaced in dropdown menus (e.g. Web UI),
# putting local subscription / CLI providers first.
PROVIDER_CATALOG: tuple[tuple[str, str], ...] = (
    ("chatgpt", "ChatGPT 订阅 (OAuth)"),
    ("antigravity", "Antigravity CLI (agy)"),
    ("claude-cli", "Claude CLI (claude 订阅)"),
    ("codex", "Codex CLI (codex 订阅)"),
    ("openai", "OpenAI API"),
    ("anthropic", "Anthropic API"),
    ("google", "Google Gemini API"),
    ("deepseek", "DeepSeek API"),
    ("qwen", "Qwen (DashScope Global)"),
    ("qwen-cn", "Qwen (DashScope 中国)"),
    ("glm", "GLM (Z.AI Global)"),
    ("glm-cn", "GLM (BigModel 中国)"),
    ("minimax", "MiniMax Global"),
    ("minimax-cn", "MiniMax 中国"),
    ("kimi", "Kimi (Moonshot)"),
    ("xai", "xAI (Grok)"),
    ("mistral", "Mistral"),
    ("openrouter", "OpenRouter"),
    ("ollama", "Ollama (Local)"),
)


def get_model_options(provider: str, mode: str) -> list[ModelOption]:
    """Return shared model options for a provider and selection mode."""
    return MODEL_OPTIONS[provider.lower()][mode]


# Served by the provider but not offered in the picker: models retired from the
# menu, and the explicit ID of a model listed under a shorter name. Known to
# validation so a config naming one runs without an unknown-model warning.
LEGACY_MODELS: dict[str, list[str]] = {
    "openai": ["gpt-5.4", "gpt-5.4-mini", "gpt-5.6-sol"],
    "xai": ["grok-4.20-0309-reasoning", "grok-4.20-0309-non-reasoning",
            "grok-4.20-multi-agent-0309"],
    "deepseek": ["deepseek-v4-flash"],
    "qwen": ["qwen3.7-max", "qwen3.7-plus", "qwen3.6-max", "qwen3.6-plus"],
    "anthropic": ["claude-opus-5", "claude-fable-5", "claude-opus-4-8", "claude-opus-4-7"],
}


def get_known_models() -> dict[str, list[str]]:
    """Build known model names from the shared CLI catalog plus legacy IDs."""
    return {
        provider: sorted(
            {
                value
                for options in mode_options.values()
                for _, value in options
            }
            | set(LEGACY_MODELS.get(provider, []))
        )
        for provider, mode_options in MODEL_OPTIONS.items()
    }
