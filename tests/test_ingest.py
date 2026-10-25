import csv
from pathlib import Path

import pytest

from slothwatch.ingest import read_csv
from slothwatch.normalize import clean_merchant, normalize_transactions


def test_csv_amount_quirks_and_debit_credit(tmp_path: Path):
    path = tmp_path / "bank.csv"
    path.write_text('Transaction Date,Details,Debit,Credit\n31/01/2026,SHOP POS 123456,"($1,234.50)",\n2026-02-01,REF 123456 Utility,0,$55.20\n', encoding="utf-8")
    rows = read_csv(path)
    assert rows[0].amount == -1234.5  # debit is outflow; credit minus debit
    assert rows[1].amount == 55.2
    assert rows[0].date == "2026-01-31"


def test_parenthetical_negative_amount(tmp_path: Path):
    path = tmp_path / "bank.csv"
    path.write_text("Date,Description,Amount\n2026-01-01,Refund,(12.50)\n", encoding="utf-8")
    assert read_csv(path)[0].amount == -12.5


def test_missing_mapping_gives_clear_error(tmp_path: Path):
    path = tmp_path / "bad.csv"
    path.write_text("X,Y\na,b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Could not detect date"):
        read_csv(path)


def test_merchant_cleaning_and_stable_id(tmp_path: Path):
    path = tmp_path / "bank.csv"
    path.write_text("Date,Description,Amount\n2026-01-01,NETFLIX CARD 123456 REF ABC123,15.00\n", encoding="utf-8")
    rows = normalize_transactions(read_csv(path))
    assert "123456" not in clean_merchant(rows[0].description)
    assert rows[0].merchant_id


def test_explicit_column_mapping_is_used(tmp_path):
    path = tmp_path / "bank.csv"
    path.write_text('Booked On,Statement Narrative,Debit GBP\n2026-01-05,Internet Plan,"(12.99)"\n', encoding="utf-8")
    rows = read_csv(path, mapping={"date": "Booked On", "description": "Statement Narrative", "debit": "Debit GBP"})
    assert len(rows) == 1
    assert rows[0].date == "2026-01-05"
    assert rows[0].amount == -12.99
