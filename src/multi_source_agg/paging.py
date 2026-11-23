"""Reading a collection that changes while you read it.

This is the module the project exists for. The brief lists "one is paginated, one isn't" as an
input problem and calls the whole thing a software engineering concern. But pagination over a
mutable collection is not a plumbing detail -- it decides whether the word *complete* has a value
at all, and the two schemes people actually use fail in opposite directions:

    OFFSET  a row inserted before the cursor is read TWICE
            a row deleted before the cursor is NEVER read
    KEYSET  never shifts, never duplicates
            a row inserted BEHIND the cursor is never seen

Every page is individually well-formed in all of those cases. Nothing errors. Nothing logs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .models import Loss, Mutation, Paging, ReadResult

Mutator = Callable[[list[str]], None]


def apply_mutation(rows: list[str], mutation: Mutation, cursor_row: str = "r00") -> None:
    """Change the collection in place, the way a live system would."""
    if mutation is Mutation.INSERT_BEHIND:
        rows.insert(0, cursor_row)
    elif mutation is Mutation.INSERT_AHEAD:
        rows.append("r99")
    elif mutation is Mutation.DELETE_BEHIND and rows:
        rows.pop(0)


def offset_read(
    rows: list[str], size: int, mutation: Mutation = Mutation.NONE, mutate_after: int = 1
) -> tuple[list[str], list[str]]:
    """Skip N, take M -- the pagination almost every API offers.

    Returns (what the reader saw, the collection as it ended up). The reader is written the
    obvious way, because the obvious way is the point.
    """
    seen: list[str] = []
    live = list(rows)
    offset = page_no = 0
    while True:
        page = live[offset : offset + size]
        if not page:
            break
        page_no += 1
        seen.extend(page)
        offset += size
        if page_no == mutate_after:
            apply_mutation(live, mutation)
    return seen, live


def keyset_read(
    rows: list[str], size: int, mutation: Mutation = Mutation.NONE, mutate_after: int = 1
) -> tuple[list[str], list[str]]:
    """Everything after the last key seen -- the scheme that is supposed to fix offset paging.

    It does fix the shifting. It does not make the read complete.
    """
    seen: list[str] = []
    live = list(rows)
    after = ""
    page_no = 0
    while True:
        page = [r for r in sorted(live) if r > after][:size]
        if not page:
            break
        page_no += 1
        seen.extend(page)
        after = page[-1]
        if page_no == mutate_after:
            apply_mutation(live, mutation)
    return seen, live


READERS: dict[Paging, Callable[..., tuple[list[str], list[str]]]] = {
    Paging.OFFSET: offset_read,
    Paging.KEYSET: keyset_read,
}


def single_read(
    rows: list[str], size: int, mutation: Mutation = Mutation.NONE, mutate_after: int = 1
) -> tuple[list[str], list[str]]:
    """No paging at all: one response, one instant.

    Included because it is the only scheme that is unambiguously complete -- with respect to the
    moment of that one response, which is the most any scheme achieves.
    """
    live = list(rows)
    return list(live), live


READERS[Paging.NONE] = single_read


def read(
    source: str,
    rows: list[str],
    paging: Paging,
    size: int = 3,
    mutation: Mutation = Mutation.NONE,
    mutate_after: int = 1,
) -> ReadResult:
    """Run a read and account for exactly what it lost.

    The accounting distinguishes three populations, which is the whole reason this is not a
    plumbing problem:

    - **stable rows** -- present before the read AND after it. These are the rows a complete read
      is answerable for.
    - **missed** -- stable rows the reader never saw. Unrepairable.
    - **unseen inserts** -- rows that appeared mid-read and were never seen. Not the reader's
      fault; there was no moment at which they were both present and ahead of the cursor.
    """
    before = list(rows)
    reader = READERS[paging]
    seen, after = reader(before, size, mutation, mutate_after)

    stable = [r for r in before if r in after]
    appeared = [r for r in after if r not in before]

    missed = tuple(r for r in stable if r not in seen)
    duplicated = tuple(sorted({r for r in seen if seen.count(r) > 1}))
    unseen_inserts = tuple(r for r in appeared if r not in seen)

    pages = 1 if paging is Paging.NONE else -(-len(seen) // size) if size else 0

    return ReadResult(
        source=source,
        paging=paging,
        mutation=mutation,
        rows_read=len(seen),
        distinct_read=len(set(seen)),
        stable_rows=len(stable),
        missed=missed,
        duplicated=duplicated,
        unseen_inserts=unseen_inserts,
        pages=pages,
    )


@dataclass(frozen=True)
class SchemeComparison:
    """The same collection and the same mutation, read both ways."""

    mutation: Mutation
    offset: ReadResult
    keyset: ReadResult

    @property
    def schemes_agree(self) -> bool:
        return self.offset.loss is self.keyset.loss

    def summary(self) -> str:
        if self.schemes_agree:
            return (
                f"Under {self.mutation} both schemes reach {self.offset.loss.value}. Agreement "
                "here is a property of this mutation, not of the schemes."
            )
        return (
            f"Under {self.mutation} offset paging reaches {self.offset.loss.value} and keyset "
            f"paging reaches {self.keyset.loss.value}. The scheme is not an implementation "
            "detail; it decides which records exist as far as the reader is concerned."
        )


def compare_schemes(
    rows: list[str], mutation: Mutation, size: int = 3
) -> SchemeComparison:
    return SchemeComparison(
        mutation=mutation,
        offset=read("offset", rows, Paging.OFFSET, size, mutation),
        keyset=read("keyset", rows, Paging.KEYSET, size, mutation),
    )


def dedup_verdict(result: ReadResult) -> str:
    """What deduplicating this read's output would and would not achieve."""
    if result.loss is Loss.DUPLICATED:
        return (
            f"{len(result.duplicated)} row(s) were read twice, so the raw count "
            f"({result.rows_read}) overstates. Taking the distinct set recovers "
            f"{result.distinct_read}, which IS the right answer here -- deduplication repairs "
            "this failure completely."
        )
    if result.loss is Loss.MISSED:
        return (
            f"{len(result.missed)} row(s) that existed throughout the read were never seen. "
            f"Deduplication cannot invent them, so the distinct count ({result.distinct_read}) is "
            "simply wrong -- and it looks exactly as plausible as a correct one."
        )
    if result.loss is Loss.UNSEEN_INSERT:
        return (
            f"{len(result.unseen_inserts)} row(s) appeared mid-read behind the cursor and were "
            "never seen. Nothing repairs this, and nothing should: there was no instant at which "
            "the read could have caught them."
        )
    return (
        "Nothing was lost, so deduplication would change nothing. That is a fact about this "
        "read, not a guarantee about the scheme."
    )


def why_retry_makes_it_worse(offset: int) -> str:
    """The brief prescribes retry. Retry is a second read at a stale cursor."""
    return (
        f"A token expiring mid-read forces a re-authentication and a resume at offset {offset}. "
        "That offset was computed against the collection as it was BEFORE the interruption. If "
        "anything was deleted while the retry was in flight, the resumed offset now points past a "
        "row nobody read. The retry is not a repair; it is a second chance to be wrong at a "
        "cursor that has gone stale."
    )


def completeness_has_no_denominator() -> str:
    return (
        "A read can count what it received. It cannot count what exists. Asking the source for a "
        "total gets another claim from the thing being audited -- and even an honest total is only "
        "true for an instant, while a paginated read occupies a stretch of time. So there is no "
        "scheme under which 'complete' has a value for a collection that changes while you read "
        "it. Keyset paging is complete with respect to a snapshot that never existed."
    )
