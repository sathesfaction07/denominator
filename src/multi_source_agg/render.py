"""Terminal output.

Excluded from coverage: it prints. One rule throughout -- a record count never appears without the
words that say whether it is a total or a floor.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .adapters import NormalisationCeiling, Parsed
from .aggregate import Aggregate
from .config import provenance, what_is_never_measured
from .fanout import check_declared_totals, empty_is_not_failure, unknown_contributors
from .gate import Gate
from .merge import IdentityComparison, PolicyComparison, policy_requirements
from .models import (
    LOSS_NOTES,
    OUTCOME_NOTES,
    PAGING_NOTES,
    ConflictPolicy,
    Identity,
    Loss,
    Mutation,
    Paging,
    ReadResult,
    SourceOutcome,
)
from .paging import completeness_has_no_denominator, dedup_verdict, why_retry_makes_it_worse

console = Console()

LOSS_COLOUR = {
    Loss.CLEAN: "green",
    Loss.DUPLICATED: "yellow",
    Loss.MISSED: "red",
    Loss.UNSEEN_INSERT: "cyan",
}

OUTCOME_COLOUR = {
    SourceOutcome.ANSWERED: "green",
    SourceOutcome.EMPTY: "cyan",
    SourceOutcome.TRUNCATED: "yellow",
    SourceOutcome.FAILED: "red",
    SourceOutcome.NEVER_ASKED: "dim",
}


def show_paging(matrix: list[tuple[Paging, Mutation, ReadResult]]) -> None:
    console.print()
    console.print(
        Panel(
            "The brief calls this a software engineering problem. It is not, and this table is\n"
            "why: the same collection, the same mutation, read two ways, loses different records.",
            title="reading something that changes while you read it",
            border_style="blue",
        )
    )
    console.print()
    table = Table(show_header=True, header_style="bold blue")
    table.add_column("paging")
    table.add_column("what changed mid-read")
    table.add_column("read", justify="right")
    table.add_column("distinct", justify="right")
    table.add_column("outcome")
    table.add_column("which rows")
    for paging, mutation, result in matrix:
        detail = []
        if result.missed:
            detail.append(f"missed {', '.join(result.missed)}")
        if result.duplicated:
            detail.append(f"duped {', '.join(result.duplicated)}")
        if result.unseen_inserts:
            detail.append(f"never saw {', '.join(result.unseen_inserts)}")
        table.add_row(
            paging.value,
            mutation.value,
            str(result.rows_read),
            str(result.distinct_read),
            f"[{LOSS_COLOUR[result.loss]}]{result.loss.value}[/]",
            ", ".join(detail) or "-",
        )
    console.print(table)
    console.print()
    for paging in Paging:
        what, note = PAGING_NOTES[paging]
        console.print(f"[bold]{paging.value}[/bold] ({what}): {note}")
    console.print()
    console.print("[bold]What deduplication can and cannot repair[/bold]")
    for _, _, result in matrix:
        if result.loss is not Loss.CLEAN:
            console.print(f"  [dim]{result.source}:[/dim] {dedup_verdict(result)}")
    console.print()
    console.print(f"[yellow]{completeness_has_no_denominator()}[/yellow]")
    console.print()
    console.print(f"[dim]{why_retry_makes_it_worse(9)}[/dim]")
    console.print()


def show_losses() -> None:
    table = Table(title="four things a read can do", show_header=True, header_style="bold blue")
    table.add_column("outcome")
    table.add_column("what it means")
    for loss in Loss:
        table.add_row(f"[{LOSS_COLOUR[loss]}]{loss.value}[/]", LOSS_NOTES[loss])
    console.print(table)
    console.print()


def show_sources(agg: Aggregate) -> None:
    console.print()
    table = Table(title="every source, and what its count is worth", show_header=True,
                  header_style="bold blue")
    table.add_column("source")
    table.add_column("fmt")
    table.add_column("outcome")
    table.add_column("records", justify="right")
    table.add_column("declared", justify="right")
    table.add_column("count means something")
    for r in agg.reports:
        table.add_row(
            r.source,
            r.fmt,
            f"[{OUTCOME_COLOUR[r.outcome]}]{r.outcome.value}[/]",
            str(r.records) if r.count_is_meaningful else "?",
            "-" if r.declared_total is None else str(r.declared_total),
            "yes" if r.count_is_meaningful else "[red]no[/red]",
        )
    console.print(table)
    console.print()
    for outcome in SourceOutcome:
        what, meaningful = OUTCOME_NOTES[outcome]
        mark = "counts" if meaningful else "does NOT count"
        console.print(f"[dim]{outcome.value}:[/dim] {what} -- {mark}")
    console.print()
    console.print(f"[yellow]{empty_is_not_failure()}[/yellow]")
    console.print()


def show_fanout(agg: Aggregate) -> None:
    console.print()
    console.print(Panel(agg.coverage.headline(), title="what came back", border_style="blue"))
    console.print()
    unknown = unknown_contributors(agg.reports)
    if unknown:
        table = Table(title="sources that make the total unknowable", show_header=True,
                      header_style="bold blue")
        table.add_column("source")
        table.add_column("why")
        for name, why in unknown:
            table.add_row(name, why)
        console.print(table)
        console.print()
        console.print(
            "[bold yellow]The uplift asks the tool to 'note what's missing'. It can name WHICH "
            "source is missing and never HOW MUCH -- records missed: at least 0, at most "
            "unbounded.[/bold yellow]"
        )
        console.print()
    table = Table(title="a source's own count of itself", show_header=True,
                  header_style="bold blue")
    table.add_column("source")
    table.add_column("verdict")
    for verdict in check_declared_totals(agg.reports):
        table.add_row(verdict.source, verdict.verdict())
    console.print(table)
    console.print()


def show_identity(comparison: IdentityComparison) -> None:
    console.print()
    table = Table(title="how many records did we get", show_header=True, header_style="bold blue")
    table.add_column("identity scheme")
    table.add_column("rows", justify="right")
    table.add_column("distinct", justify="right")
    table.add_column("what happened")
    for u in comparison.unions:
        table.add_row(u.identity.value, str(u.rows), str(u.distinct), u.summary())
    console.print(table)
    console.print()
    console.print(f"[bold yellow]{comparison.summary()}[/bold yellow]")
    console.print()


def show_policies(comparisons: dict[str, PolicyComparison]) -> None:
    console.print()
    table = Table(title="what each policy needs before it can run", show_header=True,
                  header_style="bold blue")
    table.add_column("policy")
    table.add_column("requires")
    for policy, note in policy_requirements():
        table.add_row(policy.value, note)
    console.print(table)
    console.print()
    if not comparisons:
        console.print("[dim]No contested entity in this run, so no policy had to decide.[/dim]")
        console.print()
        return
    for key, comparison in sorted(comparisons.items()):
        console.print(f"[bold]{key}[/bold]")
        inner = Table(show_header=True, header_style="bold blue")
        inner.add_column("policy")
        inner.add_column("answer")
        inner.add_column("why")
        for r in comparison.resolutions:
            answer = "[red]could not run[/red]" if not r.usable else (
                r.value if r.value is not None else "[dim]declines to pick[/dim]"
            )
            inner.add_row(r.policy.value, answer, r.detail)
        console.print(inner)
        console.print(f"  [yellow]{comparison.summary()}[/yellow]")
        console.print()


def show_normalisation(parsed: list[Parsed], ceiling: NormalisationCeiling) -> None:
    console.print()
    table = Table(title="what each adapter offered, and what the shared model kept",
                  show_header=True, header_style="bold blue")
    table.add_column("source")
    table.add_column("fmt")
    table.add_column("records", justify="right")
    table.add_column("dropped")
    for p in parsed:
        table.add_row(
            p.source, p.fmt, str(len(p.records)),
            ", ".join(p.dropped_fields) or "-",
        )
    console.print(table)
    console.print()
    console.print(f"[yellow]{ceiling.summary()}[/yellow]")
    console.print()
    only_one = ceiling.fields_only_one_source_offers
    if only_one:
        console.print(
            f"[dim]{len(only_one)} field(s) are offered by exactly one source "
            f"({', '.join(only_one)}). A shared model is an intersection, so those are the first "
            "things it cannot hold.[/dim]"
        )
        console.print()


def show_report(agg: Aggregate) -> None:
    console.print()
    console.print(Panel(agg.headline(), title="what this run found", border_style="blue"))
    console.print()
    table = Table(show_header=True, header_style="bold blue")
    table.add_column("source")
    table.add_column("outcome")
    table.add_column("records", justify="right")
    table.add_column("read")
    for r in agg.reports:
        table.add_row(
            r.source,
            f"[{OUTCOME_COLOUR[r.outcome]}]{r.outcome.value}[/]",
            str(r.records) if r.count_is_meaningful else "?",
            "-" if r.read is None else f"[{LOSS_COLOUR[r.read.loss]}]{r.read.loss.value}[/]",
        )
    console.print(table)
    console.print()
    if agg.unrepairable_reads:
        console.print(
            f"[bold red]{len(agg.unrepairable_reads)} source(s) lost rows that no deduplication "
            "can recover, and the count afterwards looks perfectly plausible.[/bold red]"
        )
        console.print()
    sensitive = agg.policy_sensitive()
    if sensitive:
        console.print(
            f"[yellow]{len(sensitive)} contested entity/entities where the conflict policy decides "
            f"the outcome: {', '.join(sorted(sensitive))}.[/yellow]"
        )
        console.print()


def show_doctor(agg: Aggregate) -> None:
    console.print()
    console.print(
        Panel(
            "Everything below is either measured from the payloads or chosen by somebody.\n"
            "This screen says which, because the two look identical in a report.",
            title="what this tool knows, and how",
            border_style="blue",
        )
    )
    console.print()
    measured = Table(title="measured from the payloads", show_header=True,
                     header_style="bold blue")
    measured.add_column("fact")
    measured.add_column("value")
    cov = agg.coverage
    measured.add_row("sources in the manifest", str(len(agg.reports)))
    measured.add_row("answered in full", str(cov.answered))
    measured.add_row("records received", str(cov.records))
    measured.add_row("is that a total?", "yes" if cov.total_is_knowable else "[red]no, a floor[/red]")
    measured.add_row("reads that lost something", str(len(agg.lossy_reads)))
    measured.add_row("of those, unrepairable", str(len(agg.unrepairable_reads)))
    measured.add_row("fields the shared model dropped", str(len(agg.ceiling.all_dropped)))
    console.print(measured)
    console.print()
    chosen = Table(title="chosen, not measured", show_header=True, header_style="bold blue")
    chosen.add_column("setting")
    chosen.add_column("where the value comes from")
    chosen.add_column("what goes wrong if it is off")
    for name, source, risk in provenance():
        chosen.add_row(name, source, risk)
    console.print(chosen)
    console.print()
    console.print("[bold]Never measured, by anything here[/bold]")
    for line in what_is_never_measured():
        console.print(f"  [yellow]-[/yellow] {line}")
    console.print()
    console.print(f"[yellow]{completeness_has_no_denominator()}[/yellow]")
    console.print()


def show_gate(gate: Gate) -> None:
    console.print()
    if gate.failures:
        console.print("[bold red]Fixable at the source[/bold red]")
        for headline, detail in gate.failures:
            console.print(f"  [red]x[/red] {headline}")
            console.print(f"    [dim]{detail}[/dim]")
        console.print()
    if gate.reported:
        console.print("[bold]Reported and NOT failed on[/bold]")
        for headline, detail in gate.reported:
            console.print(f"  [yellow]-[/yellow] {headline}")
            console.print(f"    [dim]{detail}[/dim]")
        console.print()
    console.print(
        f"[bold red]{gate.summary()}[/bold red]" if gate.failures
        else f"[bold green]{gate.summary()}[/bold green]"
    )
    console.print()


def show_policy_matrix(rows: list[tuple[str, dict[ConflictPolicy, str]]]) -> None:
    console.print()
    table = Table(title="one set of claims, every policy", show_header=True,
                  header_style="bold blue")
    table.add_column("entity")
    for policy in ConflictPolicy:
        table.add_column(policy.value, justify="center")
    for entity, answers in rows:
        table.add_row(entity, *[answers.get(p, "-") for p in ConflictPolicy])
    console.print(table)
    console.print()
    console.print(
        "[bold yellow]The claims never changed. Which policy you configured decides not only what "
        "the answer is, but WHICH entities you can answer about at all.[/bold yellow]"
    )
    console.print()


def show_identity_matrix(totals: dict[Identity, int]) -> None:
    console.print()
    table = Table(title="one set of rows, every identity scheme", show_header=True,
                  header_style="bold blue")
    for identity in Identity:
        table.add_column(identity.value, justify="right")
    table.add_row(*[str(totals[i]) for i in Identity])
    console.print(table)
    console.print()
