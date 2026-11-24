"""Generate the multi-source corpus.

Hand-authored, not randomised. Every source below exists to produce one specific outcome and its
name says which, so a reader can check the tool's claim against the input by eye.

This script OWNS `data/`. Anything not in MANIFEST_FILES is removed, because a stale payload from an
earlier convention parses cleanly and quietly changes every count.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent

# Ten rows, sorted, so both pagination schemes have something to disagree about.
ROWS = [f"r{i:02d}" for i in range(1, 11)]

SOURCES = [
    {
        "name": "alpha",
        "fmt": "json",
        "file": "alpha.json",
        "paging": "OFFSET",
        "page_size": 3,
        "mutation": "DELETE_BEHIND",
        "declared_total": 4,
        "ask": True,
        "why": "offset paging + a deletion behind the cursor -> a row is NEVER read",
    },
    {
        "name": "beta",
        "fmt": "xml",
        "file": "beta.xml",
        "paging": "KEYSET",
        "page_size": 3,
        "mutation": "INSERT_BEHIND",
        "declared_total": None,
        "ask": True,
        "why": "keyset paging + an insertion behind the cursor -> the new row is never seen",
    },
    {
        "name": "gamma",
        "fmt": "html",
        "file": "gamma.html",
        "paging": "NONE",
        "page_size": 0,
        "mutation": "NONE",
        "declared_total": 4,
        "ask": True,
        "why": "one response, and a declared total that CONTRADICTS the rows delivered",
    },
    {
        "name": "delta",
        "fmt": "csv",
        "file": "delta.csv",
        "paging": "OFFSET",
        "page_size": 3,
        "mutation": "INSERT_BEHIND",
        "declared_total": 3,
        "ask": True,
        "why": "offset paging + an insertion behind the cursor -> a row is read TWICE",
    },
    {
        "name": "epsilon",
        "fmt": "json",
        "file": "epsilon.json",
        "paging": "NONE",
        "page_size": 0,
        "mutation": "NONE",
        "declared_total": 0,
        "ask": True,
        "why": "EMPTY: it answered, and it has nothing -- which is information",
    },
    {
        "name": "zeta",
        "fmt": "json",
        "file": "zeta.json",
        "paging": "NONE",
        "page_size": 0,
        "mutation": "NONE",
        "declared_total": None,
        "ask": True,
        "why": "FAILED: the payload will not parse, so it contributes an UNKNOWN number",
    },
    {
        "name": "eta",
        "fmt": "json",
        "file": "eta.json",
        "paging": "OFFSET",
        "page_size": 3,
        "mutation": "NONE",
        "declared_total": 10,
        "truncate_after_page": 2,
        "ask": True,
        "why": "TRUNCATED: a token expires mid-read, so its count is a floor",
    },
    {
        "name": "theta",
        "fmt": "json",
        "file": "theta.json",
        "paging": "NONE",
        "page_size": 0,
        "mutation": "NONE",
        "declared_total": None,
        "ask": False,
        "why": "NEVER_ASKED: declared but excluded, so nothing here is about it",
    },
]

MANIFEST_FILES = tuple(["manifest.json"] + [s["file"] for s in SOURCES])


# alpha: JSON with an extra field the shared model cannot hold.
ALPHA = [
    {
        "native_id": f"A-{i}",
        "shared_key": f"K-{i}",
        "field_name": "price",
        "value": "42.00" if i != 3 else "41.00",
        "seen_at": f"2025-06-01T1{i}:00",
        "confidence": 0.9,
        # Only alpha offers this. It has nowhere to go in Record.
        "warehouse_region": "eu-west",
    }
    for i in range(1, 5)
]

# beta: XML. Attributes and children both become fields. Carries no confidence at all.
BETA = """<?xml version="1.0" encoding="utf-8"?>
<records>
  <record native_id="B-1">
    <shared_key>K-1</shared_key>
    <field_name>price</field_name>
    <value>42.00</value>
    <seen_at>2025-06-01T14:00</seen_at>
  </record>
  <record native_id="B-2">
    <shared_key>K-9</shared_key>
    <field_name>price</field_name>
    <value>17.50</value>
    <seen_at>2025-06-01T15:00</seen_at>
  </record>
  <record native_id="B-3">
    <shared_key>K-3</shared_key>
    <field_name>price</field_name>
    <value>41.50</value>
    <seen_at>2025-06-01T16:00</seen_at>
    <supplier_note>discontinued</supplier_note>
  </record>
