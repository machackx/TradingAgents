"""Local subscription accounts and cancellable browser sign-ins.

Only public account metadata and authorization URLs cross the HTTP boundary.
ChatGPT runs its existing PKCE flow in a child process; Codex uses the official
stdio app-server account API, sharing the same credential store as codex exec.
Neither flow creates a model turn or sends a model request.
"""

from __future__ import annotations

import copy
import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from tradingagents.llm_clients import chatgpt_auth
from tradingagents.llm_clients.codex_cli_client import find_codex_binary

PROVIDERS = ("chatgpt", "codex")
PENDING = {"starting", "pending"}
LOGIN_TIMEOUT = 600
RPC_TIMEOUT = 15
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class AuthError(RuntimeError):
    """A message safe to display in the browser."""


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def _authorization_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.hostname not in {"auth.openai.com", "chatgpt.com"}
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise AuthError("登录服务返回了无效的授权地址，请重试。")
    return value


class CodexRPC:
    """One private JSONL connection; bounded waits and no raw stderr in the UI."""

    def __init__(self, on_start: Callable | None = None):
        self.process = subprocess.Popen(
            [find_codex_binary(), "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", cwd=tempfile.gettempdir(), bufsize=1,
        )
        self._messages: queue.Queue = queue.Queue()
        self._notifications: deque = deque()
        self._id = 0
        self._reader = threading.Thread(target=self._read, daemon=True, name="codex-auth-rpc")
        self._reader.start()
        try:
            if on_start:
                on_start(self.process)
            self.request("initialize", {"clientInfo": {
                "name": "tradingagents", "title": "TradingAgents", "version": "0.1.0",
            }})
            self._send({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def _read(self) -> None:
        try:
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if isinstance(message, dict):
                    self._messages.put(message)
        finally:
            self._messages.put(None)

    def _send(self, message: dict) -> None:
        try:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError):
            raise AuthError("Codex 登录服务已关闭，请重试。") from None

    def receive(self, timeout: float) -> dict:
        try:
            message = self._messages.get(timeout=max(0, timeout))
        except queue.Empty:
            raise AuthError("Codex 登录服务响应超时，请重试。") from None
        if message is None:
            raise AuthError("Codex 登录服务已关闭，请检查本机 Codex 配置或更新 CLI。")
        return message

    def request(self, method: str, params: dict) -> dict:
        self._id += 1
        request_id = self._id
        self._send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + RPC_TIMEOUT
        while True:
            message = self.receive(deadline - time.monotonic())
            if message.get("id") != request_id:
                if "method" in message:
                    self._notifications.append(message)
                continue
            if "error" in message:
                raise AuthError("Codex 无法完成账号操作，请检查本机配置、登录限制或更新 CLI。")
            result = message.get("result")
            if not isinstance(result, dict):
                raise AuthError("Codex 返回了无效的账号信息，请更新 CLI。")
            return result

    def wait_login(self, login_id: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            message = (self._notifications.popleft() if self._notifications
                       else self.receive(deadline - time.monotonic()))
            if message.get("method") == "account/login/completed":
                params = message.get("params") or {}
                if params.get("loginId") != login_id:
                    continue
                if not params.get("success"):
                    raise AuthError("Codex 授权未完成，请重新登录。")
                return

    def close(self) -> None:
        _stop(self.process)
        self._reader.join(timeout=2)
        self.process.stdin.close()
        self.process.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class AuthManager:
    """One login at a time; the server shares ``lock`` with run creation."""

    def __init__(self):
        self.lock = threading.RLock()
        self._login: dict | None = None
        self._process: subprocess.Popen | None = None
        self._cache_lock = threading.Lock()
        self._codex_cache: tuple[float, dict] | None = None
        self._cache_version = 0

    @property
    def pending(self) -> bool:
        with self.lock:
            return self._login is not None and self._login["status"] in PENDING

    def login_status(self) -> dict | None:
        with self.lock:
            return copy.deepcopy(self._login)

    def accounts(self, *, refresh: bool = False) -> list[dict]:
        info = chatgpt_auth.account_info()
        chatgpt = {"provider": "chatgpt", "label": "ChatGPT 订阅", "installed": True,
                   "auth_mode": "chatgpt" if info["authenticated"] else None,
                   "plan": None, "error": None, **info}
        with self._cache_lock:
            cached = self._codex_cache
            if refresh or cached is None or time.monotonic() - cached[0] > 10:
                version = self._cache_version
                codex = self._codex_account()
                if version == self._cache_version:
                    self._codex_cache = (time.monotonic(), codex)
            else:
                codex = cached[1]
            return [chatgpt, dict(codex)]

    def _invalidate(self) -> None:
        # Do not wait for a status request while holding the operation lock.
        self._cache_version += 1
        self._codex_cache = None

    @staticmethod
    def _codex_account() -> dict:
        result = {"provider": "codex", "label": "Codex CLI", "installed": False,
                  "authenticated": False, "email": None, "auth_mode": None,
                  "plan": None, "error": None}
        try:
            find_codex_binary()
        except RuntimeError:
            return result
        result["installed"] = True
        try:
            with CodexRPC() as rpc:
                account = rpc.request("account/read", {"refreshToken": False}).get("account")
            if account:
                result.update(authenticated=True, email=account.get("email"),
                              auth_mode=account.get("type"), plan=account.get("planType"))
        except Exception:
            result["error"] = "无法读取 Codex 账号，请检查本机配置或更新 CLI 后刷新。"
        return result

    def start_login(self, provider: str) -> dict:
        with self.lock:
            if self.pending:
                raise AuthError("已有登录正在进行，请先完成或取消。")
            if provider == "codex":
                try:
                    find_codex_binary()
                except RuntimeError:
                    raise AuthError("未检测到 Codex CLI，请先安装 Codex。") from None
            self._login = {"id": uuid.uuid4().hex, "provider": provider, "status": "starting",
                           "auth_url": None, "expires_at": time.time() + LOGIN_TIMEOUT,
                           "error": None}
            attempt = self._login
            threading.Thread(target=self._login_worker, args=(attempt,), daemon=True,
                             name=f"{provider}-web-login").start()
            return dict(attempt)

    def _track(self, attempt: dict, process: subprocess.Popen) -> None:
        with self.lock:
            if attempt is not self._login or attempt["status"] not in PENDING:
                _stop(process)
                raise AuthError("登录已取消。")
            self._process = process

    def _publish_url(self, attempt: dict, url: str) -> None:
        url = _authorization_url(url)
        with self.lock:
            if attempt["status"] in PENDING:
                attempt.update(status="pending", auth_url=url)

    def _login_worker(self, attempt: dict) -> None:
        try:
            if attempt["provider"] == "chatgpt":
                self._chatgpt_login(attempt)
            else:
                with CodexRPC(on_start=lambda process: self._track(attempt, process)) as rpc:
                    result = rpc.request("account/login/start", {"type": "chatgpt"})
                    self._publish_url(attempt, result["authUrl"])
                    rpc.wait_login(result["loginId"], LOGIN_TIMEOUT)
            with self.lock:
                if attempt["status"] in PENDING:
                    self._invalidate()
                    attempt.update(status="succeeded", auth_url=None)
        except Exception as exc:
            with self.lock:
                if attempt["status"] in PENDING:
                    message = str(exc) if isinstance(exc, AuthError) else "登录未完成，请重试。"
                    attempt.update(status="failed", error=message, auth_url=None)
        finally:
            with self.lock:
                if attempt is self._login:
                    self._process = None
                self._invalidate()

    def _chatgpt_login(self, attempt: dict) -> None:
        process = subprocess.Popen(
            [sys.executable, "-u", "-m", "webapp.auth"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", cwd=PROJECT_ROOT,
        )
        try:
            self._track(attempt, process)
            completed = False
            for line in process.stdout:
                message = json.loads(line)
                if message.get("auth_url"):
                    self._publish_url(attempt, message["auth_url"])
                if message.get("error"):
                    raise AuthError(message["error"])
                completed = completed or message.get("completed", False)
            if process.wait(timeout=2) != 0 or not completed:
                raise AuthError("ChatGPT 登录未完成或已超时，请重试。")
        finally:
            _stop(process)
            process.stdout.close()

    def cancel(self, login_id: str) -> None:
        with self.lock:
            if not self._login or self._login["id"] != login_id:
                raise AuthError("此登录已结束或不存在，请刷新账号状态。")
            if self.pending:
                self._login.update(status="cancelled", auth_url=None)
                if self._process is not None:
                    _stop(self._process)
                self._invalidate()

    def logout(self, provider: str) -> str:
        with self.lock:
            if self.pending:
                raise AuthError("请先完成或取消正在进行的登录。")
            message = "已退出登录。"
            if provider == "chatgpt":
                outcome = chatgpt_auth.sign_out()
                if outcome == "forgotten":
                    message = "已退出本机账号；未能确认远程授权撤销，可在 ChatGPT 设置中断开应用。"
            else:
                try:
                    with CodexRPC() as rpc:
                        rpc.request("account/logout", {})
                except Exception:
                    raise AuthError("Codex 退出失败，请检查本机配置或更新 CLI 后重试。") from None
            self._invalidate()
            if self._login and self._login["provider"] == provider:
                self._login = None
            return message

    def close(self) -> None:
        with self.lock:
            if self.pending:
                self.cancel(self._login["id"])


def _chatgpt_worker() -> None:
    def say(line: str) -> None:
        if line.startswith(chatgpt_auth.AUTHORIZE_URL + "?"):
            print(json.dumps({"auth_url": line}), flush=True)

    try:
        chatgpt_auth.sign_in(say=say, read_line=None, timeout=LOGIN_TIMEOUT)
    except chatgpt_auth.ChatGPTUnreachable:
        print(json.dumps({"error": "暂时无法连接 OpenAI，请检查网络后重试。"}), flush=True)
        raise SystemExit(1) from None
    except Exception:
        print(json.dumps({"error": "ChatGPT 授权未完成或已超时，请重试；切换账号请先退出登录。"}), flush=True)
        raise SystemExit(1) from None
    print(json.dumps({"completed": True}), flush=True)


if __name__ == "__main__":
    _chatgpt_worker()
