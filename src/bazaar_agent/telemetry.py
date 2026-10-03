"""OpenTelemetry tracing of our trading runtime, exported over OTLP/HTTP to Arize Phoenix.

Tracing is OFF unless `BAZAAR_TRACING=1` (environment or `.env`). Off, `init_tracing` installs
nothing, every helper here is a no-op and `negotiate()` runs exactly as before. On, spans leave
through a BatchSpanProcessor on a background thread, so a slow or dead Phoenix never delays a tick:
an export failure costs one warning per outage, and every telemetry hook swallows its own errors.

Nothing secret reaches a span. Our own secret values (any `*_KEY`, `*_TOKEN`, `*_SECRET` variable)
are cut out by value, team-key shapes (`tk-…`) are cut out by pattern, and every string then passes
through `jev.mask.mask_text`, the masking Jev requests already get.

Attribute names follow OpenInference (`openinference.span.kind`, `input.value`, `output.value`,
`tag.tags`, …) so Phoenix renders them. The span model lives in `bazaar_agent.traces`.
"""

from __future__ import annotations

import atexit
import io
import json
import logging
import os
import re
import sys
import traceback
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from functools import wraps
from typing import TYPE_CHECKING

from openinference.semconv.resource import ResourceAttributes
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Span, Status, StatusCode, Tracer
from rich.console import Console, ConsoleRenderable, RenderHook

from bazaar_agent.config import env_file_path, read_env_file
from bazaar_agent.jev.mask import JEV_REDACTION, mask_text
from bazaar_agent.pgconn import redact as redact_db_passwords

if TYPE_CHECKING:
    from collections.abc import Sequence

    from opentelemetry.sdk.trace import ReadableSpan

    from bazaar_agent.jev import JudgeResult

TRACING_FLAG = "BAZAAR_TRACING"
DEFAULT_PHOENIX_URL = "http://127.0.0.1:6006"
OTLP_TRACES_PATH = "/v1/traces"
DEFAULT_PROJECT = "bazaar"
EXPORT_TIMEOUT_S = 3.0  # per export, retries included: bounds the flush at exit when Phoenix is down
EXPORT_DELAY_MS = 2_000  # spans reach Phoenix within ~2 s, so a negotiation can be watched live
EXPORT_TIMEOUT_MS = 5_000  # a whole batch export, above the exporter's own timeout as OTel advises
MAX_QUEUE = 2_048  # bounded: when full, new spans are dropped; the trading thread never blocks
MAX_BATCH = 512
MIN_SECRET_LENGTH = 8  # shorter values are too common to cut out of free text by value

KIND = SpanAttributes.OPENINFERENCE_SPAN_KIND
AGENT = OpenInferenceSpanKindValues.AGENT.value
CHAIN = OpenInferenceSpanKindValues.CHAIN.value
GUARDRAIL = OpenInferenceSpanKindValues.GUARDRAIL.value
INPUT = SpanAttributes.INPUT_VALUE
INPUT_MIME = SpanAttributes.INPUT_MIME_TYPE
OUTPUT = SpanAttributes.OUTPUT_VALUE
TAGS = SpanAttributes.TAG_TAGS
JSON_MIME = "application/json"

_TRUE = frozenset({"1", "true", "yes", "on"})
_SECRET_NAME = re.compile(r"(?:KEY|TOKEN|SECRET|PASSWORD)\Z", re.IGNORECASE)
_TEAM_KEY = re.compile(r"\b(?:tk-|bk_|simbk-)[A-Za-z0-9_-]{6,}")  # team keys and broker keys (real and sim)
_LOG = logging.getLogger(__name__)

type SpanValue = str | bool | int | float | list[str] | list[int]


@dataclass(frozen=True)
class TracingConfig:
    enabled: bool
    endpoint: str  # the full OTLP/HTTP traces URL
    project: str
    api_key: str | None = field(default=None, repr=False)
    secrets: tuple[str, ...] = field(default=(), repr=False)
    database_url: str | None = field(default=None, repr=False)

    @property
    def ui_url(self) -> str:
        return self.endpoint.removesuffix(OTLP_TRACES_PATH)


def _traces_url(base: str) -> str:
    base = base.rstrip("/")
    return base if base.endswith(OTLP_TRACES_PATH) else base + OTLP_TRACES_PATH


