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
