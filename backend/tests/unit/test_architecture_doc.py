"""Tested documentation (A§26.1, DOC-1), slice-1 scope: internal references resolve and every
invariant and hazard has ledger rows. Module-path checks are added as modules come into existence."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ARCHITECTURE = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
LEDGER = (ROOT / "VERIFICATION.md").read_text(encoding="utf-8")


def test_every_section_reference_resolves() -> None:
    headings = set(re.findall(r"^#{2,4} (\d+(?:\.\d+)*)\.? ", ARCHITECTURE, re.M))
    for text in (ARCHITECTURE, LEDGER):
        missing = sorted(set(re.findall(r"A§(\d+(?:\.\d+)*)", text)) - headings)
        assert missing == []


def test_every_invariant_and_hazard_has_a_ledger_row() -> None:
    rows = set(re.findall(r"^\| ((?:INV|HZ)-\d+)[ .|]", LEDGER, re.M))
    expected = {f"INV-{n}" for n in range(1, 15)} | {f"HZ-{n}" for n in range(1, 33)}
    assert expected - rows == set()
