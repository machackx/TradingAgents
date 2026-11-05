"""Account management uses isolated fake executables, never real credentials."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.llm_clients import chatgpt_auth  # noqa: E402
from webapp import auth  # noqa: E402
from webapp.server import create_app  # noqa: E402

pytestmark = pytest.mark.unit
HEADERS = {"X-TradingAgents-Auth": "1"}
SECRET = "a-secret-that-must-never-reach-the-browser"


def _eventually(read, accept, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if accept(value):
            return value
        time.sleep(0.01)
    pytest.fail(f"condition not reached; last value: {value!r}")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(chatgpt_auth, "AUTH_FILE", tmp_path / "chatgpt.json")
    monkeypatch.setattr(chatgpt_auth, "_revoke", lambda *args: True)
    monkeypatch.setattr(auth, "GEMINI_DIRECTORY", tmp_path / "gemini")

    def missing():
        raise RuntimeError("not installed in the test sandbox")

    monkeypatch.setattr(auth, "find_claude_binary", missing)
    monkeypatch.setattr(auth, "find_agy_binary", missing)
    state = tmp_path / "codex.json"
    state.write_text(json.dumps({"account": None, "mode": "success"}))
    journal = tmp_path / "rpc.jsonl"
    script = tmp_path / "codex"
    script.write_text(f"#!{sys.executable}\n" + '''
import json, pathlib, sys, time
state = pathlib.Path(sys.argv[0]).with_name("codex.json")
journal = state.with_name("rpc.jsonl")
initialized = False
def send(message):
    print(json.dumps(message), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    with journal.open("a") as out:
        out.write(json.dumps(request) + "\\n")
    method = request["method"]
    if method == "initialized":
        initialized = True
        continue
    current = json.loads(state.read_text())
    if method != "initialize" and not initialized:
        send({"id": request["id"], "error": {"message": "handshake missing"}})
        continue
    if current["mode"] == "rpc_error" and method != "initialize":
        send({"id": request["id"], "error": {"message": "''' + SECRET + '''"}})
        continue
    if current["mode"] == "slow" and method == "account/read":
        time.sleep(30)
    result = {}
    if method == "account/read":
        result = {"account": current["account"], "access_token": "''' + SECRET + '''"}
    elif method == "account/login/start":
        result = {"authUrl": "https://auth.openai.com/authorize?state=test", "loginId": "login-1"}
        if current["mode"] == "bad_url":
            result["authUrl"] = "https://evil.example/authorize"
    elif method == "account/logout":
        current["account"] = None
        state.write_text(json.dumps(current))
    send({"id": request["id"], "result": result})
    if method == "account/login/start" and current["mode"] != "pending":
        if current["mode"] == "success":
            current["account"] = {"type": "chatgpt", "email": "codex-new@example.com", "planType": "pro"}
            state.write_text(json.dumps(current))
        send({"method": "account/login/completed", "params": {"loginId": "old-login", "success": False}})
        send({"method": "account/login/completed", "params": {
            "loginId": "login-1", "success": current["mode"] == "success", "error": "''' + SECRET + '''"}})
''')
    script.chmod(0o700)
    monkeypatch.setattr(auth, "find_codex_binary", lambda: str(script))
    return {"root": tmp_path, "state": state, "journal": journal, "script": script}


@pytest.fixture
def client(sandbox):
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["results_dir"] = str(sandbox["root"] / "results")
    with TestClient(create_app(config), base_url="http://localhost") as client:
        yield client


def _state(sandbox, **updates):
    data = json.loads(sandbox["state"].read_text())
    sandbox["state"].write_text(json.dumps({**data, **updates}))


def _accounts(client):
    response = client.get("/api/auth?refresh=true")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert SECRET not in response.text
    return response.json()


def _finished(client):
    return _eventually(lambda: client.get("/api/auth").json(),
                       lambda state: state["login"]["status"] not in auth.PENDING)


def test_current_accounts_are_reported_without_credentials(client, sandbox):
    chatgpt_auth._write({"account": {"email": "gpt@example.com"},
                         "tokens": {"access_token": SECRET, "refresh_token": SECRET}})
    _state(sandbox, account={"type": "chatgpt", "email": "codex@example.com", "planType": "plus"})

    state = _accounts(client)
    gpt, codex = state["accounts"][:2]
    assert [account["provider"] for account in state["accounts"]] == list(auth.PROVIDERS)
    assert gpt["authenticated"] and gpt["email"] == "gpt@example.com"
    assert codex["authenticated"] and codex["email"] == "codex@example.com"
    assert codex["plan"] == "plus" and state["login"] is None
    requests = [json.loads(line) for line in sandbox["journal"].read_text().splitlines()]
    assert [r["method"] for r in requests] == ["initialize", "initialized", "account/read"]
    assert requests[-1]["params"] == {"refreshToken": False}


def test_api_key_mode_is_distinct_from_a_subscription(client, sandbox):
    _state(sandbox, account={"type": "apiKey"})
    account = _accounts(client)["accounts"][1]
    assert account["authenticated"] and account["auth_mode"] == "apiKey"
    assert account["email"] is None and account["plan"] is None


def test_codex_login_completes_and_logout_updates_the_same_account_store(client, sandbox):
    assert client.post("/api/auth/codex/login", headers=HEADERS).status_code == 202
    state = _finished(client)
    assert state["login"]["status"] == "succeeded"
    assert state["login"]["auth_url"] is None
    assert _accounts(client)["accounts"][1]["email"] == "codex-new@example.com"

    response = client.post("/api/auth/codex/logout", headers=HEADERS)
    assert response.status_code == 200
    state = _accounts(client)
    assert not state["accounts"][1]["authenticated"] and state["login"] is None


def test_pending_login_survives_page_reload_and_can_be_cancelled(client, sandbox):
    _state(sandbox, mode="pending")
    response = client.post("/api/auth/codex/login", headers=HEADERS)
    login_id = response.json()["login"]["id"]
    state = _eventually(lambda: client.get("/api/auth").json(),
                        lambda s: s["login"]["status"] == "pending")
    assert state["login"]["auth_url"].startswith("https://auth.openai.com/")
    assert client.get("/api/auth").json()["login"]["id"] == login_id
    assert client.post("/api/auth/chatgpt/login", headers=HEADERS).status_code == 409
    assert client.post("/api/auth/codex/logout", headers=HEADERS).status_code == 409
    assert client.post("/api/runs", json={"ticker": "NVDA", "trade_date": "2026-11-04",
                                          "analysts": ["market"]}).status_code == 409
    process = client.app.state.auth._process
    assert process is not None and process.poll() is None
    assert client.post(f"/api/auth/logins/{login_id}/cancel", headers=HEADERS).status_code == 200
    assert process.poll() is not None
    state = _accounts(client)
    assert state["login"]["status"] == "cancelled" and state["login"]["auth_url"] is None
    assert client.post("/api/auth/codex/logout", headers=HEADERS).status_code == 200


@pytest.mark.parametrize("url", [
    "http://auth.openai.com/authorize",
    "https://auth.openai.com.evil.example/authorize",
    "https://evil.example/authorize",
    "https://user:password@auth.openai.com/authorize",
    "https://auth.openai.com:8443/authorize",
    "javascript:alert(1)",
])
def test_authorization_urls_must_belong_to_openai(url):
    with pytest.raises(auth.AuthError):
        auth._authorization_url(url)


def test_cancelled_login_cannot_overwrite_a_later_login(client, sandbox):
    _state(sandbox, mode="pending")
    first = client.post("/api/auth/codex/login", headers=HEADERS).json()["login"]["id"]
    _eventually(client.app.state.auth.login_status, lambda s: s["status"] == "pending")
    client.post(f"/api/auth/logins/{first}/cancel", headers=HEADERS)
    _state(sandbox, mode="success")
    second = client.post("/api/auth/codex/login", headers=HEADERS).json()["login"]["id"]
    assert second != first
    assert client.post(f"/api/auth/logins/{first}/cancel", headers=HEADERS).status_code == 404
    assert _finished(client)["login"]["status"] == "succeeded"
    assert _accounts(client)["accounts"][1]["email"] == "codex-new@example.com"


@pytest.mark.parametrize("mode", ["failure", "bad_url", "rpc_error"])
def test_failed_authorization_does_not_expose_raw_errors_or_urls(client, sandbox, mode):
    _state(sandbox, mode=mode)
    client.post("/api/auth/codex/login", headers=HEADERS)
    state = _finished(client)
    assert state["login"]["status"] == "failed"
    assert state["login"]["auth_url"] is None
    assert SECRET not in json.dumps(state) and "evil.example" not in json.dumps(state)


def test_codex_rpc_timeout_stops_the_child_process(sandbox, monkeypatch):
    _state(sandbox, mode="slow")
    with auth.CodexRPC() as rpc:
        monkeypatch.setattr(auth, "RPC_TIMEOUT", 0.2)
        with pytest.raises(auth.AuthError, match="超时"):
            rpc.request("account/read", {"refreshToken": False})
        process = rpc.process
    assert process.poll() is not None


def test_missing_codex_and_broken_status_are_explicit(client, sandbox, monkeypatch):
    _state(sandbox, mode="rpc_error")
    account = _accounts(client)["accounts"][1]
    assert account["installed"] and account["error"] and not account["authenticated"]

    def missing():
        raise RuntimeError("not installed")

    monkeypatch.setattr(auth, "find_codex_binary", missing)
    account = _accounts(client)["accounts"][1]
    assert not account["installed"] and not account["authenticated"]
    assert client.post("/api/auth/codex/login", headers=HEADERS).status_code == 409


@pytest.mark.parametrize("path", ["/api/auth/chatgpt/login", "/api/auth/chatgpt/logout",
                                  "/api/auth/codex/login", "/api/auth/codex/logout",
                                  "/api/auth/claude-cli/login", "/api/auth/claude-cli/logout",
                                  "/api/auth/antigravity/login", "/api/auth/antigravity/logout",
                                  "/api/auth/logins/unknown/cancel", "/api/auth/logins/unknown/code"])
def test_account_mutations_require_a_non_simple_local_request(client, path):
    assert client.post(path, json={"code": "example"}).status_code == 403
    assert client.post(path, json={"code": "example"}, headers={**HEADERS, "Origin": "https://evil.example"}).status_code == 403
    assert client.post(path, json={"code": "example"}, headers={**HEADERS, "Host": "evil.example"}).status_code == 400


def test_unknown_providers_and_login_ids_are_rejected(client):
    assert client.post("/api/auth/unknown/login", headers=HEADERS).status_code == 404
    assert client.post("/api/auth/logins/unknown/cancel", headers=HEADERS).status_code == 404


def test_account_changes_are_rejected_during_analysis(client):
    from types import SimpleNamespace

    client.app.state.manager._active = SimpleNamespace(run_id="a" * 32, done=False)
    for provider in auth.PROVIDERS:
        for action in ("login", "logout"):
            response = client.post(f"/api/auth/{provider}/{action}", headers=HEADERS)
            assert response.status_code == 409
            assert response.json()["detail"]["active_run"] == "a" * 32


def test_chatgpt_logout_only_revokes_the_project_account(client, sandbox):
    chatgpt_auth._write({"host_id": "host", "account": {"email": "gpt@example.com"},
                         "tokens": {"access_token": SECRET, "refresh_token": SECRET}})
    _state(sandbox, account={"type": "chatgpt", "email": "codex@example.com"})
    assert client.post("/api/auth/chatgpt/logout", headers=HEADERS).status_code == 200
    state = _accounts(client)
    assert not state["accounts"][0]["authenticated"]
    assert state["accounts"][1]["email"] == "codex@example.com"


def test_unconfirmed_remote_revocation_is_reported(client, monkeypatch):
    monkeypatch.setattr(chatgpt_auth, "sign_out", lambda: "forgotten")
    response = client.post("/api/auth/chatgpt/logout", headers=HEADERS)
    assert response.status_code == 200 and "未能确认远程授权撤销" in response.json()["message"]


def test_chatgpt_worker_sends_only_public_protocol_events(monkeypatch, capsys):
    def sign_in(say, read_line, timeout):
        assert read_line is None
        say("private diagnostic " + SECRET)
        say(chatgpt_auth.AUTHORIZE_URL + "?state=opaque")
        return "gpt@example.com"

    monkeypatch.setattr(chatgpt_auth, "sign_in", sign_in)
    auth._chatgpt_worker()
    output = capsys.readouterr().out
    assert SECRET not in output
    assert [json.loads(line) for line in output.splitlines()] == [
        {"auth_url": chatgpt_auth.AUTHORIZE_URL + "?state=opaque"}, {"completed": True},
    ]

    def fail(**kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(chatgpt_auth, "sign_in", fail)
    with pytest.raises(SystemExit):
        auth._chatgpt_worker()
    assert SECRET not in capsys.readouterr().out


def test_chatgpt_subprocess_login_updates_the_public_account(client, sandbox, monkeypatch):
    worker = sandbox["root"] / "gpt_worker.py"
    worker.write_text('''
import json, pathlib, sys
print(json.dumps({"auth_url": "https://auth.openai.com/authorize?state=chatgpt"}), flush=True)
pathlib.Path(sys.argv[1]).write_text(json.dumps({
    "account": {"email": "gpt-new@example.com"}, "tokens": {"access_token": "''' + SECRET + '''"}}))
print(json.dumps({"completed": True}), flush=True)
''')
    original = subprocess.Popen

    def spawn(command, **kwargs):
        if command[:4] == [sys.executable, "-u", "-m", "webapp.auth"]:
            assert kwargs["stdin"] == subprocess.DEVNULL
            assert kwargs["stderr"] == subprocess.DEVNULL
            command = [sys.executable, str(worker), str(chatgpt_auth.AUTH_FILE)]
        return original(command, **kwargs)

    monkeypatch.setattr(auth.subprocess, "Popen", spawn)
    assert client.post("/api/auth/chatgpt/login", headers=HEADERS).status_code == 202
    assert _finished(client)["login"]["status"] == "succeeded"
    assert _accounts(client)["accounts"][0]["email"] == "gpt-new@example.com"


def test_server_shutdown_cancels_a_pending_login(sandbox):
    _state(sandbox, mode="pending")
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["results_dir"] = str(sandbox["root"] / "results")
    with TestClient(create_app(config), base_url="http://localhost") as client:
        client.post("/api/auth/codex/login", headers=HEADERS)
        _eventually(client.app.state.auth.login_status, lambda s: s["status"] == "pending")
        process = client.app.state.auth._process
    assert process.poll() is not None


@pytest.fixture
def subscription_clis(sandbox, monkeypatch):
    root = sandbox["root"]
    gemini = auth.GEMINI_DIRECTORY
    gemini.mkdir()
    credential = gemini / "jetski-standalone-oauth-token"
    credential.write_text(json.dumps({
        "id_token_email": "google-old@example.com", "auth_method": "oauth-personal",
        "token": {"access_token": SECRET, "refresh_token": SECRET},
    }))
    claude_state = root / "claude-state.json"
    claude_state.write_text(json.dumps({"loggedIn": True, "email": "claude-old@example.com",
                                       "authMethod": "claude.ai", "subscriptionType": "pro"}))
    script = root / "subscription-cli"
    script.write_text(f"#!{sys.executable}\n" + '''
import json, pathlib, sys
root = pathlib.Path(sys.argv[0]).parent
claude = root / "claude-state.json"
google = root / "gemini" / "jetski-standalone-oauth-token"
args = sys.argv[1:]
def write_account():
    if args[0] == "auth":
        claude.write_text(json.dumps({"loggedIn": True, "email": "claude-new@example.com",
            "authMethod": "claude.ai", "subscriptionType": "pro", "token": "''' + SECRET + '''"}))
    else:
        google.write_text(json.dumps({"id_token_email": "google-new@example.com",
            "auth_method": "oauth-personal", "token": {"access_token": "''' + SECRET + '''"}}))
if args[:2] == ["auth", "status"]:
    assert "ANTHROPIC_AUTH_TOKEN" not in __import__("os").environ
    print(claude.read_text(), flush=True)
elif args[:2] == ["auth", "logout"]:
    claude.write_text(json.dumps({"loggedIn": False}))
elif args[:2] == ["auth", "login"] or args[:2] == ["acp", "--login"]:
    url = ("https://claude.ai/oauth/authorize?state=demo" if args[0] == "auth"
           else "https://accounts.google.com/o/oauth2/auth?state=demo&client_id=demo")
    print("Open this link: " + url, flush=True)
    print("Paste the authorization code:", flush=True)
    code = sys.stdin.readline().strip()
    if code != "one-time-code":
        print("raw error ''' + SECRET + '''", flush=True)
        raise SystemExit(1)
    write_account()
    print("Login successful", flush=True)
elif args[0] == "acp":
    for line in sys.stdin:
        request = json.loads(line)
        assert request["jsonrpc"] == "2.0"
        if request["method"] == "initialize":
            result = {"protocolVersion": 1, "agentCapabilities": {"auth": {"logout": {}}}}
        elif request["method"] == "logout":
            google.write_text(json.dumps({"token": {}}))
            result = {}
        else:
            raise SystemExit(2)
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
else:
    raise SystemExit(2)
''')
    script.chmod(0o700)
    monkeypatch.setattr(auth, "find_claude_binary", lambda: str(script))
    monkeypatch.setattr(auth, "find_agy_binary", lambda: str(script))
    return root


def test_claude_and_antigravity_metadata_are_projected_without_tokens(client, subscription_clis):
    accounts = _accounts(client)["accounts"]
    assert accounts[2]["email"] == "claude-old@example.com"
    assert accounts[2]["plan"] == "pro" and accounts[2]["authenticated"]
    assert accounts[3]["email"] == "google-old@example.com" and accounts[3]["authenticated"]
    assert SECRET not in json.dumps(accounts)


@pytest.mark.parametrize("provider,email", [
    ("claude-cli", "claude-new@example.com"), ("antigravity", "google-new@example.com"),
])
def test_native_cli_login_code_and_logout_round_trip(client, subscription_clis, provider, email):
    response = client.post(f"/api/auth/{provider}/login", headers=HEADERS)
    assert response.status_code == 202
    login_id = response.json()["login"]["id"]
    login = _eventually(client.app.state.auth.login_status, lambda s: s["input_required"])
    assert login["auth_url"].startswith("https://")
    response = client.post(f"/api/auth/logins/{login_id}/code", headers=HEADERS,
                           json={"code": "one-time-code"})
    assert response.status_code == 200 and "one-time-code" not in response.text
    state = _finished(client)
    assert state["login"]["status"] == "succeeded" and not state["login"]["input_required"]
    account = next(a for a in _accounts(client)["accounts"] if a["provider"] == provider)
    assert account["email"] == email and account["authenticated"]
    assert client.post(f"/api/auth/{provider}/logout", headers=HEADERS).status_code == 200
    assert not next(a for a in _accounts(client)["accounts"] if a["provider"] == provider)["authenticated"]


@pytest.mark.parametrize("provider", ["claude-cli", "antigravity"])
def test_native_cli_cancel_kills_code_prompt_without_replacing_account(client, subscription_clis, provider):
    client.post(f"/api/auth/{provider}/login", headers=HEADERS)
    login = _eventually(client.app.state.auth.login_status, lambda s: s["input_required"])
    process = client.app.state.auth._process
    assert client.post(f"/api/auth/logins/{login['id']}/cancel", headers=HEADERS).status_code == 200
    assert process.poll() is not None
    account = next(a for a in _accounts(client)["accounts"] if a["provider"] == provider)
    assert account["email"].endswith("-old@example.com")
    response = client.post(f"/api/auth/logins/{login['id']}/code", headers=HEADERS,
                           json={"code": "one-time-code"})
    assert response.status_code == 400 and "one-time-code" not in response.text


@pytest.mark.parametrize("code", ["first\nsecond", "first\rsecond", "", "x" * 4097, None, 123])
def test_invalid_authorization_codes_are_rejected_without_echoing_them(client, code):
    response = client.post("/api/auth/logins/unknown/code", headers=HEADERS, json={"code": code})
    assert response.status_code == 400
    assert response.json()["detail"] == "请粘贴有效的授权码或授权完成后的完整地址。"


@pytest.mark.parametrize("provider,url", [
    ("claude-cli", "https://accounts.google.com/o/oauth2/auth"),
    ("antigravity", "https://claude.ai/oauth/authorize"),
    ("claude-cli", "https://claude.ai/oauth/code?code=private"),
    ("antigravity", "https://accounts.google.com/o/oauth2/auth?access_token=private"),
])
def test_login_links_are_provider_specific_and_cannot_contain_credentials(provider, url):
    with pytest.raises(auth.AuthError):
        auth._authorization_url(url, provider)


def test_claude_subscription_uses_the_selected_local_account_instead_of_inherited_api_tokens(monkeypatch):
    from tradingagents.llm_clients.claude_cli_client import claude_subscription_environment

    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", SECRET)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", SECRET)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/test/claude-config")
    environment = claude_subscription_environment()
    assert not {"ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL"} & environment.keys()
    assert environment["CLAUDE_CONFIG_DIR"] == "/test/claude-config"


def test_antigravity_api_key_mode_is_not_labelled_as_a_subscription(client, subscription_clis, monkeypatch):
    settings = auth.GEMINI_DIRECTORY / "antigravity-cli" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"modelProvider": "gemini"}))
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    account = _accounts(client)["accounts"][3]
    assert account["authenticated"] and account["auth_mode"] == "apiKey" and account["email"] is None


def test_antigravity_logout_requires_the_advertised_capability(monkeypatch):
    rpc = auth.AntigravityRPC.__new__(auth.AntigravityRPC)
    rpc.initialized = {"agentCapabilities": {}}
    with pytest.raises(auth.AuthError, match="不支持"):
        rpc.logout()