def tracing_config(env: Mapping[str, str] | None = None) -> TracingConfig:
    """Settings from the environment, then `.env`. Endpoint precedence follows OTel, then Phoenix:
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT (used as is) > PHOENIX_COLLECTOR_ENDPOINT >
    OTEL_EXPORTER_OTLP_ENDPOINT (base URLs, `/v1/traces` appended) > local Phoenix."""
    values = {**read_env_file(env_file_path()), **os.environ} if env is None else dict(env)

    def pick(*names: str) -> str | None:
        return next((values[n].strip() for n in names if (values.get(n) or "").strip()), None)

    base = pick("PHOENIX_COLLECTOR_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT") or DEFAULT_PHOENIX_URL
    secrets = {v.strip() for k, v in values.items() if _SECRET_NAME.search(k) and len(v.strip()) >= MIN_SECRET_LENGTH}
    return TracingConfig(
        enabled=(pick(TRACING_FLAG) or "").lower() in _TRUE,
        endpoint=pick("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or _traces_url(base),
        project=pick("PHOENIX_PROJECT", "PHOENIX_PROJECT_NAME") or DEFAULT_PROJECT,
        api_key=pick("PHOENIX_API_KEY"),
        secrets=tuple(sorted(secrets, key=len, reverse=True)),
        database_url=pick("DATABASE_URL"),
    )


class _Runtime:
    """The process-wide tracer. `tracer is None` means tracing is off (the default)."""

    def __init__(self) -> None:
        self.tracer: Tracer | None = None
        self.provider: TracerProvider | None = None
        self.secrets: tuple[str, ...] = ()
        self.database_url: str | None = None
        self.warned: set[str] = set()


_RT = _Runtime()


def _warn_once(key: str, message: str, *args: object) -> None:
    if key not in _RT.warned:
        _RT.warned.add(key)
        _LOG.warning(message, *args)


def never_raise[**P, R](fn: Callable[P, R]) -> Callable[P, R | None]:
    """Telemetry must never break trading: an error inside a hook is one warning, then silence."""

    @wraps(fn)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> R | None:
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - deliberately broad: this is the telemetry firewall
            _warn_once(f"hook:{fn.__qualname__}", "tracing: %s failed (%s); trading continues", fn.__qualname__, e)
            return None

    return guarded


class QuietExporter(SpanExporter):
    """Wraps the OTLP exporter: a failed export is one warning per outage, never an exception."""

    def __init__(self, inner: SpanExporter, endpoint: str) -> None:
        self._inner, self._endpoint, self._failing = inner, endpoint, False

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self._inner.export(spans)
        except Exception:  # noqa: BLE001 - an exporter must not raise into the batch thread
            result = SpanExportResult.FAILURE
        failed = result is SpanExportResult.FAILURE
        if failed and not self._failing:
            _LOG.warning(
                "tracing: cannot export to %s, spans are dropped until it is back (trading is unaffected). "
                "Is Phoenix up? `uv run bazaar obs up`",
                self._endpoint,
            )
        self._failing = failed
        return result

    def shutdown(self) -> None:
        with suppress(Exception):
            self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        try:
            return self._inner.force_flush(timeout_millis)
        except Exception:  # noqa: BLE001
            return False


def install(
    tracer: Tracer,
    secrets: tuple[str, ...] = (),
    provider: TracerProvider | None = None,
    database_url: str | None = None,
) -> None:
    """Route every helper here to `tracer` (tests install an in-memory one)."""
    _RT.tracer, _RT.secrets, _RT.provider, _RT.database_url = tracer, secrets, provider, database_url


def uninstall() -> None:
    _RT.tracer, _RT.secrets, _RT.provider, _RT.database_url = None, (), None, None


def enabled() -> bool:
    return _RT.tracer is not None


def tracer() -> Tracer | None:
    """The installed tracer, or None when tracing is off."""
    return _RT.tracer


def init_tracing(service_name: str, config: TracingConfig | None = None, exporter: SpanExporter | None = None) -> bool:
    """Start exporting spans when tracing is enabled. Idempotent, never raises; True when on."""
    if _RT.tracer is not None:
        return True
    try:
        cfg = config or tracing_config()
        if not cfg.enabled:
            return False
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        headers = {"authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else None
        inner = exporter or OTLPSpanExporter(endpoint=cfg.endpoint, headers=headers, timeout=EXPORT_TIMEOUT_S)
        provider = TracerProvider(
            resource=Resource.create({SERVICE_NAME: service_name, ResourceAttributes.PROJECT_NAME: cfg.project})
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                QuietExporter(inner, cfg.endpoint),
                max_queue_size=MAX_QUEUE,
                schedule_delay_millis=EXPORT_DELAY_MS,
                max_export_batch_size=MAX_BATCH,
                export_timeout_millis=EXPORT_TIMEOUT_MS,
            )
        )
        # The exporter logs every failed batch and the processor every dropped span; QuietExporter
        # already warns once per outage, so neither may flood the trading console.
        logging.getLogger("opentelemetry.exporter").setLevel(logging.CRITICAL)
        logging.getLogger("opentelemetry.sdk._shared_internal").setLevel(logging.ERROR)
        install(provider.get_tracer("bazaar_agent"), cfg.secrets, provider, cfg.database_url)
        atexit.register(shutdown_tracing)
        return True
    except Exception as e:  # noqa: BLE001 - tracing is optional, trading is not
        _warn_once("init", "tracing could not start (%s); trading continues without it", e)
        return False


def shutdown_tracing() -> None:
    """Flush pending spans and stop. Bounded by the export timeout; never raises."""
    provider = _RT.provider
    uninstall()
    if provider is not None:
        with suppress(Exception):
            provider.shutdown()


# ---------------------------------------------------------------- what a span may carry


def add_secret(value: str) -> None:
    """A secret learnt at run time (our venue's broker key): cut out of every span and stored row by value,
    like the `*_KEY` variables read at start."""
    value = value.strip()
    if len(value) >= MIN_SECRET_LENGTH and value not in _RT.secrets:
        _RT.secrets = tuple(sorted({*_RT.secrets, value}, key=len, reverse=True))


def scrub(text: str) -> str:
    """DB passwords cut out (`pgconn.redact`: libpq can echo one), our secret values and team-key
    shapes cut out, then the Jev masking."""
    text = redact_db_passwords(text, _RT.database_url)
    for secret in _RT.secrets:
        text = text.replace(secret, JEV_REDACTION)
    return mask_text(_TEAM_KEY.sub(JEV_REDACTION, text))


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def attributes(values: Mapping[str, object]) -> dict[str, SpanValue]:
    """OTel-legal, scrubbed attributes: None dropped, strings masked, anything else as JSON."""
    out: dict[str, SpanValue] = {}
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool | int | float):
            out[key] = value
        elif isinstance(value, str):
            out[key] = scrub(value)
        elif isinstance(value, list | tuple) and all(_is_int(v) for v in value):
            out[key] = [int(v) for v in value]
        elif isinstance(value, list | tuple) and all(isinstance(v, str) for v in value):
            out[key] = [scrub(v) for v in value]
        else:
            out[key] = scrub(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
    return out


def as_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# ---------------------------------------------------------------- spans and events


@never_raise
def _start(name: str, kind: str, values: Mapping[str, object] | None, root: bool) -> Span | None:
    if _RT.tracer is None:
        return None
    from opentelemetry.context import Context

    return _RT.tracer.start_span(
        name, context=Context() if root else None, attributes=attributes({KIND: kind, **(values or {})})
    )


@contextmanager
def span(
    name: str, kind: str = CHAIN, values: Mapping[str, object] | None = None, *, root: bool = False
) -> Iterator[Span]:
    """A span made current for the block (a child of the current one unless `root`). Off: a no-op
    span. An exception from the block is recorded (scrubbed, with its stack) and re-raised."""
    started = _start(name, kind, values, root)
    if started is None:
        yield trace.INVALID_SPAN
        return
    with trace.use_span(started, end_on_exit=True, record_exception=False, set_status_on_exception=False):
        try:
            yield started
        except BaseException as exc:
            record_failure(started, exc)
            raise


@never_raise
def set_attributes(target: Span, values: Mapping[str, object]) -> None:
    if target.is_recording():
        target.set_attributes(attributes(values))


@never_raise
def add_event(target: Span, name: str, values: Mapping[str, object] | None = None) -> None:
    if target.is_recording():
        target.add_event(name, attributes(values or {}))


def event(name: str, values: Mapping[str, object] | None = None) -> None:
    """An event on the current span (the tick being handled, the capture being run, …)."""
    add_event(trace.get_current_span(), name, values)


@never_raise
def record_failure(target: Span, exc: BaseException) -> None:
    """The exception with its stack, scrubbed, and status ERROR (the OTel `exception` event shape)."""
    if not target.is_recording():
        return
    code = getattr(exc, "code", None)
    target.add_event(
        "exception",
        attributes(
            {
                "exception.type": type(exc).__qualname__,
                "exception.message": str(exc),
                "exception.stacktrace": "".join(traceback.format_exception(exc)),
                "bazaar.error.code": code if isinstance(code, str) else None,
                "bazaar.error.status": getattr(exc, "status", None),
            }
        ),
    )
    target.set_status(Status(StatusCode.ERROR, scrub(f"{type(exc).__name__}: {exc}")[:300]))


@never_raise
def record_jev(result: JudgeResult, question: str) -> None:
    """A `jev_verdict` event on the current span: verdict, value, probabilities, latency, model."""
    verdict = result.verdicts.get(question)
    if verdict is None:
        return
    event(
        "jev_verdict",
        {
            "question": question,
            "verdict": verdict.verdict,
            "decided": verdict.decided,
            "value": verdict.value,
            "value_kind": verdict.value_kind,
            "threshold": verdict.threshold,
            "leaning": verdict.leaning,
            "margin": verdict.margin,
            "reason": verdict.reason,
            "probabilities": dict(verdict.probabilities) if verdict.probabilities is not None else None,
            "latency_ms": result.latency_ms,
            "model": result.model,
        },
    )


@never_raise
def guardrail_refusal(stage: str, item: str, violations: Sequence[str]) -> None:
    """A refusal that happens outside any negotiation (e.g. before opening a thread): its own trace."""
    with span(
        "guardrail",
        GUARDRAIL,
        {"bazaar.stage": stage, "bazaar.item": item, OUTPUT: "; ".join(violations), TAGS: ["guardrail:denied"]},
        root=True,
    ) as current:
        add_event(current, "guardrail", {"allowed": False, "violations": list(violations), "stage": stage})


def fail_current(exc: BaseException) -> None:
    """Record `exc` on the current span (a DB write that failed inside a tick that goes on)."""
    record_failure(trace.get_current_span(), exc)


def _clean_exit(exc: BaseException) -> bool:
    """`typer.Exit()` / `sys.exit(0)`: the command ended on purpose with code 0, it did not fail."""
    return getattr(exc, "exit_code", getattr(exc, "code", 1)) in (0, None)


@contextmanager
def command_span(path: str) -> Iterator[Span]:
    """Root span `cli <path>` for one CLI command: console lines printed outside a tick land on it.

    The CLI enters it with `ctx.with_resource`, and click closes resources while a failing command's
    exception is still unwinding, so the exception is read from `sys.exc_info()` after the block.
    """
    with span(f"cli {path}", CHAIN, {"bazaar.command": path, INPUT: path}, root=True) as current:
        yield current
        exc = sys.exc_info()[1]
        if exc is not None and not _clean_exit(exc):
            record_failure(current, exc)


def plain_text(renderables: Sequence[ConsoleRenderable], width: int) -> str:
    """What a terminal would show for `renderables`, without colour or markup (tables included)."""
    sink = Console(file=io.StringIO(), width=width, color_system=None, highlight=False, emoji=False)
    sink.print(*renderables)
    return sink.file.getvalue().rstrip()  # type: ignore[attr-defined]


class ConsoleToSpans(RenderHook):
    """Every console print, as plain text, becomes a `console` event on the current span.

    Installed only when tracing is on, so with tracing off the console is untouched. The hook never
    changes what is printed and never raises.
    """

    def __init__(self, command: str, width: int = 160) -> None:
        self._command, self._width = command, width

    def process_renderables(self, renderables: list[ConsoleRenderable]) -> list[ConsoleRenderable]:
        self._emit(renderables)
        return renderables

    @never_raise
    def _emit(self, renderables: list[ConsoleRenderable]) -> None:
        if _RT.tracer is None:
            return
        text = plain_text(renderables, self._width)
        if text:
            event("console", {"line": text, "bazaar.command": _leaf_command() or self._command})


def _leaf_command() -> str | None:
    """'dealer buy' while `bazaar dealer buy …` runs: the innermost command, without the program name."""
    from typer.main import get_current_context

    ctx = get_current_context(silent=True)
    parts = ctx.command_path.split(" ", 1) if ctx is not None else []
    return parts[1] if len(parts) == 2 else None


def capture_console(console: Console, command: str) -> ConsoleToSpans | None:
    """Mirror `console` into spans when tracing is on; returns the hook (pop it to stop), or None."""
    if _RT.tracer is None:
        return None
    hook = ConsoleToSpans(command, max(console.width, 120))
    console.push_render_hook(hook)
    return hook
