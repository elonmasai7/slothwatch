"""Local recurring-item forecasts with optional TabPFN and robust fallback intervals."""
from __future__ import annotations

import statistics
from datetime import date, timedelta
import calendar
from typing import Any

from .tabpfn_local import enabled_checkpoint, offline_import_environment

from .ingest import Transaction


def robust_interval(history: list[float], mad_multiplier: float = 3.5) -> tuple[float, float, float]:
    """Return median and robust MAD interval, resistant to outliers."""
    if not history:
        raise ValueError("forecast requires at least one prior observation")
    median = statistics.median(history)
    mad = statistics.median(abs(x - median) for x in history)
    sigma = 1.4826 * mad
    spread = max(mad_multiplier * sigma, abs(median) * 0.03, 1.0)
    return median, max(0.0, median - spread), median + spread



def _feature_row(dates: list[str], amounts: list[float], index: int, current_date: str) -> list[float]:
    """Build simple cycle, calendar, days-since-last and rolling-median features."""
    current = date.fromisoformat(current_date)
    gap = 30.0 if index <= 0 else float(max(1, (current - date.fromisoformat(dates[index - 1])).days))
    recent = amounts[max(0, index - 3):index]
    rolling = statistics.median(recent) if recent else 0.0
    return [float(index), float(current.month), gap, float(rolling)]


def _tabpfn_forecast(dates: list[str], amounts: list[float], next_date: str) -> dict[str, Any] | None:
    """Forecast with a cached local TabPFN checkpoint; return None when unavailable."""
    checkpoint = enabled_checkpoint()
    if checkpoint is None or len(amounts) < 4:
        return None
    offline_import_environment()
    try:
        from tabpfn import TabPFNRegressor

        x_train = [_feature_row(dates, amounts, i, dates[i]) for i in range(len(amounts))]
        x_next = [_feature_row(dates, amounts, len(amounts), next_date)]
        model = TabPFNRegressor(model_path=str(checkpoint), random_state=0, show_progress_bar=False)
        model.fit(x_train, amounts)
        expected = float(model.predict(x_next, output_type="mean")[0])
        quantiles = model.predict(x_next, output_type="quantiles", quantiles=[0.05, 0.95])
        if isinstance(quantiles, dict):
            quantiles = quantiles.get("quantiles", quantiles)
        low = float(quantiles[0][0])
        high = float(quantiles[1][0])
        if not all(map(lambda x: x == x and abs(x) != float("inf"), (expected, low, high))):
            return None
        return {"expected": max(0.0, expected), "low": max(0.0, min(low, high)), "high": max(low, high), "method": f"TabPFN-local-quantiles:{checkpoint.name}"}
    except Exception:
        # The caller records a robust fallback. No exception text is logged because it
        # can include local paths or device details. No remote model is attempted.
        return None


def forecast_rows(rows: list[Transaction], min_observations: int = 4, mad_multiplier: float = 3.5) -> dict[str, dict]:
    """Forecast each recurring row from earlier rows for the same merchant only."""
    groups: dict[str, list[Transaction]] = {}
    for row in sorted(rows, key=lambda x: (x.date, x.id)):
        groups.setdefault(row.merchant_id, []).append(row)
    results: dict[str, dict] = {}
    for merchant_id, items in groups.items():
        prior: list[float] = []
        for item in items:
            if len(prior) >= min_observations:
                try:
                    method = "robust-median-mad"
                    median, low, high = robust_interval(prior, mad_multiplier)
                    merchant_key = items[0].merchant.lower()
                    utility_item = any(key in merchant_key for key in ("electric", "utility", "water", "gas"))
                    if utility_item:
                        # Utility bills have legitimate seasonality; widen the fallback band rather than waking the model on season alone.
                        low = max(0.0, min(low, median * 0.55))
                        high = max(high, median * 1.45)
                    # TabPFN is opt-in and local-checkpoint-only. Missing weights or API/device
                    # incompatibility safely retain the robust median/MAD forecast.
                    if len(prior) >= min_observations:
                        prediction = _tabpfn_forecast([x.date for x in items[:len(prior)]], prior, item.date)
                        if prediction is not None:
                            median, low, high = prediction["expected"], prediction["low"], prediction["high"]
                            method = prediction["method"]
                    vals = prior[-4:]
                    # Creeping increase catches low month-to-month drift accumulating over cycles.
                    creep = (item.id == items[-1].id) and (not utility_item) and len(vals) >= 4 and vals[0] > 0 and (vals[-1] / vals[0] - 1) * 100 >= 8.0
                    results[item.id] = {
                        "expected": median,
                        "low": low,
                        "high": high,
                        "method": method,
                        "history_count": len(prior),
                        "creeping_increase": bool(creep and abs(item.amount) > vals[-1]),
                    }
                except (ValueError, statistics.StatisticsError):
                    pass
            prior.append(abs(item.amount))
    return results


def forecast_next_month(amounts: list[float], dates: list[str]) -> dict:
    """Forecast next recurrence with local TabPFN when explicitly enabled, else robust fallback."""
    if not amounts:
        return {"expected": None, "low": None, "high": None, "method": "insufficient-history"}
    if len(dates) >= 1 and len(amounts) >= 4:
        try:
            last = date.fromisoformat(dates[-1])
            month = 1 if last.month == 12 else last.month + 1
            year = last.year + 1 if last.month == 12 else last.year
            next_date = date(year, month, min(last.day, calendar.monthrange(year, month)[1])).isoformat()
            prediction = _tabpfn_forecast(dates, [abs(x) for x in amounts], next_date)
            if prediction is not None:
                return prediction
        except (ValueError, IndexError):
            pass
    median, low, high = robust_interval([abs(x) for x in amounts])
    return {"expected": median, "low": low, "high": high, "method": "robust-median-mad"}
