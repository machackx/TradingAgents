"""Claude Code CLI (`claude`) LLM client for TradingAgents.

Wraps Anthropic's Claude Code CLI (`claude -p ... --output-format json`) as a
LangChain `BaseChatModel` so TradingAgents can run multi-agent analyses—including
analyst tool-calling loops and manager structured outputs—through a local
signed-in Claude subscription without an API key.
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
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, Field

from .antigravity_client import (
    REQUEST_TIMEOUT_SECONDS,
    _build_tool_dispatch_schema,
    _extract_usage_metadata,
    _format_messages_for_cli,
    _normalize_effort,
    _parse_cli_output,
)
from .base_client import BaseLLMClient
from .validators import validate_model

logger = logging.getLogger(__name__)

def _default_claude_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = [
        Path.home() / ".local" / "bin" / "claude",
        Path.home() / ".claude" / "local" / "claude",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
        Path.home() / ".npm-global" / "bin" / "claude",
        Path("/Applications/Claude.app/Contents/Helpers/claude"),
    ]
    app_support = Path.home() / "Library" / "Application Support" / "Claude" / "claude-code"
    if app_support.is_dir():
        bundled = sorted(
            app_support.glob("*/*/claude.app/Contents/MacOS/claude"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
            reverse=True,
        )
        candidates.extend(bundled)
    return tuple(candidates)


_CANDIDATE_PATHS = _default_claude_candidates()


def find_claude_binary(cli_path: str | None = None) -> str:
    """Resolve the `claude` executable path or raise a RuntimeError with install steps."""
    explicit = cli_path or os.environ.get("CLAUDE_CLI_PATH")
    if explicit:
        expanded = os.path.expanduser(explicit)
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return expanded
        found = shutil.which(expanded)
        if found:
            return found
        raise RuntimeError(
            f"Claude CLI binary not found at configured path: {explicit!r}. "
            "Check CLAUDE_CLI_PATH or install `claude`."
        )

    found = shutil.which("claude")
    if found:
        return found

    for candidate in _CANDIDATE_PATHS:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    raise RuntimeError(
        "Claude Code CLI ('claude') was not found on this machine. "
        "Install it with:\n"
        "  npm install -g @anthropic-ai/claude-code\n"
        "or:\n"
        "  brew install --cask claude-code\n"
        "Then run `claude` once to sign in with your Claude subscription, "
        "or set CLAUDE_CLI_PATH to the binary path."
    )


class ChatClaudeCLI(BaseChatModel):
    """LangChain chat model backed by the Anthropic Claude Code CLI (`claude`)."""

    model: str = "claude-sonnet-5"
    cli_path: str | None = None
    effort: str | None = None
    timeout: int = REQUEST_TIMEOUT_SECONDS
    max_retries: int = 2
    bound_tools: list[dict[str, Any]] = Field(default_factory=list)
    structured_schema: dict[str, Any] | None = None

    @property
    def _llm_type(self) -> str:
        return "claude-cli"

    def _build_command(self, prompt: str, json_schema: dict[str, Any] | None = None) -> list[str]:
        binary = find_claude_binary(self.cli_path)
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
                        f"Claude CLI exited with code {completed.returncode}: {stderr}"
                    )
                payload = _parse_cli_output(completed.stdout)
                if payload.get("is_error") is True:
                    err_detail = payload.get("error") or payload.get("result") or payload.get("response") or "error"
                    raise RuntimeError(f"Claude CLI returned error: {err_detail}")
                status = payload.get("status")
                if isinstance(status, str) and status.upper() not in ("SUCCESS", "OK", "DONE"):
                    err_detail = payload.get("error") or payload.get("result") or payload.get("response") or status
                    raise RuntimeError(f"Claude CLI returned status {status!r}: {err_detail}")
                return payload
            except Exception as exc:
                last_err = exc
                if attempt + 1 < attempts:
                    logger.warning(
                        "Claude CLI call failed (attempt %d/%d): %s; retrying...",
                        attempt + 1,
                        attempts,
                        exc,
                    )
        raise RuntimeError(f"Claude CLI invocation failed: {last_err}") from last_err

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
        response_text = payload.get("result") if "result" in payload else payload.get("response")
        if not isinstance(response_text, str):
            response_text = json.dumps(response_text, ensure_ascii=False) if response_text is not None else ""

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
        """Bind LangChain tools so `claude` can emit structured tool calls for LangGraph's ToolNode."""
        formatted_tools = [convert_to_openai_tool(tool) for tool in tools]
        return self.model_copy(update={"bound_tools": formatted_tools})

    def with_structured_output(
        self,
        schema: dict[str, Any] | type[BaseModel],
        *,
        include_raw: bool = False,
        **kwargs: Any,
    ) -> Runnable[Any, Any]:
        """Enforce a Pydantic schema or JSON schema via `claude --json-schema`."""
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
                    raise ValueError(f"Failed to parse structured output from Claude CLI: {exc}") from exc

            if include_raw:
                return {"raw": message, "parsed": parsed, "parsing_error": err}
            return parsed

        return bound | RunnableLambda(_parse_message)


class ClaudeCLIClient(BaseLLMClient):
    """Client for Anthropic Claude Code CLI (`claude`)."""

    def __init__(self, model: str, base_url: str | None = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """Return configured ChatClaudeCLI instance."""
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

        return ChatClaudeCLI(**llm_kwargs)

    def validate_model(self) -> bool:
        """Validate model for Claude CLI."""
        return validate_model("claude-cli", self.model)
