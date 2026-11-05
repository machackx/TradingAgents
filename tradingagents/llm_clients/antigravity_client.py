"""Antigravity CLI (`agy`) LLM client for TradingAgents.

Wraps Google's Antigravity CLI (`agy -p ... --output-format json`) as a
LangChain `BaseChatModel` so TradingAgents can run full multi-agent stock
and crypto analyses—including analyst tool-calling loops and manager
structured outputs—through the local `agy` binary and its signed-in session.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, Field

from .base_client import BaseLLMClient
from .validators import validate_model

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 600

_CANDIDATE_PATHS = (
    Path.home() / ".local" / "bin" / "agy",
    Path("/opt/homebrew/bin/agy"),
    Path("/usr/local/bin/agy"),
    Path.home() / ".gemini" / "antigravity" / "bin" / "agy",
)

_EFFORT_MAP = {
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "max": "max",
}

_PIPELINE_SYSTEM_GUARD = (
    "You are acting as an LLM inference engine inside the TradingAgents "
    "financial analysis pipeline. Answer strictly based on the conversation "
    "and data provided below. Do NOT execute shell commands, inspect or edit "
    "local workspace files, or invoke built-in IDE/filesystem tools."
)


def _normalize_effort(effort: str | None) -> str | None:
    if not effort:
        return None
    return _EFFORT_MAP.get(effort.strip().lower(), effort.strip().lower())


def find_agy_binary(cli_path: str | None = None) -> str:
    """Resolve the `agy` executable path or raise a RuntimeError with install steps."""
    explicit = cli_path or os.environ.get("ANTIGRAVITY_CLI_PATH")
    if explicit:
        expanded = os.path.expanduser(explicit)
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return expanded
        found = shutil.which(expanded)
        if found:
            return found
        raise RuntimeError(
            f"Antigravity CLI binary not found at configured path: {explicit!r}. "
            "Check ANTIGRAVITY_CLI_PATH or install `agy`."
        )

    for cmd in ("agy", "antigravity"):
        found = shutil.which(cmd)
        if found:
            return found

    for candidate in _CANDIDATE_PATHS:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    raise RuntimeError(
        "Antigravity CLI ('agy') was not found on this machine. "
        "Install it with:\n"
        "  brew install --cask antigravity-cli\n"
        "or:\n"
        "  curl -fsSL https://antigravity.google/cli/install.sh | bash\n"
        "Then run `agy` once to sign in, or set ANTIGRAVITY_CLI_PATH to the binary path."
    )


def _format_messages_for_cli(
    messages: Sequence[BaseMessage],
    bound_tools: Sequence[dict[str, Any]] = (),
) -> str:
    """Serialize LangChain messages (and optional bound tool specs) into a prompt for `agy -p`."""
    sections: list[str] = [_PIPELINE_SYSTEM_GUARD]

    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content, ensure_ascii=False)
        if isinstance(msg, SystemMessage):
            sections.append(f"[System Instructions]\n{content}")
        elif isinstance(msg, HumanMessage):
            sections.append(f"[User]\n{content}")
        elif isinstance(msg, AIMessage):
            tool_calls = getattr(msg, "tool_calls", None) or []
            if tool_calls:
                calls_repr = json.dumps(
                    [{"name": tc.get("name"), "args": tc.get("args", {})} for tc in tool_calls],
                    ensure_ascii=False,
                )
                text_part = f"{content}\n" if content else ""
                sections.append(f"[Assistant]\n{text_part}[Tool Calls Made]: {calls_repr}")
            else:
                sections.append(f"[Assistant]\n{content}")
        elif isinstance(msg, ToolMessage):
            tool_name = getattr(msg, "name", None) or "tool"
            sections.append(f"[Tool Output ({tool_name})]\n{content}")
        else:
            sections.append(f"[Message]\n{content}")

    if bound_tools:
        tools_json = json.dumps(list(bound_tools), ensure_ascii=False, indent=2)
        sections.append(
            "[Available Data Tools]\n"
            "You may request execution of the following TradingAgents tools by returning "
            "them in `tool_calls`:\n"
            f"{tools_json}\n\n"
            "Instructions for output:\n"
            "- If you need to call one or more tools from [Available Data Tools] to fetch data, "
            "populate `tool_calls` with `[{\"name\": \"<tool_name>\", \"arguments\": {...}}]` "
            "using the exact parameter names from the tool schema, and set `final_answer` to `\"\"`.\n"
            "- If you have all the tool results you need to write your final report, set "
            "`tool_calls` to `[]` and write your complete report in `final_answer`."
        )

    return "\n\n".join(sections)


def _build_tool_dispatch_schema(bound_tools: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """JSON schema passed to `agy --json-schema` when tools are bound."""
    tool_names = [
        t["function"]["name"]
        for t in bound_tools
        if isinstance(t, dict) and isinstance(t.get("function"), dict) and t["function"].get("name")
    ]
    name_prop: dict[str, Any] = {"type": "string"}
    if tool_names:
        name_prop["enum"] = tool_names

    return {
        "type": "object",
        "properties": {
            "tool_calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": name_prop,
                        "arguments": {"type": "object"},
                    },
                    "required": ["name", "arguments"],
                },
            },
            "final_answer": {"type": "string"},
        },
        "required": ["tool_calls", "final_answer"],
    }


def _parse_cli_output(stdout: str) -> dict[str, Any]:
    """Parse `agy --output-format json` stdout into a normalized dict."""
    text = (stdout or "").strip()
    if not text:
        return {"response": "", "structured_output": None, "usage": {}}

    # Try parsing the full stdout as JSON first.
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except ValueError:
        pass

    # If diagnostic lines preceded the JSON object on stdout, locate the outermost JSON object.
    last_brace = text.rfind("}")
    if last_brace != -1:
        for idx in range(text.find("{"), last_brace + 1):
            if idx != -1 and text[idx] == "{":
                candidate = text[idx : last_brace + 1]
                try:
                    data = json.loads(candidate)
                    if isinstance(data, dict):
                        return data
                except ValueError:
                    continue

    return {"response": text, "structured_output": None, "usage": {}}


def _extract_usage_metadata(usage: Any) -> dict[str, int] | None:
    """Convert `agy` usage telemetry into LangChain `usage_metadata`."""
    if not isinstance(usage, dict):
        return None
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (input_tokens + output_tokens))
    if not (input_tokens or output_tokens or total_tokens):
        return None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


class ChatAntigravityCLI(BaseChatModel):
    """LangChain chat model backed by the Google Antigravity CLI (`agy`)."""

    model: str = "gemini-3.8-flash"
    cli_path: str | None = None
    effort: str | None = None
    timeout: int = REQUEST_TIMEOUT_SECONDS
    max_retries: int = 2
    bound_tools: list[dict[str, Any]] = Field(default_factory=list)
    structured_schema: dict[str, Any] | None = None

    @property
    def _llm_type(self) -> str:
        return "antigravity-cli"

    def _build_command(self, prompt: str, json_schema: dict[str, Any] | None = None) -> list[str]:
        binary = find_agy_binary(self.cli_path)
        cmd = [binary, "-p", prompt, "--output-format", "json"]
        if self.model and self.model.lower() not in ("default", "inherit"):
            cmd.extend(["--model", self.model])
        normalized_effort = _normalize_effort(self.effort)
        if normalized_effort:
            cmd.extend(["--effort", normalized_effort])
        if json_schema is not None:
            cmd.extend(["--json-schema", json.dumps(json_schema, ensure_ascii=False)])
        return cmd

    def _execute_cli(self, cmd: list[str]) -> dict[str, Any]:
        last_err: Exception | None = None
        attempts = max(1, int(self.max_retries) + 1)
        for attempt in range(attempts):
            try:
                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    cwd=tempfile.gettempdir(),
                    check=False,
                )
                if completed.returncode != 0:
                    stderr = (completed.stderr or completed.stdout or "").strip()
                    raise RuntimeError(
                        f"Antigravity CLI exited with code {completed.returncode}: {stderr}"
                    )
                payload = _parse_cli_output(completed.stdout)
                status = payload.get("status")
                if isinstance(status, str) and status.upper() not in ("SUCCESS", "OK", "DONE"):
                    err_detail = payload.get("error") or payload.get("response") or status
                    raise RuntimeError(f"Antigravity CLI returned status {status!r}: {err_detail}")
                return payload
            except Exception as exc:
                last_err = exc
                if attempt + 1 < attempts:
                    logger.warning(
                        "Antigravity CLI call failed (attempt %d/%d): %s; retrying...",
                        attempt + 1,
                        attempts,
                        exc,
                    )
        raise RuntimeError(f"Antigravity CLI invocation failed: {last_err}") from last_err

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        prompt = _format_messages_for_cli(messages, self.bound_tools)

        active_schema = self.structured_schema
        if active_schema is None and self.bound_tools:
            active_schema = _build_tool_dispatch_schema(self.bound_tools)

        cmd = self._build_command(prompt, json_schema=active_schema)
        payload = self._execute_cli(cmd)

        usage_metadata = _extract_usage_metadata(payload.get("usage"))
        structured_data = payload.get("structured_output")
        response_text = payload.get("response")
        if not isinstance(response_text, str):
            response_text = json.dumps(response_text, ensure_ascii=False) if response_text is not None else ""

        # If structured_output was not populated separately by the CLI, try parsing response_text
        if structured_data is None and active_schema is not None and response_text.strip():
            try:
                parsed_candidate = json.loads(response_text)
                if isinstance(parsed_candidate, dict):
                    structured_data = parsed_candidate
            except ValueError:
                pass

        if self.bound_tools and isinstance(structured_data, dict):
            raw_calls = structured_data.get("tool_calls") or []
            final_answer = structured_data.get("final_answer") or ""
            lc_tool_calls = []
            for tc in raw_calls:
                if not isinstance(tc, dict) or not tc.get("name"):
                    continue
                args = tc.get("arguments")
                if not isinstance(args, dict):
                    args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
                lc_tool_calls.append(
                    {
                        "name": str(tc["name"]),
                        "args": args,
                        "id": str(tc.get("id") or f"call_{uuid.uuid4().hex[:12]}"),
                        "type": "tool_call",
                    }
                )
            msg = AIMessage(
                content="" if lc_tool_calls else str(final_answer),
                tool_calls=lc_tool_calls,
                usage_metadata=usage_metadata,
            )
            return ChatResult(generations=[ChatGeneration(message=msg)])

        additional_kwargs: dict[str, Any] = {}
        if structured_data is not None:
            additional_kwargs["structured_output"] = structured_data
            if not response_text:
                response_text = json.dumps(structured_data, ensure_ascii=False)

        msg = AIMessage(
            content=response_text,
            additional_kwargs=additional_kwargs,
            usage_metadata=usage_metadata,
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any],
        *,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        """Bind LangChain tools so `agy` can emit structured tool calls for LangGraph's ToolNode."""
        formatted_tools = [convert_to_openai_tool(tool) for tool in tools]
        return self.model_copy(update={"bound_tools": formatted_tools})

    def with_structured_output(
        self,
        schema: dict[str, Any] | type[BaseModel],
        *,
        include_raw: bool = False,
        **kwargs: Any,
    ) -> Runnable[Any, Any]:
        """Enforce a Pydantic schema or JSON schema via `agy --json-schema`."""
        is_pydantic = isinstance(schema, type) and issubclass(schema, BaseModel)
        schema_dict = schema.model_json_schema() if is_pydantic else dict(schema)
        bound = self.model_copy(update={"structured_schema": schema_dict})

        def _parse_message(message: AIMessage) -> Any:
            raw_data = message.additional_kwargs.get("structured_output")
            err: Exception | None = None
            parsed: Any = None
            try:
                if raw_data is not None:
                    parsed = schema.model_validate(raw_data) if is_pydantic else raw_data
                else:
                    text = (message.content or "").strip()
                    parsed = (
                        schema.model_validate_json(text)
                        if is_pydantic
                        else json.loads(text)
                    )
            except Exception as exc:
                err = exc
                if not include_raw:
                    raise ValueError(f"Failed to parse structured output from Antigravity CLI: {exc}") from exc

            if include_raw:
                return {"raw": message, "parsed": parsed, "parsing_error": err}
            return parsed

        return bound | RunnableLambda(_parse_message)


