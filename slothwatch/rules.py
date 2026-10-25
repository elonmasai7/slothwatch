"""Rung 0: plain rules. Costs nothing and settles most rows."""
from __future__ import annotations
from dataclasses import dataclass
from .config import Config
from .ingest import Tx
from .normalize import Profile


@dataclass
class RuleOutcome:
    settled: bool            # True -> row is done, rung 0 stayed in charge
    reason: str = ""
    flag: str | None = None  # a surprise kind raised by a rule (escalates)
    note: str = ""


def build_index(pool: list[Tx]) -> dict[str, list[Tx]]:
    idx: dict[str, list[Tx]] = {}
    for t in pool:
        idx.setdefault(t.merchant, []).append(t)
    return idx


def apply_rules(tx: Tx, by_merchant: dict[str, list[Tx]], profiles: dict[str, Profile],
                history_merchants: set[str], cfg: Config) -> RuleOutcome:
    m = tx.merchant
    if any(k and k in m for k in map(str.upper, cfg.ignore_merchants)):
        return RuleOutcome(True, "ignore_list")
    if any(k and k in m for k in map(str.upper, cfg.allow_merchants)):
        return RuleOutcome(True, "allow_list")

    # duplicate / near-duplicate: same merchant + amount inside the window, earlier in the stream
    if tx.amount >= cfg.dupe_min_amount:
        for o in by_merchant.get(m, []):
            if o.key != tx.key and o.seq < tx.seq and abs(o.amount - tx.amount) < 0.005 \
                    and 0 <= (tx.date - o.date).days <= cfg.dupe_window_days:
                return RuleOutcome(False, flag="duplicate",
                                   note=f"same amount charged {(tx.date - o.date).days} day(s) earlier")

    prof = profiles.get(m)
    if prof is None:
        if m not in history_merchants:
            if tx.amount >= cfg.large_new_merchant:
                return RuleOutcome(False, flag="large_new_merchant", note="first charge from this merchant")
            return RuleOutcome(True, "new_merchant_small")
        return RuleOutcome(True, "not_recurring")

    prev = [o for o in by_merchant.get(m, []) if o.seq < tx.seq]
    if prev:
        last = max(prev, key=lambda o: (o.date, o.seq)).amount
        if abs(tx.amount - last) <= cfg.same_cycle_tolerance * max(last, 1):
            return RuleOutcome(True, "same_as_last_cycle")
    return RuleOutcome(False, "recurring_changed")