</records>
"""

# gamma: an HTML table, scraped. Declares 4 rows in its own header and delivers 3.
GAMMA = """<html><body>
<p>Total records: 4</p>
<table>
  <tr><th>native_id</th><th>shared_key</th><th>field_name</th><th>value</th><th>seen_at</th></tr>
  <tr><td>G-1</td><td>K-1</td><td>price</td><td>42.00</td><td>2025-06-01T09:00</td></tr>
  <tr><td>G-2</td><td>K-3</td><td>price</td><td>41.00</td><td>2025-06-01T10:00</td></tr>
  <tr><td>G-3</td><td>K-7</td><td>price</td><td>99.00</td><td>2025-06-01T11:00</td></tr>
</table>
</body></html>
"""

# delta: CSV. Carries confidence but no seen_at, so LATEST_WINS cannot run on its claims.
DELTA = """native_id,shared_key,field_name,value,confidence,tax_band
D-1,K-1,price,42.00,0.7,standard
D-2,K-3,price,41.00,0.4,standard
D-3,K-8,price,13.00,0.6,reduced
"""

EPSILON: list[dict] = []

# zeta: deliberately malformed. A real failure, not a simulated one.
ZETA = '[{"native_id": "Z-1", "value": "unterminated'

ETA = [
    {
        "native_id": f"E-{i}",
        "shared_key": f"K-{i}",
        "field_name": "price",
        "value": "42.00",
        "seen_at": f"2025-06-02T0{i}:00",
        "confidence": 0.5,
    }
    for i in range(1, 8)
]

THETA = [
    {"native_id": "T-1", "shared_key": "K-1", "field_name": "price", "value": "1.00"},
]


def prune(directory: Path) -> list[str]:
    """Remove any payload this script did not write."""
    removed = []
    for path in sorted(directory.iterdir()):
        suffix_matches = path.suffix in {".json", ".xml", ".html", ".csv"}
        if path.is_file() and suffix_matches and path.name not in MANIFEST_FILES:
            path.unlink()
            removed.append(path.name)
    return removed


def main() -> None:
    HERE.mkdir(parents=True, exist_ok=True)
    removed = prune(HERE)

    (HERE / "manifest.json").write_text(
        json.dumps({"rows": ROWS, "sources": SOURCES}, indent=2) + "\n"
, encoding="utf-8")
    (HERE / "alpha.json").write_text(json.dumps(ALPHA, indent=2) + "\n", encoding="utf-8")
    (HERE / "beta.xml").write_text(BETA, encoding="utf-8")
    (HERE / "gamma.html").write_text(GAMMA, encoding="utf-8")
    (HERE / "delta.csv").write_text(DELTA, encoding="utf-8")
    (HERE / "epsilon.json").write_text(json.dumps(EPSILON) + "\n", encoding="utf-8")
    (HERE / "zeta.json").write_text(ZETA, encoding="utf-8")
    (HERE / "eta.json").write_text(json.dumps(ETA, indent=2) + "\n", encoding="utf-8")
    (HERE / "theta.json").write_text(json.dumps(THETA, indent=2) + "\n", encoding="utf-8")

    print(f"wrote a manifest and {len(SOURCES)} source payloads in 4 formats")
    for s in SOURCES:
        print(f"  {s['name']:<8} {s['fmt']:<5} {s['paging']:<7} {s['why']}")
    if removed:
        print(f"pruned {len(removed)} file(s) this generator did not write: {', '.join(removed)}")

    on_disk = {p.name for p in HERE.iterdir() if p.suffix in {".json", ".xml", ".html", ".csv"}}
    assert on_disk == set(MANIFEST_FILES), f"drift: {on_disk ^ set(MANIFEST_FILES)}"


if __name__ == "__main__":
    main()
