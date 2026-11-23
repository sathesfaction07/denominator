"""The vocabulary.

The brief asks for "complete data aggregation from all sources without missing records" and calls
this a software engineering problem. The enums here are the reason it is not: each one names a way
the word *complete* fails to have a value, and none of them is fixed by better plumbing.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class Paging(StrEnum):
    """How a source hands over more than one page."""

    NONE = "NONE"
    OFFSET = "OFFSET"
    KEYSET = "KEYSET"


PAGING_NOTES: dict[Paging, tuple[str, str]] = {
    Paging.NONE: (
        "everything in one response",
        "Complete with respect to the instant of that one response, which is the best any "
        "scheme manages.",
    ),
    Paging.OFFSET: (
        "skip N, take M",
        "A row inserted before the cursor is read TWICE; a row deleted before it is NEVER read. "
        "Both pages are individually well-formed.",
    ),
    Paging.KEYSET: (
        "everything after the last key seen",
        "Never shifts, never duplicates -- and a row inserted BEHIND the cursor is never seen, "
        "because the cursor has already passed where it sorts.",
    ),
}


class Mutation(StrEnum):
    """What the collection did while we were reading it."""

    NONE = "NONE"
    INSERT_BEHIND = "INSERT_BEHIND"
    INSERT_AHEAD = "INSERT_AHEAD"
    DELETE_BEHIND = "DELETE_BEHIND"


class Loss(StrEnum):
    """What a read lost, if anything.

    DUPLICATED and MISSED are not degrees of the same thing. One is repairable by deduplication
    and the other is not, which is why they are separate members.
    """

    CLEAN = "CLEAN"
    DUPLICATED = "DUPLICATED"
    MISSED = "MISSED"
    UNSEEN_INSERT = "UNSEEN_INSERT"


LOSS_NOTES: dict[Loss, str] = {
    Loss.CLEAN: "every row that existed throughout the read was seen exactly once",
    Loss.DUPLICATED: (
        "a row was read more than once. Deduplication repairs this one, which is why it is the "
        "less dangerous of the two"
    ),
    Loss.MISSED: (
        "a row that existed for the whole read was never seen. Deduplication cannot invent it, "
        "and the count afterwards looks perfectly plausible"
    ),
    Loss.UNSEEN_INSERT: (
        "a row that appeared mid-read was never seen. Not a defect in the reader -- the row was "
        "not there when the reader passed its position"
    ),
}


class SourceOutcome(StrEnum):
    """What happened when we asked a source.

    FAILED and EMPTY are different, and folding them together is how a report claims a total it
    has no basis for.
    """

    ANSWERED = "ANSWERED"
    EMPTY = "EMPTY"
    TRUNCATED = "TRUNCATED"
    FAILED = "FAILED"
    NEVER_ASKED = "NEVER_ASKED"


OUTCOME_NOTES: dict[SourceOutcome, tuple[str, bool]] = {
    # (what it means, whether a record count from this source means anything)
    SourceOutcome.ANSWERED: ("the source responded and paging ran to the end", True),
    SourceOutcome.EMPTY: ("the source responded, and had nothing -- which IS information", True),
    SourceOutcome.TRUNCATED: (
        "the source responded and then stopped part-way, so its count is a floor",
        False,
    ),
    SourceOutcome.FAILED: (
        "no usable response, so this source contributes an UNKNOWN number of records",
        False,
    ),
    SourceOutcome.NEVER_ASKED: ("we did not ask, so nothing here is about it", False),
}


class ConflictPolicy(StrEnum):
    """How to pick when two sources claim different values for one field."""

    LATEST_WINS = "LATEST_WINS"
    HIGHEST_CONFIDENCE = "HIGHEST_CONFIDENCE"
    MAJORITY = "MAJORITY"
    FLAG_FOR_REVIEW = "FLAG_FOR_REVIEW"


POLICY_NOTES: dict[ConflictPolicy, str] = {
    ConflictPolicy.LATEST_WINS: (
        "needs a comparable timestamp on every claim, and prefers recency over correctness"
    ),
    ConflictPolicy.HIGHEST_CONFIDENCE: (
        "needs a confidence per claim, and those numbers come from the sources themselves"
    ),
    ConflictPolicy.MAJORITY: (
        "needs three or more sources, and counts sources rather than weighing them -- two copies "
        "of one upstream outvote one independent observation"
    ),
    ConflictPolicy.FLAG_FOR_REVIEW: (
        "answers nothing and is the only one that does not invent a winner"
    ),
}


class Identity(StrEnum):
    """What makes two records from different sources the same record."""

    NATIVE_ID = "NATIVE_ID"
    SHARED_KEY = "SHARED_KEY"
    NONE = "NONE"


class Record(BaseModel):
    """One source's claim about one entity."""

    model_config = {"frozen": True}

    source: str = Field(min_length=1)
    native_id: str = Field(min_length=1)
    shared_key: str | None = None
    field_name: str = Field(min_length=1)
    value: str
    seen_at: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    def key(self, identity: Identity) -> str | None:
        """The identity this record has under a given scheme.

        `None` means this record cannot participate in a union under that scheme -- which is a
        different thing from being unique.
        """
        if identity is Identity.NATIVE_ID:
            return f"{self.source}:{self.native_id}"
        if identity is Identity.SHARED_KEY:
            return self.shared_key
        return None


