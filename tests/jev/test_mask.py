"""Port of vendor/jev-sdk/test/jev-mask.test.ts, plus the JSON parity the digests depend on.

Every fixture is synthetic. No value here is a real credential, Party or address. The left column
is what an operator can paste into a state; the right column is all that may leave the machine.
"""

from __future__ import annotations

import re

import pytest

from bazaar_agent.jev.mask import (
    js_json_dumps,
    js_number,
    mask_questions,
    mask_state,
    mask_text,
    masked_digest,
    utf16_length,
)


def pem_header(edge: str) -> str:
    # Composed so a secret scanner reading this file never sees a whole PEM header.
    return "-----" + edge + " RSA PRIVATE KEY-----"


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        (
            "party " + "sample-operator-fixture" + "::" + "N4mespace.fixture is ready",
            "party [redacted] is ready",
        ),
        ("Authorization: Bearer fixture-token-value-0001", "Authorization: [redacted]"),
        (
            "cookie eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmaXh0dXJlIn0.c2lnbmF0dXJlLWZpeHR1cmUwMQ set",
            "cookie [redacted] set",
        ),
        (
            "DATABASE_URL=" + "postgres:" + "//sample:fixture-password-0001@db.fixture.example:5432/ledger",
            "DATABASE_URL=[redacted]",
        ),
        ("reading " + "redis:" + "//cache.fixture.example:6379/0 now", "reading [redacted] now"),
        (pem_header("BEGIN"), "[redacted]"),
        ("ping operator.fixture@example.com about it", "ping [redacted] about it"),
        ("key sample_ak_FIXTUREFIXTUREFIXTURE01 used", "key [redacted] used"),
        ('config {"api_key": "fixture-value-0001"}', 'config {"[redacted]"}'),
        ("calling localhost:8080 and api.internal", "calling [redacted] and [redacted]"),
        ("account acct_fixture01 paid", "account [redacted] paid"),
    ],
    ids=[
        "party id",
        "bearer token",
        "jwt",
        "connection string with credentials",
        "connection string without credentials",
        "private key header",
        "e-mail address",
        "api key shape",
        "keyed secret",
        "internal hostnames",
        "account id",
    ],
)
def test_masks_a_value_that_may_not_leave(raw: str, masked: str) -> None:
    assert mask_text(raw) == masked


def test_redacts_a_complete_multiline_private_key() -> None:
    body = "".join(["synthetic", "base64", "body"])
    text = f"before\n{pem_header('BEGIN')}\n{body}\n{pem_header('END')}\nafter"
    assert mask_text(text) == "before\n[redacted]\nafter"


def test_fails_closed_on_an_unterminated_private_key_block() -> None:
    body = "".join(["synthetic", "base64", "body"])
    text = f"before\n{pem_header('BEGIN')}\n{body}\ntrailing private text\n"
    assert mask_text(text) == "before\n[redacted]"


def test_keeps_the_line_structure_a_judge_needs() -> None:
    assert (
        mask_text("engine refused\n  reason: risk_limit_exceeded\n") == "engine refused\n reason: risk_limit_exceeded"
    )


def test_control_characters_become_spaces_and_nfkc_applies() -> None:
    assert mask_text("tick\x00\x07 17  ok ") == "tick 17 ok"
    assert mask_text("ﬁnal ①") == "final 1"


@pytest.mark.parametrize(
    "text",
    [
        "order 12 rejected: risk_limit_exceeded after 3 retries",
        "Final price: 12 primas for LAV-03.",
        "Abuela: ¿me das 15 por el LAV-03? Es el último.",
    ],
)
def test_leaves_ordinary_negotiation_text_alone(text: str) -> None:
    assert mask_text(text) == text


def test_masks_every_string_inside_a_structured_state_and_keeps_numbers() -> None:
    assert mask_state(
        {
            "service": "worker",
            "attempts": 3,
            "ready": False,
            "nothing": None,
            "lines": ["Bearer fixture-token-value-0001", "ready"],
            "nested": {"party": "sample-operator-fixture" + "::" + "N4mespace.fixture"},
        }
    ) == {
        "service": "worker",
        "attempts": 3,
        "ready": False,
        "nothing": None,
        "lines": ["[redacted]", "ready"],
        "nested": {"party": "[redacted]"},
    }


@pytest.mark.parametrize("key", ["apiKey", "api_key", "client_secret", "refreshToken", "Authorization", "PASSWORD"])
def test_a_credential_named_field_is_masked_whatever_its_value(key: str) -> None:
    assert mask_state({key: {"deep": 1}}) == {key: "[redacted]"}


def test_masks_the_instructions_and_criteria_and_drops_stakes() -> None:
    assert mask_questions(
        {
            "needs_owner": {
                "type": "noul",
                "stakes": "critical",
                "instructions": "Does Bearer fixture-token-value-0001 need the owner?",
                "criteria": {"true": "the owner must act", "false": "operator.fixture@example.com can act"},
            }
        }
    ) == {
        "needs_owner": {
            "type": "noul",
            "instructions": "Does [redacted] need the owner?",
            "criteria": {"true": "the owner must act", "false": "[redacted] can act"},
        }
    }


def test_the_digest_is_stable_hex_and_never_carries_the_text() -> None:
    digest = masked_digest("engine refused")
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert digest == masked_digest("engine refused")
    assert digest != masked_digest("engine accepted")


# --- what JSON.stringify writes, so the digests match upstream -----------------------------------


@pytest.mark.parametrize(
    ("value", "written"),
    [
        (0.0, "0"),
        (-0.0, "0"),
        (2.0, "2"),
        (1.25, "1.25"),
        (1e-7, "1e-7"),
        (0.000001, "0.000001"),
        (1e21, "1e+21"),
    ],
)
def test_numbers_are_written_the_way_javascript_writes_them(value: float, written: str) -> None:
    assert js_number(value) == written


def test_objects_use_javascript_key_order_and_compact_separators() -> None:
    value = {"b": 1, "10": "x", "a": [True, None, 0.5], "2": float("nan"), "é": "ü\n"}
    assert js_json_dumps(value) == '{"2":null,"10":"x","b":1,"a":[true,null,0.5],"é":"ü\\n"}'


def test_lengths_count_utf16_code_units_like_javascript() -> None:
    assert utf16_length("ab") == 2
    assert utf16_length("\U0001f600") == 2
