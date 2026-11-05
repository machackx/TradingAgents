"""OpenAI Codex CLI (`codex`) LLM client for TradingAgents.

Wraps OpenAI's Codex CLI (`codex exec ...`) as a LangChain `BaseChatModel` so
TradingAgents can run multi-agent analyses—including analyst tool-calling loops
and manager structured outputs—through a local signed-in Codex CLI session.
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
from copy import deepcopy
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

_CANDIDATE_PATHS = (
    Path.home() / ".local" / "bin" / "codex",
    Path("/opt/homebrew/bin/codex"),
    Path("/usr/local/bin/codex"),
    Path.home() / ".npm-global" / "bin" / "codex",
    Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"),
    Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"),
    Path("/Applications/Codex.app/Contents/Resources/bin/codex"),
)


def _strict_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Close every object and require all fields for Codex's strict output format.

    Keep the caller's schema intact, including Pydantic defaults used locally.
    Nullable fields retain their null alternatives in the wire schema.
    """
    result = deepcopy(schema)

    def normalize(node: dict[str, Any]) -> None:
        node.pop("default", None)
        if node.get("type") == "object" or "properties" in node:
            properties = node.setdefault("properties", {})
            node["additionalProperties"] = False
            node["required"] = list(properties)
        for key in ("properties", "$defs", "definitions"):
            for child in node.get(key, {}).values():
                normalize(child)
        items = node.get("items")
        if isinstance(items, dict):
            normalize(items)
        for key in ("anyOf", "allOf", "oneOf"):
            for child in node.get(key, []):
                normalize(child)

    normalize(result)
    return result


