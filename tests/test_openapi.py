import json
import unittest
from pathlib import Path

API = Path(__file__).resolve().parent.parent / "docs" / "api"
METHODS = {"get", "post", "put", "patch", "delete"}


def load(name):
    return json.loads((API / name).read_text())


def operations(spec):
    return {(m, p) for p, ops in spec["paths"].items() for m in ops if m in METHODS}


def refs(node):
    if isinstance(node, dict):
        if "$ref" in node:
            yield node["$ref"]
        for v in node.values():
            yield from refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from refs(v)


class OpenApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = load("openapi.json")
        cls.server = load("openapi.server.json")

    def test_is_openapi_31(self):
        self.assertEqual(self.spec["openapi"], "3.1.0")
        self.assertEqual(self.spec["servers"][0]["url"], "https://bazaar.causaprima.ai")

    def test_covers_every_server_operation(self):
        self.assertEqual(operations(self.server) - operations(self.spec), set())

    def test_every_game_operation_documents_its_response(self):
        for m, p in operations(self.spec):
            if p.startswith("/api/admin") or p == "/{path}":
                continue
            ok = self.spec["paths"][p][m]["responses"]["200"]["content"]
            schema = next(iter(ok.values()))["schema"]
            self.assertTrue(schema, f"{m.upper()} {p} has an empty response schema")

    def test_team_operations_declare_their_key(self):
        for m, p in operations(self.spec):
            if p.startswith("/api/me") or p.startswith("/api/duels") or p.startswith("/api/broker"):
                schemes = {k for req in self.spec["paths"][p][m].get("security", []) for k in req}
                self.assertTrue(schemes & {"TeamKey", "BrokerKey"}, f"{m.upper()} {p} declares no key")

    def test_credentialed_operations_document_their_refusal(self):
        for m, p in operations(self.spec):
            op = self.spec["paths"][p][m]
            if not op.get("security"):
                continue
            refusal = op["responses"].get("401", {}).get("content", {}).get("application/json", {})
            self.assertEqual(refusal.get("schema"), {"$ref": "#/components/schemas/Error"}, f"{m.upper()} {p}")

    def test_refusal_codes_seen_live_are_listed(self):
        codes = self.spec["components"]["schemas"]["ErrorCode"]["enum"]
        for code in ("bad_key", "bad_token", "too_many_failures", "not_found"):
            self.assertIn(code, codes)

    def test_every_ref_resolves(self):
        schemas = self.spec["components"]["schemas"]
        for r in set(refs(self.spec)):
            self.assertTrue(r.startswith("#/components/schemas/"), r)
            self.assertIn(r.rsplit("/", 1)[1], schemas, r)

    def test_response_schemas_say_whether_they_were_observed(self):
        for name, schema in self.spec["components"]["schemas"].items():
            if name in self.server["components"]["schemas"]:
                continue
            self.assertIn("x-verified", schema, name)

    def test_no_secrets(self):
        text = (API / "openapi.json").read_text()
        self.assertNotRegex(text, r"\btk-(?!xxxx)[a-z0-9]{4}-[a-z0-9]{4}\b")
        self.assertNotRegex(text, r"\bbk_(?!\.\.\.)[A-Za-z0-9]{6,}")


if __name__ == "__main__":
    unittest.main()
