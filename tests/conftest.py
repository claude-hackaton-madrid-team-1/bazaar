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