class AntigravityClient(BaseLLMClient):
    """Client for Google Antigravity CLI (`agy`)."""

    def __init__(self, model: str, base_url: str | None = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """Return configured ChatAntigravityCLI instance."""
        self.warn_if_unknown_model()

        effort = (
            self.kwargs.get("effort")
            or self.kwargs.get("thinking_level")
            or self.kwargs.get("reasoning_effort")
        )
        cli_path = self.kwargs.get("cli_path") or (self.base_url if self.base_url else None)

        llm_kwargs: dict[str, Any] = {
            "model": self.model,
            "cli_path": cli_path,
            "effort": effort,
        }
        if "timeout" in self.kwargs and self.kwargs["timeout"] is not None:
            llm_kwargs["timeout"] = int(self.kwargs["timeout"])
        if "max_retries" in self.kwargs and self.kwargs["max_retries"] is not None:
            llm_kwargs["max_retries"] = int(self.kwargs["max_retries"])
        if "callbacks" in self.kwargs and self.kwargs["callbacks"] is not None:
            llm_kwargs["callbacks"] = self.kwargs["callbacks"]

        return ChatAntigravityCLI(**llm_kwargs)

    def validate_model(self) -> bool:
        """Validate model for Antigravity CLI."""
        return validate_model("antigravity", self.model)
