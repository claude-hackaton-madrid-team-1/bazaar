import json
import os
import re
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

SPEC = json.loads((Path(__file__).resolve().parent.parent / "docs" / "api" / "openapi.json").read_text())
BASE = os.environ.get("BAZAAR_URL", "https://bazaar.causaprima.ai").rstrip("/")
LIVE = os.environ.get("BAZAAR_LIVE") == "1"
RECORD = os.environ.get("BAZAAR_RECORD") == "1"
KEY = os.environ.get("BAZAAR_KEY", "") if LIVE else "replay"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "api"
PRIVATE_FIELDS = {"affinity", "your_value", "collection_value"}
METHODS = {"get", "post", "put", "patch", "delete"}
MIN_INTERVAL_S = 0.25

PUBLIC_READS = {
    "/api/health": ("/api/health", {}),
    "/api/clock": ("/api/clock", {}),
    "/api/catalog": ("/api/catalog", {}),
    "/api/leaderboard": ("/api/leaderboard", {}),
    "/api/feed": ("/api/feed", {"limit": 20}),
    "/api/schedule": ("/api/schedule", {}),
    "/api/dealers": ("/api/dealers", {}),
    "/api/dealers/{pid}": ("/api/dealers/{pid}", {}),
    "/api/levels": ("/api/levels", {}),
    "/api/venues": ("/api/venues", {}),
    "/api/venues/{vid}/offers": ("/api/venues/{vid}/offers", {}),
    "/{path}": ("/", {}),
}

TEAM_READS = {
    "/api/me": ("/api/me", {}),
    "/api/me/value": ("/api/me/value", {"card": "{card}"}),
    "/api/me/threads": ("/api/me/threads", {}),
    "/api/me/offers": ("/api/me/offers", {}),
    "/api/cards/{asset_id}": ("/api/cards/{asset_id}", {}),
    "/api/threads/{tid}": ("/api/threads/{tid}", {}),
    "/api/duels": ("/api/duels", {}),
}

REFUSED_CODES = {"TeamKey": "bad_key", "BrokerKey": "bad_key", "AdminToken": "bad_token"}
REFUSAL_PROBES = {
    "TeamKey": ("get", "/api/me"),
    "BrokerKey": ("get", "/api/broker/book"),
    "AdminToken": ("get", "/api/admin/overview"),
}

_last_call = [0.0]


def operations():
    return {(m, p) for p, ops in SPEC["paths"].items() for m in ops if m in METHODS}


def schemes(method, path):
    return {k for req in SPEC["paths"][path][method].get("security", []) for k in req}


def fixture_path(method, path, query, keyed):
    slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{method} {path} {urlencode(query or {})}").strip("_")
    return FIXTURES / f"{slug}.{'team' if keyed else 'anon'}.json"


def mask(node):
    if isinstance(node, dict):
        return {k: mask(v) for k, v in node.items()}
    if isinstance(node, float):
        return 1.0
    if isinstance(node, int) and not isinstance(node, bool):
        return 1
    return node


def redact(node):
    if isinstance(node, dict):
        return {k: mask(v) if k in PRIVATE_FIELDS else redact(v) for k, v in node.items()}
    if isinstance(node, list):
        return [redact(v) for v in node]
    return node


