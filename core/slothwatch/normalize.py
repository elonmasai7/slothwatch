"""Merchant normalization and stable local merchant identifiers."""
from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher

from .ingest import Transaction

_REF_PATTERNS = [
    r"\b(?:ref|reference|auth|approval|trace|txn|transaction|card)\s*[:#-]?\s*[a-z0-9-]{4,}\b",
    r"\b(?:ending|end)\s+\d{3,}\b",
    r"\b\d{6,}\b",
    r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b",
    r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b",
]


def clean_merchant(description: str) -> str:
    """Remove reference codes, card suffixes, dates and punctuation from merchant text."""
    value = (description or "").upper().strip()
    for pattern in _REF_PATTERNS:
        value = re.sub(pattern, " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:POS|PURCHASE|PAYMENT|DEBIT|CREDIT|ONLINE|CARD|VISA|MASTERCARD)\b", " ", value)
    value = re.sub(r"[^A-Z0-9 &'.-]", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" -.'")
    # Keep the most useful leading merchant phrase; bank details often trail it.
    value = re.split(r"\s{2,}|\s+\*{2,}", value)[0]
    return value.title() or "Unknown Merchant"


def normalize_transactions(rows: list[Transaction], similarity: float = 0.88) -> list[Transaction]:
    """Assign normalized merchant names and stable IDs with conservative fuzzy grouping."""
    canonical: list[str] = []
    for tx in sorted(rows, key=lambda r: (r.date, r.id)):
        candidate = clean_merchant(tx.description)
        matched = next((known for known in canonical if SequenceMatcher(None, candidate.lower(), known.lower()).ratio() >= similarity), None)
        merchant = matched or candidate
        if matched is None:
            canonical.append(candidate)
        tx.merchant = merchant
        tx.merchant_id = hashlib.sha256(merchant.casefold().encode()).hexdigest()[:16]
    return rows
