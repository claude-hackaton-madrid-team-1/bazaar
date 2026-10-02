"""Phoenix admin chores against a fake Phoenix (httpx.MockTransport, no network): the one-time
bootstrap of an auth-enabled Phoenix, the span summary, and the `bazaar obs` commands around them."""

import json

import httpx
import pytest
from typer.testing import CliRunner

from bazaar_agent import cli
from bazaar_agent import phoenix_admin as pa
from bazaar_agent import telemetry as tm

PASSWORD = "Initial-Admin-Pw-123"
KEY = "eyJhbGciOi.payload.signature"


class FakePhoenix:
    """Just enough of Phoenix 20.x: login cookie, forced reset, GraphQL patchViewer, system keys."""

    def __init__(self, needs_reset=True, password=PASSWORD, graphql_errors=False):
        self.needs_reset, self.password, self.graphql_errors = needs_reset, password, graphql_errors
        self.calls: list[str] = []
        self.sessions = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(f"{request.method} {request.url.path}")
        body = json.loads(request.content) if request.content else {}
        authed = "phoenix-access-token" in request.headers.get("cookie", "")
        if request.url.path == "/auth/login":
            if body.get("password") != self.password:
                return httpx.Response(401, text="Invalid email and/or password")
            self.sessions += 1
            return httpx.Response(204, headers={"set-cookie": f"phoenix-access-token=s{self.sessions}; Path=/"})
        if not authed:
            return httpx.Response(401, text="Unauthorized")
        if request.url.path == "/v1/user":
            return httpx.Response(
                200, json={"data": {"email": pa.ADMIN_EMAIL, "password_needs_reset": self.needs_reset}}
            )
        if request.url.path == "/graphql":
            if self.graphql_errors:
                return httpx.Response(200, json={"errors": [{"message": "nope"}]})
            assert body["variables"] == {"p": self.password}
            self.needs_reset = False
            return httpx.Response(200, json={"data": {"patchViewer": {"__typename": "UserMutationPayload"}}})
        if request.url.path == "/v1/system/api_keys":
            data = {"id": "U3lzdGVtQXBpS2V5OjE=", "name": body["data"]["name"], "key": KEY, "created_at": "now"}
            return httpx.Response(201, json={"data": data})
        return httpx.Response(404)


def client_for(fake: FakePhoenix) -> httpx.Client:
    return httpx.Client(base_url="https://phoenix.test", transport=httpx.MockTransport(fake.handler))


def test_bootstrap_clears_the_forced_reset_logs_in_again_and_mints_a_system_key():
    fake = FakePhoenix(needs_reset=True)
    created = pa.bootstrap(client_for(fake), PASSWORD, "railway-ingest", "spans")
    assert created == pa.SystemKey("U3lzdGVtQXBpS2V5OjE=", "railway-ingest", KEY)
    assert fake.calls == [
        "POST /auth/login",
        "GET /v1/user",
        "POST /graphql",
        "POST /auth/login",
        "POST /v1/system/api_keys",
    ]


def test_bootstrap_skips_the_reset_when_the_admin_has_none():
    fake = FakePhoenix(needs_reset=False)
    pa.bootstrap(client_for(fake), PASSWORD, "k", "d")
    assert "POST /graphql" not in fake.calls and fake.calls.count("POST /auth/login") == 1


def test_a_wrong_password_names_the_step_and_status_never_the_password():
    with pytest.raises(pa.PhoenixAdminError) as err:
        pa.bootstrap(client_for(FakePhoenix(password="other-password-9")), PASSWORD, "k", "d")
    assert str(err.value) == "login failed: HTTP 401" and PASSWORD not in str(err.value)


def test_a_graphql_error_on_the_reset_is_a_failure():
    with pytest.raises(pa.PhoenixAdminError, match="GraphQL error"):
        pa.bootstrap(client_for(FakePhoenix(graphql_errors=True)), PASSWORD, "k", "d")


def spans_client(status=200, spans=()):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/projects/bazaar/spans"
        assert request.url.params["sort"] == "start_time" and request.url.params["order"] == "desc"
        assert request.headers["authorization"] == f"Bearer {KEY}"
        return httpx.Response(status, json={"data": list(spans), "next_cursor": None})

    return httpx.Client(base_url="https://phoenix.test", headers={"authorization": f"Bearer {KEY}"},
                        transport=httpx.MockTransport(handler))  # fmt: skip


