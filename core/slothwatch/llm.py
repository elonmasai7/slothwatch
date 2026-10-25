"""Strict-JSON local Gemma explainer with one retry and a transparent template fallback."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SEVERITIES = {"ignore", "watch", "act"}


def validate_verdict(value: Any) -> dict[str, str]:
    """Validate the four-field JSON contract without accepting extra/missing fields."""
    required = {"verdict_two_sentences", "severity", "suggested_action", "draft_message"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("verdict JSON must contain exactly the four documented fields")
    if value["severity"] not in SEVERITIES:
        raise ValueError("severity must be ignore, watch, or act")
    for key in required:
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValueError(f"{key} must be a non-empty string")
    sentences = re.split(r"(?<=[.!?])\s+", value["verdict_two_sentences"].strip())
    if len(sentences) != 2:
        raise ValueError("verdict_two_sentences must contain exactly two sentences")
    return value


def template_verdict(row: dict, context: dict, reason: str) -> dict[str, str]:
    """Generate a deterministic and safe local verdict when no model is available."""
    merchant = str(row.get("merchant", "Unknown merchant"))
    amount = float(row.get("amount", 0.0))
    expected = context.get("expected")
    if expected is None:
        detail = reason.rstrip(".")
    else:
        detail = f"the observed amount {abs(amount):.2f} is outside the expected {context.get('low', 0):.2f}–{context.get('high', 0):.2f} interval"
    severity = "act" if "duplicate" in reason.lower() or abs(amount) >= 250 or "spike" in reason.lower() else "watch"
    return validate_verdict({
        "verdict_two_sentences": f"{merchant} needs a quick review because {detail}. Check the original statement before taking any action.",
        "severity": severity,
        "suggested_action": "Review the charge and contact the provider if it is incorrect.",
        "draft_message": f"Hello, I am reviewing a transaction from {merchant} dated {row.get('date', 'the statement date')} for {abs(amount):.2f}. Could you confirm what it relates to and explain the amount?",
    })


def explain_with_ollama(row: dict, context: dict, reason: str, llm_config: dict, use_ollama: bool = True) -> tuple[dict[str, str], dict[str, Any]]:
    """Call local Ollama only with one flagged row and its forecast context; fall back safely."""
    prompt_path = Path(__file__).parent / "prompts" / "gemma_verdict_v1.txt"
    prompt_template = prompt_path.read_text(encoding="utf-8")
    safe_input = {"flagged_row": {k: row.get(k) for k in ("date", "merchant", "amount", "category")}, "forecast_context": context, "rule_reason": reason}
    prompt = prompt_template + "\nINPUT JSON:\n" + json.dumps(safe_input, ensure_ascii=False)
    if use_ollama and llm_config.get("enabled", True):
        endpoint = str(llm_config.get("endpoint", "http://127.0.0.1:11434/api/generate"))
        parsed_endpoint = urlsplit(endpoint)
        if parsed_endpoint.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed_endpoint.scheme != "http":
            # Never allow the model adapter to exfiltrate personal transactions.
            return template_verdict(row, context, reason), {"provider": "template", "tokens_in": 0, "tokens_out": 0, "error": "non-local Ollama endpoint refused"}
        payload = {"model": str(llm_config.get("model", "gemma3:4b")), "prompt": prompt, "stream": False, "format": "json", "options": {"temperature": 0}}
        for attempt in range(2):
            try:
                request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(request, timeout=float(llm_config.get("timeout_seconds", 4))) as response:
                    body = json.loads(response.read().decode("utf-8"))
                text = body.get("response", "")
                if not text and isinstance(body.get("message"), dict):
                    text = body["message"].get("content", "")
                result = validate_verdict(json.loads(text))
                has_model_counts = isinstance(body.get("prompt_eval_count"), int) and isinstance(body.get("eval_count"), int)
                return result, {"provider": "ollama", "model": payload["model"], "tokens_in": int(body.get("prompt_eval_count", len(prompt.split()))), "tokens_out": int(body.get("eval_count", len(text.split()))), "token_source": "ollama-metrics" if has_model_counts else "whitespace-estimate", "attempts": attempt + 1}
            except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
                if attempt == 1:
                    return template_verdict(row, context, reason), {"provider": "template", "tokens_in": 0, "tokens_out": 0, "error": type(exc).__name__}
    return template_verdict(row, context, reason), {"provider": "template", "tokens_in": 0, "tokens_out": 0}
