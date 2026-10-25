"""Low-noise local spans with optional Sentry initialization."""
from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class LocalTracer:
    """Write span records as JSON Lines, excluding transaction descriptions and amounts."""

    def __init__(self, trace_file: str | Path, sentry_dsn: str | None = None):
        self.path = Path(trace_file)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.sentry_enabled = False
        if sentry_dsn:
            try:
                import sentry_sdk
                sentry_sdk.init(dsn=sentry_dsn, traces_sample_rate=1.0, send_default_pii=False)
                self.sentry_enabled = True
            except ImportError:
                self.sentry_enabled = False

    @contextmanager
    def span(self, name: str, **attributes) -> Iterator[dict]:
        """Record a sanitized local span and mirror it to Sentry only when explicitly enabled."""
        start = time.perf_counter()
        span_id = uuid.uuid4().hex[:16]
        fields = {"span_id": span_id, "name": name, **attributes}
        sentry_cm = None
        if self.sentry_enabled:
            try:
                import sentry_sdk
                sentry_cm = sentry_sdk.start_span(op="ai.pipeline", name=name)
            except (ImportError, RuntimeError):
                sentry_cm = None
        try:
            if sentry_cm is None:
                yield fields
            else:
                with sentry_cm as sentry_span:
                    try:
                        yield fields
                    finally:
                        sentry_span.set_data("slothwatch.latency_ms", round((time.perf_counter() - start) * 1000, 3))
                        # Keep local attributes useful to the SlothWatch dashboard while
                        # adding common GenAI semantic-convention keys for Sentry AI monitoring.
                        for key, value in fields.items():
                            if isinstance(value, (str, int, float, bool)):
                                sentry_span.set_data(f"slothwatch.{key}", value)
                        sentry_span.set_data("gen_ai.operation.name", name)
                        model = fields.get("model")
                        if isinstance(model, str):
                            sentry_span.set_data("gen_ai.request.model", model)
                        if isinstance(fields.get("tokens_in"), int):
                            sentry_span.set_data("gen_ai.usage.input_tokens", fields["tokens_in"])
                        if isinstance(fields.get("tokens_out"), int):
                            sentry_span.set_data("gen_ai.usage.output_tokens", fields["tokens_out"])
        finally:
            fields["latency_ms"] = round((time.perf_counter() - start) * 1000, 3)
            fields.setdefault("tokens_in", 0)
            fields.setdefault("tokens_out", 0)
            fields.setdefault("cost_usd", 0.0)
            fields.setdefault("status", "ok")
            # Even when Sentry is configured, preserve a local sanitized copy for auditability.
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(fields, sort_keys=True) + "\n")


def sentry_dsn_from_env(config: dict) -> str | None:
    return os.getenv(config.get("sentry_dsn_env", "SENTRY_DSN")) or None
