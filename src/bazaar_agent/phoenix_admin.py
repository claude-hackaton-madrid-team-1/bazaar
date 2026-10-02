"""Admin chores for a Phoenix with auth on (our Railway Phoenix), over its REST and GraphQL APIs.

`bootstrap` runs once after the first deploy. Phoenix creates `admin@localhost` with the password in
PHOENIX_DEFAULT_ADMIN_INITIAL_PASSWORD and flags it for a forced reset; bootstrap "changes" it to the
same value, so the Railway variable stays the real password, then mints a system API key for span
ingestion. `span_summary` proves spans arrive: span counts per name in one project.

Secrets pass through here but are never logged: an error names the step and the HTTP status only.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import httpx

ADMIN_EMAIL = "admin@localhost"  # Phoenix's default admin (phoenix.db.facilitator)
_CLEAR_RESET = "mutation($p: String!) { patchViewer(input: {currentPassword: $p, newPassword: $p}) { __typename } }"
MAX_SPAN_PAGE = 1000  # the REST API's cap per page


class PhoenixAdminError(RuntimeError):
    """A step failed. The message names the step and the status, never a secret."""


@dataclass(frozen=True)
class SystemKey:
    id: str
    name: str
    key: str  # shown once by Phoenix: store it as a Railway variable, never print it


@dataclass(frozen=True)
class SpanSummary:
    total: int
    by_name: dict[str, int]
    newest_start: str | None


def _check(step: str, reply: httpx.Response) -> httpx.Response:
    if not reply.is_success:
        raise PhoenixAdminError(f"{step} failed: HTTP {reply.status_code}")
    return reply


def login(client: httpx.Client, password: str) -> None:
    """Session cookies on `client`. A password change ends every session, hence a second login."""
    _check("login", client.post("/auth/login", json={"email": ADMIN_EMAIL, "password": password}))


def needs_reset(client: httpx.Client) -> bool:
    data = _check("read the admin user", client.get("/v1/user")).json().get("data") or {}
    return bool(data.get("password_needs_reset"))


def clear_forced_reset(client: httpx.Client, password: str) -> None:
    reply = _check(
        "clear the forced reset", client.post("/graphql", json={"query": _CLEAR_RESET, "variables": {"p": password}})
    )
    if reply.json().get("errors"):
        raise PhoenixAdminError("clear the forced reset failed: GraphQL error")


def create_system_key(client: httpx.Client, name: str, description: str) -> SystemKey:
    body = {"data": {"name": name, "description": description}}
    data = _check("create the system API key", client.post("/v1/system/api_keys", json=body)).json()["data"]
    return SystemKey(id=str(data["id"]), name=str(data["name"]), key=str(data["key"]))


def bootstrap(client: httpx.Client, password: str, key_name: str, description: str) -> SystemKey:
    """Log in as the default admin, clear the forced first-login reset, mint a system API key."""
    login(client, password)
    if needs_reset(client):
        clear_forced_reset(client, password)
        client.cookies.clear()
        login(client, password)
    return create_system_key(client, key_name, description)


def span_summary(client: httpx.Client, project: str, limit: int = 500) -> SpanSummary:
    """The latest `limit` spans of `project` (newest first), counted by span name."""
    reply = client.get(
        f"/v1/projects/{project}/spans",
        params={"limit": min(limit, MAX_SPAN_PAGE), "sort": "start_time", "order": "desc"},
    )
    if reply.status_code == httpx.codes.NOT_FOUND:
        return SpanSummary(0, {}, None)
    spans = _check("list spans", reply).json().get("data") or []
    names = Counter(str(s.get("name")) for s in spans)
    starts = [str(s["start_time"]) for s in spans if s.get("start_time")]
    return SpanSummary(len(spans), dict(names.most_common()), max(starts) if starts else None)
