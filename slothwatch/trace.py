"""Agent tracing. One span per rung. Always writes local JSONL; also sends to Sentry when a DSN is set
and sentry-sdk is installed. Local inference is tracked with cost 0.0."""
from __future__ import annotations
import contextlib, json, time, uuid
from datetime import datetime
from .config import Config


class Span:
    def __init__(self, name: str):
        self.name, self.attrs = name, {}

    def set(self, **kw):
        self.attrs.update(kw)


class Tracer:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sentry = None
        if cfg.sentry_dsn:
            try:
                import sentry_sdk
                sentry_sdk.init(dsn=cfg.sentry_dsn, traces_sample_rate=1.0, send_default_pii=False)
                self.sentry = sentry_sdk
            except Exception:
                self.sentry = None
        self.trace_id = uuid.uuid4().hex[:12]
        self.file = cfg.path("traces", f"{datetime.now():%Y-%m-%d}.jsonl")

    def new_trace(self):
        self.trace_id = uuid.uuid4().hex[:12]

    @contextlib.contextmanager
    def span(self, name: str, **attrs):
        sp = Span(name)
        sp.attrs.update(attrs)
        t0 = time.perf_counter()
        cm = self.sentry.start_span(op="gen_ai.invoke_agent" if name == "run" else "gen_ai.rung", name=name) \
            if self.sentry else contextlib.nullcontext(None)
        with cm as sentry_span:
            try:
                yield sp
            finally:
                ms = (time.perf_counter() - t0) * 1000
                sp.attrs.setdefault("cost_usd", 0.0)
                if sentry_span is not None:
                    for k, v in sp.attrs.items():
                        sentry_span.set_data(k, v)
                with open(self.file, "a") as f:
                    f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "trace": self.trace_id,
                                        "span": name, "duration_ms": round(ms, 2), "attrs": sp.attrs}) + "\n")


class NullTracer(Tracer):
    def __init__(self):
        self.sentry = None
        self.trace_id = "null"

    @contextlib.contextmanager
    def span(self, name, **attrs):
        sp = Span(name)
        sp.attrs.update(attrs)
        yield sp
