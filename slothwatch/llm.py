"""Rung 2: local Gemma via Ollama's HTTP API. Strict-JSON output, schema-validated, one retry,
templated fallback when the model is unavailable. Token counts are MEASURED when Ollama answers
and ESTIMATED (chars/4) otherwise; the `estimated` flag is always carried."""
from __future__ import annotations
import json, time, urllib.request
from dataclasses import dataclass
from pathlib import Path
from .config import Config

PROMPTS = Path(__file__).parent / "prompts"
SEVERITIES = {"ignore", "watch", "act"}
KEYS = ("verdict_two_sentences", "severity", "suggested_action", "draft_message")


@dataclass
class Explanation:
    verdict: str
    severity: str
    action: str
    draft: str
    source: str              # "gemma" | "template"
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    estimated: bool = False
    model: str = ""


def load_prompt(name: str) -> str:
    return (PROMPTS / name).read_text()


def validate(obj) -> dict | None:
    if not isinstance(obj, dict) or any(k not in obj or not isinstance(obj[k], str) for k in KEYS):
        return None
    if obj["severity"] not in SEVERITIES or not obj["verdict_two_sentences"].strip():
        return None
    return obj


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class Ollama:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._ok: bool | None = None

    def available(self) -> bool:
        if self._ok is None:
            try:
                with urllib.request.urlopen(self.cfg.ollama_url + "/api/tags", timeout=1.0) as r:
                    names = [m["name"] for m in json.load(r).get("models", [])]
                self._ok = any(n.split(":")[0] == self.cfg.ollama_model.split(":")[0] for n in names)
            except Exception:
                self._ok = False
        return self._ok

    def generate(self, prompt: str) -> tuple[str, int, int]:
        body = json.dumps({"model": self.cfg.ollama_model, "prompt": prompt, "stream": False, "format": "json",
                           "options": {"temperature": 0, "seed": 0}}).encode()
        req = urllib.request.Request(self.cfg.ollama_url + "/api/generate", body,
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.cfg.ollama_timeout) as r:
            d = json.load(r)
        return d.get("response", ""), d.get("prompt_eval_count", 0), d.get("eval_count", 0)


def template(s: dict, cur: str = "$") -> dict:
    m, kind, a = s["merchant"], s["kind"], f'{cur}{s["amount"]:.2f}'
    dev = s.get("deviation", 0.0)
    if kind == "duplicate":
        return dict(verdict_two_sentences=f"{m} charged {a} twice within a few days. This looks like an accidental duplicate.",
                    severity="act", suggested_action=f"Ask {m} to refund the second {a} charge.",
                    draft_message=f"Hello, I was charged {a} twice by {m} within a few days. Please refund the duplicate charge. Thank you.")
    if kind == "creeping_increase":
        sev = "act" if dev >= 15 else "watch"
        return dict(verdict_two_sentences=f"{m} has crept up {dev:.0f}% over its last charges in small steps, and now bills {a}. Each rise looks minor, but together they add up.",
                    severity=sev, suggested_action=f"Ask {m} to review your rate or look for a cheaper plan.",
                    draft_message=f"Hello, I noticed my {m} charges have risen about {dev:.0f}% over recent months. Could you review my rate or tell me what changed? Thank you.")
    if kind == "large_new_merchant":
        return dict(verdict_two_sentences=f"First-ever charge from {m} for {a}, larger than your usual new-merchant spend. Confirm you recognise it.",
                    severity="act", suggested_action=f"Check that you authorised the {a} charge from {m}.",
                    draft_message=f"Hello, I do not recognise a {a} charge from {m}. Please send the transaction details so I can verify it. Thank you.")
    sev = "act" if abs(dev) >= 30 else ("ignore" if abs(dev) < 10 else "watch")
    return dict(verdict_two_sentences=f"{m} charged {a}, {dev:+.0f}% versus the expected {cur}{s.get('expected', 0):.2f}. That is outside its usual range of {cur}{s.get('lo', 0):.2f} to {cur}{s.get('hi', 0):.2f}.",
                severity=sev, suggested_action=f"Check the {m} statement for what changed.",
                draft_message="" if sev == "ignore" else f"Hello, my latest {m} charge of {a} is higher than usual. Could you explain the change? Thank you.")


class Explainer:
    def __init__(self, cfg: Config, tracer=None):
        self.cfg, self.tracer = cfg, tracer
        self.ollama = Ollama(cfg)
        self.prompt_tpl = load_prompt("explain_v1.txt")

    def prompt_for(self, s: dict) -> str:
        c = self.cfg.currency
        return self.prompt_tpl.format(merchant=s["merchant"], date=s["date"], amount=f'{c}{s["amount"]:.2f}',
                                      kind=s["kind"], note=s.get("note", "") or "n/a",
                                      expected=f'{c}{s.get("expected", 0):.2f}', lo=f'{c}{s.get("lo", 0):.2f}',
                                      hi=f'{c}{s.get("hi", 0):.2f}', method=s.get("method", "n/a"),
                                      deviation=s.get("deviation", 0.0))

    def explain(self, s: dict) -> Explanation:
        prompt = self.prompt_for(s)
        t0 = time.perf_counter()
        if self.ollama.available():
            tin = tout = 0
            for _ in range(2):  # one retry on invalid JSON
                try:
                    text, a, b = self.ollama.generate(prompt)
                    tin, tout = tin + a, tout + b
                    obj = validate(json.loads(text))
                    if obj:
                        return Explanation(obj["verdict_two_sentences"], obj["severity"], obj["suggested_action"],
                                           obj["draft_message"], "gemma", tin, tout,
                                           (time.perf_counter() - t0) * 1000, False, self.cfg.ollama_model)
                except Exception:
                    continue
        t = template(s, self.cfg.currency)
        return Explanation(t["verdict_two_sentences"], t["severity"], t["suggested_action"], t["draft_message"],
                           "template", estimate_tokens(prompt), estimate_tokens(json.dumps(t)),
                           (time.perf_counter() - t0) * 1000, True, "template-fallback")