def test_span_summary_counts_the_newest_spans_by_name():
    spans = [
        {"name": "monitor tick 132", "start_time": "2026-10-02T20:33:47Z"},
        {"name": "monitor tick 131", "start_time": "2026-10-02T20:32:25Z"},
        {"name": "monitor tick 132", "start_time": "2026-10-02T20:33:48Z"},
        {"name": "duels tick 132", "start_time": None},
    ]
    summary = pa.span_summary(spans_client(spans=spans), "bazaar", limit=5000)
    assert summary.total == 4 and summary.newest_start == "2026-10-02T20:33:48Z"
    assert summary.by_name == {"monitor tick 132": 2, "monitor tick 131": 1, "duels tick 132": 1}


def test_a_project_without_spans_yet_is_an_empty_summary_and_auth_errors_raise():
    assert pa.span_summary(spans_client(status=404), "bazaar") == pa.SpanSummary(0, {}, None)
    with pytest.raises(pa.PhoenixAdminError, match="list spans failed: HTTP 401"):
        pa.span_summary(spans_client(status=401), "bazaar")


# ---------------------------------------------------------------- the `bazaar obs` commands


@pytest.fixture
def runner():
    return CliRunner()


def config(api_key=None):
    """Tracing OFF: the app callback must not install a real exporter (unit tests make no network calls)."""
    return tm.TracingConfig(enabled=False, endpoint="https://phoenix.test/v1/traces", project="bazaar", api_key=api_key)


def test_obs_up_treats_a_phoenix_that_already_answers_as_up(runner, monkeypatch):
    monkeypatch.setattr(tm, "tracing_config", lambda: config())
    monkeypatch.setattr(cli, "_phoenix_health", lambda url: (True, "up (HTTP 200)"))
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: pytest.fail("docker must not be started"))
    result = runner.invoke(cli.app, ["obs", "up"])
    assert result.exit_code == 0 and "already up at https://phoenix.test" in result.output


def test_obs_up_without_phoenix_or_docker_fails_with_a_clear_message(runner, monkeypatch):
    def no_docker(*args, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(tm, "tracing_config", lambda: config())
    monkeypatch.setattr(cli, "_phoenix_health", lambda url: (False, "unreachable"))
    monkeypatch.setattr(cli.subprocess, "run", no_docker)
    result = runner.invoke(cli.app, ["obs", "up"])
    assert result.exit_code == 1 and "docker is not installed" in result.output


def test_obs_bootstrap_writes_only_the_key_to_stdout(runner, monkeypatch):
    monkeypatch.setenv("PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD", PASSWORD)
    seen = {}

    def fake_bootstrap(client, password, key_name, description):
        seen.update(url=str(client.base_url), password=password, key_name=key_name)
        return pa.SystemKey("id1", key_name, KEY)

    monkeypatch.setattr(pa, "bootstrap", fake_bootstrap)
    result = runner.invoke(cli.app, ["obs", "bootstrap", "--url", "https://phoenix.test/"])
    assert result.exit_code == 0, result.output
    assert result.stdout == KEY
    assert seen == {"url": "https://phoenix.test", "password": PASSWORD, "key_name": "railway-ingest"}


def test_obs_bootstrap_needs_the_admin_password_in_the_environment(runner, monkeypatch):
    monkeypatch.delenv("PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD", raising=False)
    result = runner.invoke(cli.app, ["obs", "bootstrap", "--url", "https://phoenix.test"])
    assert result.exit_code == 1 and "PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD is not set" in result.output


def test_obs_bootstrap_failure_exits_1_without_a_key(runner, monkeypatch):
    monkeypatch.setenv("PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD", PASSWORD)

    def refused(*args, **kwargs):
        raise pa.PhoenixAdminError("login failed: HTTP 401")

    monkeypatch.setattr(pa, "bootstrap", refused)
    result = runner.invoke(cli.app, ["obs", "bootstrap", "--url", "https://phoenix.test"])
    assert result.exit_code == 1 and KEY not in result.stdout


def test_obs_spans_prints_counts_with_the_configured_key(runner, monkeypatch):
    monkeypatch.setattr(tm, "tracing_config", lambda: config(api_key=KEY))

    def fake_summary(client, project, limit):
        assert client.headers["authorization"] == f"Bearer {KEY}" and project == "bazaar"
        return pa.SpanSummary(3, {"monitor tick 132": 2, "duels tick 132": 1}, "2026-10-02T20:33:48Z")

    monkeypatch.setattr(pa, "span_summary", fake_summary)
    result = runner.invoke(cli.app, ["obs", "spans"])
    text = " ".join(result.output.split())
    assert result.exit_code == 0 and "3 spans, newest start 2026-10-02T20:33:48Z" in text
    assert "2  monitor tick 132" in result.output and KEY not in result.output