def _build_codex_tool_dispatch_schema(bound_tools: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Use one variant per tool so closing objects preserves its real arguments."""
    schema = _build_tool_dispatch_schema(bound_tools)
    schema["properties"]["tool_calls"]["items"] = {
        "anyOf": [
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": [tool["function"]["name"]]},
                    "arguments": tool["function"].get("parameters", {"type": "object", "properties": {}}),
                },
            }
            for tool in bound_tools
        ],
    }
    return schema


def find_codex_binary(cli_path: str | None = None) -> str:
    """Resolve the `codex` executable path or raise a RuntimeError with install steps."""
    explicit = cli_path or os.environ.get("CODEX_CLI_PATH")
    if explicit:
        expanded = os.path.expanduser(explicit)
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return expanded
        found = shutil.which(expanded)
        if found:
            return found
        raise RuntimeError(
            f"Codex CLI binary not found at configured path: {explicit!r}. "
            "Check CODEX_CLI_PATH or install `codex`."
        )

    found = shutil.which("codex")
    if found:
        return found

    for candidate in _CANDIDATE_PATHS:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    raise RuntimeError(
        "OpenAI Codex CLI ('codex') was not found on this machine. "
        "Install it with:\n"
        "  npm install -g @openai/codex\n"
        "or:\n"
        "  brew install --cask codex\n"
        "Then run `codex login` once to sign in with your subscription, "
        "or set CODEX_CLI_PATH to the binary path."
    )


class ChatCodexCLI(BaseChatModel):
    """LangChain chat model backed by the OpenAI Codex CLI (`codex`)."""

    model: str = "gpt-6-luna"
    cli_path: str | None = None
    effort: str | None = None
    timeout: int = REQUEST_TIMEOUT_SECONDS
    max_retries: int = 2
    bound_tools: list[dict[str, Any]] = Field(default_factory=list)
    structured_schema: dict[str, Any] | None = None

    @property
    def _llm_type(self) -> str:
        return "codex-cli"

    def _build_command(self, prompt: str, schema_path: str | None = None) -> list[str]:
        binary = find_codex_binary(self.cli_path)
        cmd = [binary, "exec", "--skip-git-repo-check", "--sandbox", "read-only"]
        if self.model and self.model.lower() not in ("default", "inherit"):
            cmd.extend(["--model", self.model])
        normalized_effort = _normalize_effort(self.effort)
        if normalized_effort:
            cmd.extend(["-c", f'model_reasoning_effort="{normalized_effort}"'])
        if schema_path is not None:
            cmd.extend(["--output-schema", schema_path])
        cmd.append(prompt)
        return cmd

    def _execute_cli(self, prompt: str, json_schema: dict[str, Any] | None = None) -> dict[str, Any]:
        wire_schema = _strict_output_schema(json_schema) if json_schema is not None else None
        last_err: Exception | None = None
        attempts = max(1, int(self.max_retries) + 1)
        for attempt in range(attempts):
            schema_file: str | None = None
            try:
                if wire_schema is not None:
                    fd, schema_file = tempfile.mkstemp(prefix="codex_schema_", suffix=".json")
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        json.dump(wire_schema, handle, ensure_ascii=False)
                cmd = self._build_command(prompt, schema_path=schema_file)
                completed = subprocess.run(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    cwd=tempfile.gettempdir(),
                    check=False,
                )
                if completed.returncode != 0:
                    stderr = (completed.stderr or completed.stdout or "").strip()
                    raise RuntimeError(
                        f"Codex CLI exited with code {completed.returncode}: {stderr}"
                    )
                payload = _parse_cli_output(completed.stdout)
                status = payload.get("status")
                if isinstance(status, str) and status.upper() not in ("SUCCESS", "OK", "DONE"):
                    err_detail = payload.get("error") or payload.get("response") or status
                    raise RuntimeError(f"Codex CLI returned status {status!r}: {err_detail}")
                return payload
            except Exception as exc:
                last_err = exc
                if attempt + 1 < attempts:
                    logger.warning(
                        "Codex CLI call failed (attempt %d/%d): %s; retrying...",
                        attempt + 1,
                        attempts,
                        exc,
                    )
            finally:
                if schema_file:
                    try:
                        os.unlink(schema_file)
                    except OSError:
                        pass
        raise RuntimeError(f"Codex CLI invocation failed: {last_err}") from last_err

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
            active_schema = _build_codex_tool_dispatch_schema(self.bound_tools)

        payload = self._execute_cli(prompt, json_schema=active_schema)

        usage_metadata = _extract_usage_metadata(payload.get("usage"))
        structured_data = payload.get("structured_output")
        response_text = payload.get("response")
        if response_text is None and active_schema is not None and isinstance(payload, dict):
            # When codex exec outputs raw JSON matching the schema directly at top-level
            if "tool_calls" in payload or any(k in payload for k in (active_schema.get("properties") or {})):
                structured_data = payload
                response_text = json.dumps(payload, ensure_ascii=False)
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
        """Bind LangChain tools so `codex` can emit structured tool calls for LangGraph's ToolNode."""
        formatted_tools = [convert_to_openai_tool(tool) for tool in tools]
        return self.model_copy(update={"bound_tools": formatted_tools})

    def with_structured_output(
        self,
        schema: dict[str, Any] | type[BaseModel],
        *,
        include_raw: bool = False,
        **kwargs: Any,
    ) -> Runnable[Any, Any]:
        """Enforce a Pydantic schema or JSON schema via `codex exec --output-schema`."""
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
                    raise ValueError(f"Failed to parse structured output from Codex CLI: {exc}") from exc

            if include_raw:
                return {"raw": message, "parsed": parsed, "parsing_error": err}
            return parsed

        return bound | RunnableLambda(_parse_message)


class CodexCLIClient(BaseLLMClient):
    """Client for OpenAI Codex CLI (`codex`)."""

    def __init__(self, model: str, base_url: str | None = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """Return configured ChatCodexCLI instance."""
        self.warn_if_unknown_model()

        effort = (
            self.kwargs.get("effort")
            or self.kwargs.get("reasoning_effort")
            or self.kwargs.get("thinking_level")
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

        return ChatCodexCLI(**llm_kwargs)

    def validate_model(self) -> bool:
        """Validate model for Codex CLI."""
        return validate_model("codex", self.model)