class ReadResult(BaseModel):
    """What one paginated read of one source actually produced."""

    model_config = {"frozen": True}

    source: str = Field(min_length=1)
    paging: Paging
    mutation: Mutation = Mutation.NONE
    rows_read: int = Field(ge=0)
    distinct_read: int = Field(ge=0)
    stable_rows: int = Field(ge=0)
    missed: tuple[str, ...] = ()
    duplicated: tuple[str, ...] = ()
    unseen_inserts: tuple[str, ...] = ()
    pages: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _counts_are_consistent(self) -> ReadResult:
        if self.distinct_read > self.rows_read:
            raise ValueError("cannot read more distinct rows than rows")
        if self.duplicated and self.distinct_read == self.rows_read:
            raise ValueError("a duplicate means distinct_read must be below rows_read")
        return self

    @property
    def loss(self) -> Loss:
        """The worst thing that happened, because the worst thing is what matters.

        MISSED outranks DUPLICATED deliberately: one is repairable and the other is not.
        """
        if self.missed:
            return Loss.MISSED
        if self.duplicated:
            return Loss.DUPLICATED
        if self.unseen_inserts:
            return Loss.UNSEEN_INSERT
        return Loss.CLEAN

    @property
    def dedup_repairs_it(self) -> bool:
        """Whether taking the distinct set recovers the right answer."""
        return not self.missed and bool(self.duplicated)


class SourceReport(BaseModel):
    """One source's contribution, and how much of it can be trusted as a count."""

    model_config = {"frozen": True}

    source: str = Field(min_length=1)
    outcome: SourceOutcome
    fmt: str = "json"
    records: int = Field(default=0, ge=0)
    declared_total: int | None = None
    read: ReadResult | None = None
    detail: str = ""

    @model_validator(mode="after")
    def _silent_sources_contribute_nothing(self) -> SourceReport:
        if self.outcome in (SourceOutcome.FAILED, SourceOutcome.NEVER_ASKED) and self.records:
            raise ValueError(f"{self.outcome} cannot carry a record count")
        return self

    @property
    def count_is_meaningful(self) -> bool:
        return OUTCOME_NOTES[self.outcome][1]

    @property
    def contributes_unknown(self) -> bool:
        """Whether this source makes the overall total unknowable rather than merely smaller."""
        return self.outcome in (
            SourceOutcome.FAILED,
            SourceOutcome.TRUNCATED,
            SourceOutcome.NEVER_ASKED,
        )
