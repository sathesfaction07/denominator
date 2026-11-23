"""Running the whole fan-out, and assembling a result that cannot overstate itself.

The order matters and is the argument: read each source (accounting for what paging lost), then
fan-in (accounting for who did not answer), then union (which needs an identity decision), then
resolve conflicts (which needs a policy decision). Each step adds an unknown, and none of them
removes one.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field

from .adapters import NormalisationCeiling, Parsed, ceiling, parse
from .config import Settings
from .fanout import Coverage, coverage, disagreeing_totals, outcome_counts, unknown_contributors
from .merge import PolicyComparison, Union, compare_policies, resolve, union
from .models import (
    ConflictPolicy,
    Identity,
    Loss,
    Mutation,
    Paging,
    ReadResult,
    Record,
    SourceOutcome,
    SourceReport,
)
from .paging import read


@dataclass
class Aggregate:
    """Everything one run established, and every unknown it accumulated."""

    reports: list[SourceReport] = field(default_factory=list)
    parsed: list[Parsed] = field(default_factory=list)
    records: list[Record] = field(default_factory=list)
    identity: Identity = Identity.SHARED_KEY
    policy: ConflictPolicy = ConflictPolicy.FLAG_FOR_REVIEW

    @property
    def coverage(self) -> Coverage:
        return coverage(self.reports)

    @property
    def union(self) -> Union:
        return union(self.records, self.identity)

    @property
    def lossy_reads(self) -> list[ReadResult]:
        return [r.read for r in self.reports if r.read and r.read.loss is not Loss.CLEAN]

    @property
    def unrepairable_reads(self) -> list[ReadResult]:
        return [r for r in self.lossy_reads if r.loss is Loss.MISSED]

    @property
    def ceiling(self) -> NormalisationCeiling:
        return ceiling(self.parsed)

    def claims_by_entity(self) -> dict[str, list[Record]]:
        """Group claims by identity, so a conflict can be asked about at all."""
        grouped: dict[str, list[Record]] = defaultdict(list)
        for r in self.records:
            key = r.key(self.identity)
            if key is not None:
                grouped[key].append(r)
        return dict(grouped)

    def contested(self) -> dict[str, list[Record]]:
        """Entities where the sources do not agree on a value."""
        return {
            key: claims
            for key, claims in self.claims_by_entity().items()
            if len({c.value for c in claims}) > 1
        }

    def policy_sensitive(self) -> dict[str, PolicyComparison]:
        """Contested entities where the choice of policy changes the outcome.

        "Changes the outcome" covers two cases, and an earlier version of this only caught the
        first: the policies reach different VALUES, or some of them cannot run at all. On claims
        where only one policy is operable the values trivially agree while the policy decides
        everything.
        """
        out = {}
        for key, claims in self.contested().items():
            comparison = compare_policies(claims)
            if comparison.policy_decides_the_outcome:
                out[key] = comparison
        return out

    def headline(self) -> str:
        """The sentence that must not be able to mislead.

        It refuses to print a record count without saying whether that count is a total or a
        floor, because the difference is the whole accuracy question.
        """
        cov = self.coverage
        u = self.union
        base = cov.headline()
        # Two different populations, and saying "those collapse to N" conflated them: the coverage
        # count excludes a TRUNCATED source (whose COUNT is only a floor) while the union includes
        # its records (which are real records we hold). Both are right; printing them adjacent
        # without saying so was not.
        detail = (
            f" Separately, {u.rows} record(s) reached the union -- more than {cov.records} because a "
            f"TRUNCATED source's records are real even though its count is only a floor -- and under "
            f"{self.identity.value} those collapse to {u.distinct} distinct record(s), where a "
            "different identity scheme gives a different number."
        )
        if self.unrepairable_reads:
            names = ", ".join(r.source for r in self.unrepairable_reads)
            detail += (
                f" And {len(self.unrepairable_reads)} source(s) ({names}) lost rows that no "
                "deduplication can recover."
            )
        return base + detail


def _outcome_for(parsed: Parsed, truncated: bool) -> SourceOutcome:
    """A parse failure never reaches here -- `run` handles that where the exception is caught."""
    if truncated:
        return SourceOutcome.TRUNCATED
    if not parsed.records:
        return SourceOutcome.EMPTY
    return SourceOutcome.ANSWERED


def run(settings: Settings) -> Aggregate:
    """Ask every source in the manifest, and account for each one separately."""
    spec = json.loads(settings.manifest_path.read_text())
    rows: list[str] = spec["rows"]
    result = Aggregate(identity=settings.identity, policy=settings.policy)

    for source in spec["sources"]:
        name = source["name"]

        if not source.get("ask", True) or name in settings.skipped:
            result.reports.append(
                SourceReport(
                    source=name,
                    outcome=SourceOutcome.NEVER_ASKED,
                    fmt=source["fmt"],
                    detail="Excluded from this run, so nothing here is about it.",
                )
            )
            continue

        payload = (settings.data_dir / source["file"]).read_text()
        try:
            parsed = parse(name, source["fmt"], payload)
        except Exception as exc:
            result.reports.append(
                SourceReport(
                    source=name,
                    outcome=SourceOutcome.FAILED,
                    fmt=source["fmt"],
                    detail=(
                        f"{type(exc).__name__} while parsing. This source contributes an UNKNOWN "
                        "number of records, not zero."
                    ),
                )
            )
            continue

        paging = Paging(source["paging"])
        mutation = Mutation(source.get("mutation", "NONE"))
        truncate_after = source.get("truncate_after_page")

        read_result = None
        if paging is not Paging.NONE:
            read_result = read(
                name, rows, paging, source.get("page_size") or settings.page_size, mutation
            )

        outcome = _outcome_for(parsed, truncated=truncate_after is not None)
        report = SourceReport(
            source=name,
            outcome=outcome,
            fmt=source["fmt"],
            records=len(parsed.records) if outcome is not SourceOutcome.FAILED else 0,
            declared_total=source.get("declared_total"),
            read=read_result,
            detail=(
                "Stopped part-way, so this count is a floor."
                if outcome is SourceOutcome.TRUNCATED
                else parsed.summary()
            ),
        )
        result.reports.append(report)
        result.parsed.append(parsed)
        if outcome is not SourceOutcome.FAILED:
            result.records.extend(parsed.records)

    return result


def payload(agg: Aggregate) -> dict:
    """A machine-readable summary, for CI to assert on."""
    cov = agg.coverage
    resolved = {
        key: resolve(claims, agg.policy).value for key, claims in agg.contested().items()
    }
    return {
        "sources": len(agg.reports),
        "outcomes": {k.value: v for k, v in outcome_counts(agg.reports).items()},
        "records": cov.records,
        "union_rows": agg.union.rows,
        "total_is_knowable": cov.total_is_knowable,
        "unknown_contributors": [name for name, _ in unknown_contributors(agg.reports)],
        "disagreeing_declared_totals": list(disagreeing_totals(agg.reports)),
        "identity": agg.identity.value,
        "distinct_under_identity": agg.union.distinct,
        "distinct_by_identity": {
            i.value: union(agg.records, i).distinct for i in Identity
        },
        "lossy_reads": {r.source: r.loss.value for r in agg.lossy_reads},
        "unrepairable_reads": [r.source for r in agg.unrepairable_reads],
        "dedup_repairable": [r.source for r in agg.lossy_reads if r.dedup_repairs_it],
        "dropped_fields": list(agg.ceiling.all_dropped),
        "dropped_by_source": {k: list(v) for k, v in agg.ceiling.dropped_by_source.items()},
        "contested_entities": sorted(agg.contested()),
        "policy": agg.policy.value,
        "policy_sensitive": sorted(agg.policy_sensitive()),
        "resolved": dict(sorted(resolved.items())),
        "formats": sorted({r.fmt for r in agg.reports}),
    }