def request(method, path, query=None, key=None):
    fixture = fixture_path(method, path, query, bool(key))
    if not LIVE:
        if not fixture.exists():
            raise AssertionError(f"no saved response {fixture.name}; record it with BAZAAR_LIVE=1 BAZAAR_RECORD=1")
        saved = json.loads(fixture.read_text())
        body = saved["body"]
        raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        return saved["status"], saved["content_type"], raw
    status, ctype, raw = call(method, path, query, key)
    if RECORD and status != 429:
        body = redact(json.loads(raw)) if ctype == "application/json" else raw.decode()
        fixture.parent.mkdir(parents=True, exist_ok=True)
        saved = {"status": status, "content_type": ctype, "body": body}
        fixture.write_text(json.dumps(saved, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    return status, ctype, raw


def call(method, path, query=None, key=None):
    wait = _last_call[0] + MIN_INTERVAL_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()
    url = BASE + path + ("?" + urlencode(query) if query else "")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key:
        headers["X-Team-Key"] = key
    data = b"{}" if method in ("post", "put", "patch") else None
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.headers.get_content_type(), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get_content_type(), e.read()


def schema_of(method, path, status, ctype):
    response = SPEC["paths"][path][method]["responses"].get(str(status))
    if response is None:
        return None, None
    content = response.get("content", {})
    media = content.get(ctype) or content.get("application/json") or next(iter(content.values()), {})
    return response, media.get("schema")


def resolve(schema):
    while isinstance(schema, dict) and "$ref" in schema:
        schema = SPEC["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]]
    return schema


class ApiResponsesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from jsonschema import Draft202012Validator

        cls.validator_for = staticmethod(
            lambda schema: Draft202012Validator({**schema, "components": SPEC["components"]})
        )
        cls.ids = {"pid": "abuela", "vid": "rastro"}
        status, _, body = request("get", "/api/dealers")
        if status == 200 and json.loads(body).get("personas"):
            cls.ids["pid"] = json.loads(body)["personas"][0]["id"]
        status, _, body = request("get", "/api/venues")
        if status == 200 and json.loads(body).get("venues"):
            cls.ids["vid"] = json.loads(body)["venues"][0]["venue"]
        if KEY:
            me = json.loads(request("get", "/api/me", key=KEY)[2])
            assets = me.get("assets", [])
            cls.ids["asset_id"] = assets[0]["id"] if assets else None
            cards = [a["ref"] for a in assets if a.get("kind") == "card"]
            cls.ids["card"] = cards[0] if cards else None
            threads = json.loads(request("get", "/api/me/threads", key=KEY)[2]).get("threads", [])
            cls.ids["tid"] = threads[0]["id"] if threads else None

    def fill(self, template):
        missing = [k for k, v in self.ids.items() if "{" + k + "}" in template and v is None]
        if missing:
            self.skipTest(f"no live {', '.join(missing)} to probe {template}")
        return template.format(**{k: v for k, v in self.ids.items() if v is not None})

    def assert_matches_spec(self, method, path, status, ctype, body):
        response, schema = schema_of(method, path, status, ctype)
        self.assertIsNotNone(response, f"{method.upper()} {path} returned {status}, undocumented in the spec")
        if ctype != "application/json":
            return None
        payload = json.loads(body)
        self.assertTrue(schema, f"{method.upper()} {path} {status} has no schema")
        errors = sorted(self.validator_for(schema).iter_errors(payload), key=lambda e: list(e.absolute_path))
        self.assertEqual(
            [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: fails {e.validator}" for e in errors],
            [],
            f"{method.upper()} {path} {status} does not match its schema",
        )
        declared = resolve(schema).get("properties")
        if declared and isinstance(payload, dict):
            self.assertEqual(
                sorted(set(payload) - set(declared)), [], f"{method.upper()} {path} returns undocumented fields"
            )
        return payload

    def test_saved_responses_hide_private_values_and_keys(self):
        saved = sorted(FIXTURES.glob("*.json"))
        self.assertTrue(saved)
        for fixture in saved:
            with self.subTest(fixture=fixture.name):
                text = fixture.read_text()
                self.assertNotRegex(text, r"\btk-(?!xxxx)[a-z0-9]{4}-[a-z0-9]{4}\b")
                self.assertNotRegex(text, r"\bbk_(?!\.\.\.)[A-Za-z0-9]{6,}")
                body = json.loads(text)["body"]
                self.assertEqual(redact(body), body)

    def test_every_operation_is_read_live_or_behind_a_key(self):
        probed = {("get", p) for p in {**PUBLIC_READS, **TEAM_READS}}
        refused = {op for op in operations() - probed if schemes(*op)}
        self.assertEqual(operations() - probed - refused, set())

    def test_public_reads_match_the_spec(self):
        for path, (template, query) in PUBLIC_READS.items():
            with self.subTest(path=path):
                status, ctype, body = request("get", self.fill(template), query)
                self.assertEqual(status, 200, body[:200])
                self.assert_matches_spec("get", path, status, ctype, body)

    def test_team_reads_match_the_spec(self):
        if not KEY:
            self.skipTest("set BAZAAR_KEY to call the team routes live")
        for path, (template, query) in TEAM_READS.items():
            with self.subTest(path=path):
                concrete = self.fill(template)
                status, ctype, body = request("get", concrete, {k: self.fill(v) for k, v in query.items()}, KEY)
                self.assertEqual(status, 200, body[:200])
                self.assert_matches_spec("get", path, status, ctype, body)

    def test_each_key_scheme_refuses_a_missing_key_as_documented(self):
        for scheme, (method, path) in REFUSAL_PROBES.items():
            with self.subTest(scheme=scheme):
                self.assertIn(scheme, schemes(method, path))
                status, ctype, body = request(method, path)
                if status == 429 and b"too_many_failures" in body:
                    self.skipTest("this address is locked out for wrong keys; try again later")
                self.assertEqual(status, 401, body[:200])
                payload = self.assert_matches_spec(method, path, status, ctype, body)
                self.assertEqual(payload["error"], REFUSED_CODES[scheme])
                self.assertIn(payload["error"], SPEC["components"]["schemas"]["ErrorCode"]["enum"])

if __name__ == "__main__":
    unittest.main()
