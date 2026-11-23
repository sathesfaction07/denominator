"""What `check` fails on, and what it merely reports.

One question decides the line: **can the owner of a source fix it at the source?**

Offset paging on a collection that changes, no stable sort key, a declared total that contradicts
the rows delivered, an identity key the source does not carry -- all fixable by whoever runs that
source, all gated.

A source that failed is not. Neither is an unknown denominator, nor two policies reaching different
answers. Gating on those would fail every honest run, and a gate that fails every run gets switched
off inside a week.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .aggregate import Aggregate
from .fanout import check_declared_totals, unknown_contributors
from .merge import union
from .models import Identity, Loss, Paging, SourceOutcome


@dataclass
class Gate:
    failures: list[tuple[str, str]] = field(default_factory=list)
    reported: list[tuple[str, str]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        if self.passed:
            return (
                f"PASS. Nothing fixable at source. {len(self.reported)} thing(s) are reported "
                "below and deliberately not failed on -- they are properties of aggregating from "
                "several places, not defects in any one of them."
            )
        return (
            f"FAIL on {len(self.failures)} thing(s) a source owner can fix at the source. "
            f"{len(self.reported)} further thing(s) are reported and NOT failed on."
        )


def check(agg: Aggregate) -> Gate:
    gate = Gate()

    for report in agg.reports:
        read = report.read
        if read and read.paging is Paging.OFFSET and read.loss is not Loss.CLEAN:
            gate.failures.append(
                (
                    f"{report.source}: offset paging over a collection that changed mid-read "
                    f"({read.loss.value})",
                    "A row inserted before the cursor is read twice; a row deleted before it is "
                    "never read. Fixable: page by a stable key instead of by position.",
                )
            )

    for verdict in check_declared_totals(agg.reports):
        if verdict.agrees is False:
            gate.failures.append(
                (
                    f"{verdict.source}: declared {verdict.declared} records and delivered "
                    f"{verdict.delivered}",
                    "The two numbers come from the same source and contradict each other, so one "
                    "of them is wrong at the source. Fixable there and nowhere else.",
                )
            )

    if agg.identity is not Identity.NONE:
        for report in agg.reports:
            if report.outcome not in (SourceOutcome.ANSWERED, SourceOutcome.TRUNCATED):
                continue
            rows = [r for r in agg.records if r.source == report.source]
            unkeyed = [r for r in rows if r.key(agg.identity) is None]
            if unkeyed:
                gate.failures.append(
                    (
                        f"{report.source}: {len(unkeyed)} record(s) carry no {agg.identity.value}",
                        "They cannot take part in the union, so they are kept separate -- which "
                        "looks like being unique and is only being unmatched. Fixable: emit the "
                        "shared key.",
                    )
                )

    for source, fields in agg.ceiling.dropped_by_source.items():
        gate.reported.append(
            (
                f"{source}: {len(fields)} offered field(s) have nowhere to go in the shared model",
                f"{', '.join(fields)}. The adapter succeeded and the information is gone. Not a "
                "defect in the source -- the shared model is an intersection, and this is the same "
                "ceiling DocConsolidator found in a different domain.",
            )
        )

    unknown = unknown_contributors(agg.reports)
    if unknown:
        gate.reported.append(
            (
                f"{len(unknown)} source(s) contributed an UNKNOWN number of records",
                ", ".join(f"{name} ({why})" for name, why in unknown)
                + ". The total is a floor, not a total. Nobody can fix a source being down, and "
                "nothing here can put a number on the gap.",
            )
        )

    keyset_losses = [
        r.read for r in agg.reports
        if r.read and r.read.paging is Paging.KEYSET and r.read.loss is Loss.UNSEEN_INSERT
    ]
    if keyset_losses:
        gate.reported.append(
            (
                f"{len(keyset_losses)} source(s) paged by key and still missed a row that appeared "
                "mid-read",
                "Not a defect. The row was not there when the cursor passed its position, so there "
                "was no instant at which the read could have caught it. This is what 'complete' "
                "cannot mean for a collection that changes while you read it.",
            )
        )

    sensitive = agg.policy_sensitive()
    if sensitive:
        gate.reported.append(
            (
                f"{len(sensitive)} contested entity/entities where the conflict policy decides the "
                "outcome",
                ", ".join(sorted(sensitive))
                + ". Either the policies reach different values, or only some of them can run at "
                "all. Choosing a policy is an operator's job and no source owner can fix it.",
            )
        )

    distinct = {i.value: union(agg.records, i).distinct for i in Identity}
    if len(set(distinct.values())) > 1:
        gate.reported.append(
            (
                "the count of distinct records depends on the identity scheme",
                ", ".join(f"{k}={v}" for k, v in distinct.items())
                + ". Same rows, different totals, and no scheme the data prefers. Identity is a "
                "decision, so there is nothing here for a source owner to fix.",
            )
        )

    return gate
