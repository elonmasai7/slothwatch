"""CSV ingest. Never modifies the source file. Handles the usual bank-export quirks:
separate debit/credit columns, (parenthesised) negatives, currency symbols,
thousands separators, mixed date formats, BOM, odd delimiters."""
from __future__ import annotations
import csv, hashlib, re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from .config import Config

SYN = {
    "date": ["date", "transaction date", "posted date", "posting date", "trans date", "booking date", "txn date"],
    "description": ["description", "details", "narrative", "memo", "payee", "merchant", "name", "transaction", "particulars"],
    "amount": ["amount", "value", "amt"],
    "debit": ["debit", "withdrawal", "withdrawals", "money out", "paid out"],
    "credit": ["credit", "deposit", "deposits", "money in", "paid in"],
    "account": ["account", "account name", "account number"],
}


@dataclass
class Tx:
    date: date
    desc: str
    amount: float            # spend is positive; credits/refunds are dropped
    account: str = ""
    key: str = ""
    seq: int = 0
    merchant: str = ""
    category: str = "other"


def parse_amount(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.strip().replace("\u2212", "-")
    if not s:
        return None
    neg = (s.startswith("(") and s.endswith(")")) or s.startswith("-") or s.endswith("-") or s.upper().endswith("DR")
    t = re.sub(r"[^\d.,]", "", s)
    if not re.search(r"\d", t):
        return None
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        parts = t.split(",")
        t = t.replace(",", ".") if len(parts) == 2 and len(parts[1]) == 2 else t.replace(",", "")
    v = float(t)
    return -v if neg else v


def _formats(dayfirst: bool) -> list[str]:
    dm, md = ["%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y"], ["%m/%d/%Y", "%m/%d/%y"]
    other = ["%Y-%m-%d", "%Y/%m/%d", "%d %b %Y", "%b %d, %Y", "%d %B %Y"]
    return other + (dm + md if dayfirst else md + dm)


def detect_date_format(samples: list[str], dayfirst: bool) -> str:
    for fmt in _formats(dayfirst):
        try:
            for s in samples:
                datetime.strptime(s.strip(), fmt)
            return fmt
        except ValueError:
            continue
    raise ValueError(f"Could not detect a date format from samples like {samples[:3]!r}. "
                     "Set `columns`/`dayfirst` in slothwatch.toml.")


def _find(header: list[str], want: str, override: dict) -> str | None:
    if want in override:
        return override[want]
    low = {h.strip().lower(): h for h in header}
    for name in SYN[want]:
        if name in low:
            return low[name]
    return None


def read_csv(path: str | Path, cfg: Config) -> tuple[list[Tx], dict]:
    return read_csv_text(Path(path).read_text(encoding="utf-8-sig", errors="replace"), cfg)


def read_csv_text(raw: str, cfg: Config) -> tuple[list[Tx], dict]:
    """Parse CSV contents already in memory, such as a dashboard upload."""
    try:
        dialect = csv.Sniffer().sniff(raw[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.DictReader(raw.splitlines(), dialect=dialect))
    if not rows:
        return [], {"skipped_credits": 0, "rows": 0}
    header = list(rows[0].keys())
    c = {k: _find(header, k, cfg.columns) for k in SYN}
    if not c["date"] or not c["description"] or not (c["amount"] or c["debit"] or c["credit"]):
        raise ValueError(f"Could not map columns from header {header}. Add a `columns` mapping to slothwatch.toml.")
    fmt = detect_date_format([r[c["date"]] for r in rows if r.get(c["date"], "").strip()][:50], cfg.dayfirst)

    sign = cfg.amount_sign
    if sign == "auto" and c["amount"]:
        vals = [parse_amount(r[c["amount"]]) for r in rows]
        vals = [v for v in vals if v]
        sign = "negative_is_spend" if sum(v < 0 for v in vals) > sum(v > 0 for v in vals) else "positive_is_spend"

    out, skipped, occ = [], 0, {}
    for r in rows:
        if not r.get(c["date"], "").strip():
            continue
        d = datetime.strptime(r[c["date"]].strip(), fmt).date()
        if c["debit"] or c["credit"]:
            deb = parse_amount(r.get(c["debit"])) if c["debit"] else None
            cre = parse_amount(r.get(c["credit"])) if c["credit"] else None
            spend = abs(deb) if deb else (-abs(cre) if cre else None)
        else:
            a = parse_amount(r[c["amount"]])
            spend = None if a is None else (-a if sign == "negative_is_spend" else a)
        if spend is None or spend <= 0:
            skipped += 1
            continue
        desc = (r.get(c["description"]) or "").strip()
        k = (d, desc, round(spend, 2))
        occ[k] = occ.get(k, 0) + 1
        key = hashlib.sha1(f"{d}|{desc}|{spend:.2f}|{occ[k]}".encode()).hexdigest()[:10]
        out.append(Tx(d, desc, round(spend, 2), (r.get(c["account"]) or "") if c["account"] else "", key))
    return out, {"skipped_credits": skipped, "rows": len(out), "date_format": fmt, "sign": sign}


def read_pdf(data: bytes, cfg: Config) -> tuple[list[Tx], dict]:
    """Extract simple statement rows from a text PDF; scanned/image PDFs need OCR/export to CSV."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("PDF support needs pypdf. Install with `pip install -e '.[pdf]'`.") from exc
    import io

    try:
        text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    except Exception as exc:
        raise ValueError("Could not read this PDF. Try exporting the statement as CSV.") from exc
    date_re = re.compile(r"^(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\s+(.+?)\s+([\(\-]?\s*(?:[A-Z]{3}\s*)?[$€£]?\d[\d,.]*\)?-?)\s*$")
    out, occurrence = [], {}
    for line in text.splitlines():
        m = date_re.match(" ".join(line.split()))
        if not m:
            continue
        raw_date, desc, raw_amount = m.groups()
        parsed_date = None
        for fmt in _formats(cfg.dayfirst):
            try:
                parsed_date = datetime.strptime(raw_date, fmt).date()
                break
            except ValueError:
                continue
        amount = parse_amount(raw_amount)
        if parsed_date is None or amount is None or amount <= 0 or not desc.strip():
            continue
        identity = (parsed_date, desc.strip(), round(amount, 2))
        occurrence[identity] = occurrence.get(identity, 0) + 1
        key = hashlib.sha1(f"{identity}|{occurrence[identity]}|pdf".encode()).hexdigest()[:10]
        out.append(Tx(parsed_date, desc.strip(), round(amount, 2), key=key))
    if not out:
        raise ValueError("No readable transaction rows found. This PDF may be scanned or use a different layout; export it as CSV.")
    out.sort(key=lambda tx: (tx.date, tx.desc, tx.amount))
    for i, tx in enumerate(out):
        tx.seq = i
    return out, {"rows": len(out), "format": "text PDF"}


def load_path(path: str | Path, cfg: Config) -> list[Tx]:
    """A file, or a folder of CSVs (overlapping exports are merged; true repeat charges inside one file are kept)."""
    p = Path(path)
    files = sorted(p.glob("*.csv")) if p.is_dir() else [p]
    if not files:
        raise FileNotFoundError(f"No CSV files found at {p}")
    seen: dict[str, Tx] = {}
    for f in files:
        for tx in read_csv(f, cfg)[0]:
            seen.setdefault(tx.key, tx)
    txs = sorted(seen.values(), key=lambda t: (t.date, t.desc, t.amount))
    for i, t in enumerate(txs):
        t.seq = i
    return txs
