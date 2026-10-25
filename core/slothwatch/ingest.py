"""Defensive, read-only CSV ingestion for common bank export formats."""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable


@dataclass
class Transaction:
    """Normalized transaction row; amount is signed from the account's perspective."""

    id: str
    date: str
    description: str
    amount: float
    account: str = ""
    category: str = ""
    merchant: str = ""
    merchant_id: str = ""
    source_file: str = ""
    is_anomaly: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _date_value(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise ValueError("empty date")
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y", "%d %b %Y", "%b %d, %Y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError as exc:
        raise ValueError(f"unsupported date format: {value!r}") from exc


def _amount_value(raw: str) -> float:
    """Parse currency-looking amounts, including $1,234.50 and (12.00)."""
    value = (raw or "").strip()
    if not value:
        return 0.0
    negative = value.startswith("(") and value.endswith(")")
    value = value.strip("() ").replace(",", "")
    value = re.sub(r"[^0-9.\-+]", "", value)
    if value in {"", ".", "-", "+"}:
        return 0.0
    amount = float(value)
    return -abs(amount) if negative else amount


def _headers(headers: Iterable[str]) -> dict[str, str]:
    normalized = {re.sub(r"[^a-z0-9]", "", h.lower()): h for h in headers}
    aliases = {
        "date": ("date", "transactiondate", "posteddate", "valuedate", "bookingdate", "transdate"),
        "description": ("description", "details", "merchant", "transaction", "narration", "payee", "name", "memo"),
        "amount": ("amount", "transactionamount", "value", "debitcredit", "sum"),
        "debit": ("debit", "withdrawal", "moneyout", "paidout", "debitamount"),
        "credit": ("credit", "deposit", "moneyin", "paidin", "creditamount"),
        "account": ("account", "accountname", "accountnumber", "card"),
        "category": ("category", "type", "classification"),
    }
    out: dict[str, str] = {}
    for key, candidates in aliases.items():
        for candidate in candidates:
            if candidate in normalized:
                out[key] = normalized[candidate]
                break
    return out


def read_csv(path: str | Path, mapping: dict[str, str] | None = None) -> list[Transaction]:
    """Read CSV rows without modifying the input file. Mapping keys are normalized field names."""
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(file_path)
    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(handle, dialect=dialect)
        if not reader.fieldnames:
            return []
        detected = _headers(reader.fieldnames)
        if mapping:
            detected.update(mapping)
        if "date" not in detected or "description" not in detected:
            raise ValueError("Could not detect date and description columns; set a column mapping")
        if not any(k in detected for k in ("amount", "debit", "credit")):
            raise ValueError("Could not detect amount/debit/credit column; set a column mapping")
        rows: list[Transaction] = []
        for index, row in enumerate(reader, start=2):
            try:
                tx_date = _date_value(row.get(detected["date"], ""))
            except ValueError as exc:
                raise ValueError(f"line {index}: {exc}") from exc
            description = (row.get(detected["description"], "") or "").strip()
            if not description:
                description = "Unknown transaction"
            if "amount" in detected:
                amount = _amount_value(row.get(detected["amount"], ""))
            else:
                debit = abs(_amount_value(row.get(detected.get("debit", ""), ""))) if "debit" in detected else 0.0
                credit = abs(_amount_value(row.get(detected.get("credit", ""), ""))) if "credit" in detected else 0.0
                amount = credit - debit
            account = (row.get(detected.get("account", ""), "") or "").strip() if "account" in detected else ""
            category = (row.get(detected.get("category", ""), "") or "").strip() if "category" in detected else ""
            key = f"{file_path.resolve()}|{index}|{tx_date}|{description}|{amount:.4f}"
            tx_id = hashlib.sha256(key.encode()).hexdigest()[:20]
            rows.append(Transaction(tx_id, tx_date, description, amount, account, category, source_file=str(file_path)))
    return rows


def parse_date(value: str) -> date:
    """Public helper for tests and integrations."""
    return date.fromisoformat(_date_value(value))
