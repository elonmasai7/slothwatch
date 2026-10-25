from datetime import date

from slothwatch.forecast import forecast_next_month, robust_interval
from slothwatch.ingest import Transaction
from slothwatch.normalize import normalize_transactions
from slothwatch.rules import apply_rung0


def test_rung0_detects_duplicate_but_does_not_silently_discard_it():
    rows = [
        Transaction("one", "2026-01-04", "TuneCloud", 9.99),
        Transaction("two", "2026-01-06", "TuneCloud", 9.99),
    ]
    normalized = normalize_transactions(rows)
    result = apply_rung0(normalized, {"duplicate_window_days": 3})
    assert result["two"]["duplicate"] is True
    assert result["two"]["settled"] is False
    assert "duplicate" in result["two"]["reason"]


def test_robust_interval_and_next_month_fallback_are_deterministic(monkeypatch, tmp_path):
    monkeypatch.setenv("SLOTHWATCH_ENABLE_TABPFN", "1")
    monkeypatch.setenv("TABPFN_MODEL_CACHE_DIR", str(tmp_path / "empty-cache"))
    median, low, high = robust_interval([100, 101, 99, 102, 100])
    assert median == 100
    assert low < median < high
    result = forecast_next_month([100, 101, 99, 102], ["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"])
    assert result["method"] == "robust-median-mad"
    assert result["low"] <= result["expected"] <= result["high"]
