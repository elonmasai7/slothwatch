"""Merchant cleaning, fuzzy grouping, category guess, recurring detection."""
from __future__ import annotations
import difflib, re, statistics
from dataclasses import dataclass, field
from datetime import date
from .config import Config
from .ingest import Tx

NOISE = {"CARD", "POS", "DEBIT", "PURCHASE", "PAYMENT", "PMT", "ACH", "VISA", "MC", "RECURRING", "ONLINE",
         "WWW", "COM", "REF", "TXN", "ID", "AUTOPAY", "DD", "SO"}
CATS = [("utility", ("ELECTRIC", "POWER", "WATER", "GAS", "ENERGY", "UTILIT", "WASTE", "INTERNET", "FIBER",
                     "MOBILE", "PHONE", "WIRELESS", "BROADBAND")),
        ("insurance", ("INSURANCE", "ASSUR", "COVER")),
        ("housing", ("RENT", "MORTGAGE", "LANDLORD")),
        ("subscription", ("STREAM", "MUSIC", "PLUS", "PREMIUM", "CLOUD", "NEWS", "GYM", "FITNESS", "VAULT"))]


def clean_merchant(desc: str) -> str:
    s = desc.upper()
    s = re.sub(r"\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?", " ", s)
    s = re.sub(r"[#*_/\\]", " ", s)
    s = " ".join(t for t in s.split() if not any(ch.isdigit() for ch in t))
    s = re.sub(r"[^A-Z ]", " ", s)          # drops '&' and punctuation
    toks = [t for t in s.split() if t not in NOISE and len(t) > 1]
    return " ".join(toks[:4]) or "UNKNOWN"


def categorize(name: str) -> str:
    for cat, words in CATS:
        if any(w in name for w in words):
            return cat
    return "other"


class MerchantIndex:
    """Groups variants of one merchant. Deliberately conservative: merging two DIFFERENT bills pollutes both
    histories (found the hard way: SOUTH WASTE vs SOUTH WATER scores 0.909 on plain similarity).
    Merge only on a whole-token prefix ("STREAMBOX PREMIUM" ~ "STREAMBOX PREMIUM CO") or a near-typo (>= 0.95)."""
    def __init__(self, cutoff: float = 0.95):
        self.known: list[str] = []
        self.cutoff = cutoff

    @staticmethod
    def _prefix(a: str, b: str) -> bool:
        ta, tb = a.split(), b.split()
        short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        return len(short) >= 2 and long_[:len(short)] == short

    def resolve(self, name: str) -> str:
        if name in self.known:
            return name
        for k in self.known:
            if self._prefix(name, k) or difflib.SequenceMatcher(None, name, k).ratio() >= self.cutoff:
                return k
        self.known.append(name)
        return name


def annotate(txs: list[Tx], index: MerchantIndex | None = None) -> list[Tx]:
    index = index or MerchantIndex()
    for t in txs:
        t.merchant = index.resolve(clean_merchant(t.desc))
        t.category = categorize(t.merchant)
    return txs


@dataclass
class Profile:
    merchant: str
    cadence: str
    period: int
    n: int
    category: str
    dates: list[date] = field(default_factory=list)
    amounts: list[float] = field(default_factory=list)


def _cadence(median_gap: float):
    if 5 <= median_gap <= 9:
        return "weekly", 7
    if 26 <= median_gap <= 35:
        return "monthly", 30
    if 350 <= median_gap <= 380:
        return "annual", 365
    return None, 0


def detect_recurring(history: list[Tx], cfg: Config) -> dict[str, Profile]:
    by: dict[str, list[Tx]] = {}
    for t in history:
        by.setdefault(t.merchant, []).append(t)
    out: dict[str, Profile] = {}
    for m, rows in by.items():
        rows = sorted(rows, key=lambda t: t.date)
        if len(rows) < 3:
            continue
        gaps = [(b.date - a.date).days for a, b in zip(rows, rows[1:])]
        long_gaps = [g for g in gaps if g > cfg.dupe_window_days]
        if not long_gaps or (len(gaps) - len(long_gaps)) / len(gaps) > 0.15:
            continue
        med = statistics.median(long_gaps)
        cad, period = _cadence(med)
        if not cad or sum(abs(g - med) <= 0.2 * med for g in long_gaps) / len(long_gaps) < 0.7:
            continue
        amts = [t.amount for t in rows]
        mean = statistics.fmean(amts)
        if mean <= 0 or (statistics.pstdev(amts) / mean) > 0.5:
            continue
        out[m] = Profile(m, cad, period, len(rows), rows[0].category, [t.date for t in rows], amts)
    return out
