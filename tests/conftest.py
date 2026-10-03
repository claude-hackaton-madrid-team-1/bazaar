import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from bazaar_agent import telemetry as tm

SECRET = "tk-team1-very-secret-0042"


@pytest.fixture
def spans():
    """Tracing on, into memory: every finished span is in the returned exporter. No network."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tm.install(provider.get_tracer("test"), secrets=(SECRET,))
    yield exporter
    tm.uninstall()


@pytest.fixture(autouse=True)
def no_real_tracing(monkeypatch):
    """A teammate's BAZAAR_TRACING=1 in .env must never make the suite export to a real Phoenix."""
    monkeypatch.setenv("BAZAAR_TRACING", "0")


@pytest.fixture(autouse=True)
def no_tick_stagger(monkeypatch):
    """A BAZAAR_TICK_OFFSET_S in a teammate's .env must not shift every tick loop the suite runs."""
    monkeypatch.setenv("BAZAAR_TICK_OFFSET_S", "")


@pytest.fixture(autouse=True)
def trading_enabled_in_guardrails(monkeypatch, tmp_path_factory):
    """`guardrails.kill_switch()` re-reads GUARDRAILS.md on every call: the suite reads a copy with
    `trading_enabled = true`, so a kill switch committed in the real file never breaks unrelated tests."""
    from bazaar_agent import guardrails as gr

    copy = tmp_path_factory.getbasetemp() / "GUARDRAILS.trading.md"
    if not copy.is_file():
        text = gr.GUARDRAILS_FILE.read_text(encoding="utf-8")
        copy.write_text(text.replace("- `trading_enabled` = false", "- `trading_enabled` = true"), encoding="utf-8")
    monkeypatch.setattr(gr, "GUARDRAILS_FILE", copy)


@pytest.fixture(autouse=True)
def no_shared_holdings_db():
    """Unit tests never reach a real database through the per-process holdings connection: a teammate's
    DATABASE_URL may be the shared team DB. Tests that need Postgres build their own `SharedDb`."""
    from bazaar_agent import holdings

    saved = dict(holdings._PROCESS)
    holdings._PROCESS.clear()
    holdings._PROCESS.update({"name": "pytest", "db": holdings.SharedDb(None), "writer_db": holdings.SharedDb(None)})
    yield
    holdings._PROCESS.clear()
    holdings._PROCESS.update(saved)


@pytest.fixture(autouse=True)
def no_real_models(monkeypatch):
    """No test loads the real fastembed models: that would download ~150 MB from Hugging Face on a background
    thread (a live network call) that can still be running native code when the interpreter exits."""
    monkeypatch.setenv("BAZAAR_MODELS", "off")


@pytest.fixture(autouse=True)
def official_value_cap_off(request, monkeypatch):
    """The official value cap (`guardrails._official_value_violations`, GET /api/me/value) is off in the tests that
    predate it: their fake clients have no `value()` and their contexts no value book, so every card buy would be
    refused (fail closed). A test marked `official_values` runs the real cap (tests/test_official_values.py and the
    per-path tests)."""
    if request.node.get_closest_marker("official_values") is not None:
        return
    from bazaar_agent import guardrails as gr

    monkeypatch.setattr(gr, "_official_value_violations", lambda action, ctx, rules: [])


@pytest.fixture(autouse=True)
def no_shared_breakers():
    """`guardrails.check()` reads the circuit breakers of this process's database once per tick: the suite reads an
    empty board instead (no Postgres connect per test). Tests of the breakers build their own `BreakerBoard`."""
    from bazaar_agent import breakers

    old = breakers.install(breakers.BreakerBoard(None))
    yield
    if old is None:
        breakers._BOARD.pop("board", None)
    else:
        breakers.install(old)


@pytest.fixture(autouse=True)
def human_approval_off(request, monkeypatch):
    """`human_approval_above` (GUARDRAILS.md) refuses every big card trade without an approval in Postgres, and a
    missing database fails closed: the tests that predate it trade at their own prices. A test marked
    `human_approval` runs the real rule (tests/test_approvals.py)."""
    if request.node.get_closest_marker("human_approval") is not None:
        return
    from bazaar_agent import guardrails as gr

    monkeypatch.setattr(gr, "_approval_violations", lambda action, ctx, rules: [])
