"""Asking several sources at once, and what the answer is worth when one does not reply.

The uplift asks for: *3 of 4 sources respond -> still produce output, note what's missing.*

Producing the output is easy. **Noting what's missing is not possible.** A source that did not
answer did not tell you how many records it was holding, so the honest statement about the gap is
"at least zero, at most unbounded". What can be named is WHICH source is silent -- never how much.

Which means the total this module produces is a numerator that looks like a total, and the whole job
here is to stop it looking like one.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import OUTCOME_NOTES, SourceOutcome, SourceReport


@dataclass(frozen=True)
class Coverage:
    """How much of the intended fan-out actually came back."""

    asked: int
    answered: int
    empty: int
    truncated: int
    failed: int
    never_asked: int
    records: int

    @property
    def sources_with_unknown_contribution(self) -> int:
        return self.truncated + self.failed + self.never_asked

    @property
    def total_is_knowable(self) -> bool:
        """Whether `records` is a total or merely a floor."""
        return self.sources_with_unknown_contribution == 0

    def headline(self) -> str:
        """The sentence that must not be able to mislead."""
        if self.total_is_knowable:
            return (
                f"{self.records} records from {self.answered + self.empty} of {self.asked} "
                "sources, all of which answered in full. This is a total, and it is a total as of "
                "the moment each source was read -- not as of any single instant."
            )
        return (
            f"{self.records} records from {self.answered} source(s) that answered in full. "
            f"{self.sources_with_unknown_contribution} source(s) contributed an UNKNOWN number, "
            f"so {self.records} is a FLOOR, not a total. Records missing: at least 0, at most "
            "unbounded -- nothing here can put a number on the gap."
        )


def coverage(reports: list[SourceReport]) -> Coverage:
    counts = dict.fromkeys(SourceOutcome, 0)
    for r in reports:
        counts[r.outcome] += 1
    return Coverage(
        asked=sum(1 for r in reports if r.outcome is not SourceOutcome.NEVER_ASKED),
        answered=counts[SourceOutcome.ANSWERED],
        empty=counts[SourceOutcome.EMPTY],
        truncated=counts[SourceOutcome.TRUNCATED],
        failed=counts[SourceOutcome.FAILED],
        never_asked=counts[SourceOutcome.NEVER_ASKED],
        records=sum(r.records for r in reports if r.count_is_meaningful),
    )


def outcome_counts(reports: list[SourceReport]) -> dict[SourceOutcome, int]:
    counts = dict.fromkeys(SourceOutcome, 0)
    for r in reports:
        counts[r.outcome] += 1
    return counts


def unknown_contributors(reports: list[SourceReport]) -> tuple[tuple[str, str], ...]:
    """The sources whose absence makes the total unknowable, and why each one does."""
    return tuple(
        (r.source, OUTCOME_NOTES[r.outcome][0]) for r in reports if r.contributes_unknown
    )


def empty_is_not_failure() -> str:
    """The distinction a fan-out most often collapses."""
    return (
        "EMPTY and FAILED both contribute zero records and mean opposite things. An empty source "
        "told you something -- it has nothing -- and its zero belongs in the total. A failed "
        "source told you nothing, and its zero is a placeholder for a number nobody knows. Folding "
        "them together is how a report claims a total it has no basis for."
    )


@dataclass(frozen=True)
class DeclaredTotalCheck:
    """A source's own count of itself, against the rows it actually handed over.

    Worth being careful about what this can establish. A declared total that MATCHES is weak
    evidence, because both numbers come from the same place. A declared total that DISAGREES is
    strong evidence -- of something, though not of which side is wrong.
    """

    source: str
    declared: int | None
    delivered: int

    @property
    def agrees(self) -> bool | None:
        if self.declared is None:
            return None
        return self.declared == self.delivered

    def verdict(self) -> str:
        if self.declared is None:
            return (
                f"{self.source} declared no total, so the {self.delivered} rows it delivered "
                "cannot be checked against anything. Not a defect -- most sources do not offer one."
            )
        if self.declared == self.delivered:
            return (
                f"{self.source} declared {self.declared} and delivered {self.delivered}. They "
                "agree, which is WEAK evidence: both numbers come from the same source, so a "
                "source that undercounts itself produces exactly this agreement."
            )
        direction = "fewer" if self.delivered < self.declared else "more"
        return (
            f"{self.source} declared {self.declared} and delivered {self.delivered} -- "
            f"{abs(self.declared - self.delivered)} {direction}. They disagree, which is STRONG "
            "evidence that something is wrong, and no evidence at all about which number to trust."
        )


def check_declared_totals(reports: list[SourceReport]) -> list[DeclaredTotalCheck]:
    return [
        DeclaredTotalCheck(source=r.source, declared=r.declared_total, delivered=r.records)
        for r in reports
        if r.count_is_meaningful
    ]

def disagreeing_totals(reports: list[SourceReport]) -> tuple[str, ...]:
    return tuple(c.source for c in check_declared_totals(reports) if c.agrees is False)
