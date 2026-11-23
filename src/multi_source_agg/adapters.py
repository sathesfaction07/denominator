"""Per-source adapters: JSON, XML, HTML and CSV into one model.

The brief's recommendation, built. XML parsing is genuinely absent from the rest of this portfolio
(0 files), so this is new ground here -- but the *finding* about normalization is not new, and it is
worth saying so plainly rather than re-pitching it:

**Shoehorn already established that a common target model sets a ceiling the parser cannot
exceed.** The same thing is true here. `Record` is the intersection of what four formats can say, so
a field only one source carries has nowhere to go. What this module adds is the count: which fields
each source offered, and which of them the shared model dropped.

The adapters themselves are ordinary. That is the point -- the loss is not a bug in any of them.
"""

from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .models import Record

MODEL_FIELDS = frozenset(
    {"source", "native_id", "shared_key", "field_name", "value", "seen_at", "confidence"}
)


@dataclass(frozen=True)
class Parsed:
    """What one adapter got out of one payload, and what it had to leave behind."""

    source: str
    fmt: str
    records: tuple[Record, ...]
    offered_fields: tuple[str, ...]
    dropped_fields: tuple[str, ...]

    @property
    def lossless(self) -> bool:
        return not self.dropped_fields

    def summary(self) -> str:
        base = f"{self.source} ({self.fmt}): {len(self.records)} record(s)"
        if self.lossless:
            return (
                f"{base}, and every field it offered fits the shared model. Nothing was lost "
                "here, which is a fact about this payload rather than about the format."
            )
        return (
            f"{base}, and {len(self.dropped_fields)} field(s) it offered have nowhere to go in "
            f"the shared model: {', '.join(self.dropped_fields)}. The adapter succeeded and the "
            "information is gone."
        )


def _drops(offered: set[str]) -> tuple[str, ...]:
    return tuple(sorted(offered - MODEL_FIELDS))


def _record(source: str, raw: dict) -> Record:
    """Build a Record from whatever the adapter managed to name.

    Confidence is only set when the source actually supplied one. A missing confidence stays
    missing rather than becoming 0.0, because 0.0 is a claim of no confidence and absence is not.
    """
    confidence = raw.get("confidence")
    return Record(
        source=source,
        native_id=str(raw["native_id"]),
        shared_key=raw.get("shared_key") or None,
        field_name=str(raw.get("field_name", "value")),
        value=str(raw.get("value", "")),
        seen_at=raw.get("seen_at") or None,
        confidence=float(confidence) if confidence not in (None, "") else None,
    )


def from_json(source: str, payload: str) -> Parsed:
    """The easy one. A dict per record, and any extra keys are visible."""
    rows = json.loads(payload)
    offered: set[str] = set()
    records = []
    for row in rows:
        offered |= set(row)
        records.append(_record(source, row))
    return Parsed(source, "json", tuple(records), tuple(sorted(offered)), _drops(offered))


def from_xml(source: str, payload: str) -> Parsed:
    """Child elements become fields.

    Note what an XML adapter has to decide that a JSON one does not: attributes and child
    elements are both "fields" to the shared model, and text with tail whitespace is not the same
    string as the text a JSON source would have sent.
    """
    root = ET.fromstring(payload)
    offered: set[str] = set()
    records = []
    for node in root.findall("record"):
        row: dict[str, str] = dict(node.attrib)
        for child in node:
            row[child.tag] = (child.text or "").strip()
        offered |= set(row)
        records.append(_record(source, row))
    return Parsed(source, "xml", tuple(records), tuple(sorted(offered)), _drops(offered))


ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")


def from_html(source: str, payload: str) -> Parsed:
    """A table, scraped.

    Regex rather than a parser, deliberately: this is what a scraper against an HTML source
    actually looks like, and the fragility belongs in the open. The header row names the fields,
    so a source that renames a column silently produces records with a field nobody asked for.
    """
    rows = ROW_RE.findall(payload)
    if not rows:
        return Parsed(source, "html", (), (), ())
    header = [TAG_RE.sub("", c).strip() for c in CELL_RE.findall(rows[0])]
    offered = set(header)
    records = []
    for raw_row in rows[1:]:
        cells = [TAG_RE.sub("", c).strip() for c in CELL_RE.findall(raw_row)]
        if len(cells) != len(header):
            continue
        records.append(_record(source, dict(zip(header, cells, strict=True))))
    return Parsed(source, "html", tuple(records), tuple(sorted(offered)), _drops(offered))


def from_csv(source: str, payload: str) -> Parsed:
    reader = csv.DictReader(io.StringIO(payload))
    offered = set(reader.fieldnames or [])
    records = tuple(_record(source, row) for row in reader)
    return Parsed(source, "csv", records, tuple(sorted(offered)), _drops(offered))


ADAPTERS = {
    "json": from_json,
    "xml": from_xml,
    "html": from_html,
    "csv": from_csv,
}


def parse(source: str, fmt: str, payload: str) -> Parsed:
    if fmt not in ADAPTERS:
        raise ValueError(f"no adapter for format: {fmt}")
    return ADAPTERS[fmt](source, payload)


@dataclass(frozen=True)
class NormalisationCeiling:
    """What the shared model could not hold, across every source."""

    parsed: tuple[Parsed, ...]

    @property
    def dropped_by_source(self) -> dict[str, tuple[str, ...]]:
        return {p.source: p.dropped_fields for p in self.parsed if p.dropped_fields}

    @property
    def all_dropped(self) -> tuple[str, ...]:
        seen: set[str] = set()
        for p in self.parsed:
            seen |= set(p.dropped_fields)
        return tuple(sorted(seen))

    @property
    def fields_only_one_source_offers(self) -> tuple[str, ...]:
        counts: dict[str, int] = {}
        for p in self.parsed:
            for field in p.offered_fields:
                counts[field] = counts.get(field, 0) + 1
        return tuple(sorted(f for f, n in counts.items() if n == 1))

    def summary(self) -> str:
        if not self.all_dropped:
            return (
                "Every field every source offered fits the shared model. No ceiling was reached "
                "on this input."
            )
        return (
            f"{len(self.all_dropped)} field(s) across {len(self.dropped_by_source)} source(s) have "
            f"nowhere to go in the shared model: {', '.join(self.all_dropped)}. Every adapter "
            "reported success. This is the same ceiling Shoehorn found in a different "
            "domain: the target model decides what can be carried, and the parser cannot exceed it."
        )


def ceiling(parsed: list[Parsed]) -> NormalisationCeiling:
    return NormalisationCeiling(parsed=tuple(parsed))
