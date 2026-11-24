"""Command line.

`report` is the one you would run. The rest exist because the reasoning is the product.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console

from . import render
from .adapters import ceiling
from .aggregate import payload, run
from .config import Settings
from .gate import check as run_gate
from .merge import compare_identities, compare_policies, union
from .models import ConflictPolicy, Identity, Mutation, Paging
from .paging import compare_schemes, dedup_verdict, read

app = typer.Typer(
    add_completion=False,
    help="Aggregates records from several sources, and reports why 'complete' has no denominator.",
)
console = Console()

ROWS = [f"r{i:02d}" for i in range(1, 11)]


def _settings(identity: str | None = None, policy: str | None = None) -> Settings:
    kwargs: dict = {}
    if identity is not None:
        kwargs["identity"] = Identity(identity.upper())
    if policy is not None:
        kwargs["policy"] = ConflictPolicy(policy.upper())
    return Settings(**kwargs)


@app.command()
def doctor(
    identity: str = typer.Option(None, help="NATIVE_ID, SHARED_KEY or NONE"),
    policy: str = typer.Option(None, help="LATEST_WINS, HIGHEST_CONFIDENCE, MAJORITY, FLAG_FOR_REVIEW"),
) -> None:
    """Say what was measured and what was chosen."""
    render.show_doctor(run(_settings(identity, policy)))


@app.command()
def paging(size: int = typer.Option(3, help="page size")) -> None:
    """Read the same collection every way, and show what each one loses."""
    matrix = [
        (scheme, mutation, read(f"{scheme.value.lower()}/{mutation.value.lower()}", ROWS, scheme, size, mutation))
        for scheme in Paging
        for mutation in Mutation
    ]
    render.show_paging(matrix)
    render.show_losses()


@app.command()
def sources(identity: str = typer.Option(None), policy: str = typer.Option(None)) -> None:
    """Every source, its format, and whether its record count means anything."""
    render.show_sources(run(_settings(identity, policy)))


@app.command()
def fanout(identity: str = typer.Option(None), policy: str = typer.Option(None)) -> None:
    """What came back, and what the gap is worth."""
    render.show_fanout(run(_settings(identity, policy)))


@app.command("identity")
def identity_counts(policy: str = typer.Option(None)) -> None:
    """Count the same rows under every identity scheme."""
    agg = run(_settings(None, policy))
    render.show_identity(compare_identities(agg.records))
    render.show_identity_matrix({i: union(agg.records, i).distinct for i in Identity})


@app.command()
def conflict(identity: str = typer.Option(None)) -> None:
    """Every conflict policy, on every contested entity."""
    agg = run(_settings(identity, None))
    render.show_policies({k: compare_policies(v) for k, v in agg.contested().items()})


@app.command()
def normalize(identity: str = typer.Option(None)) -> None:
    """What each adapter offered, and what the shared model could not hold."""
    agg = run(_settings(identity, None))
    render.show_normalisation(agg.parsed, ceiling(agg.parsed))


@app.command("compare-policies")
def compare_policies_cmd(identity: str = typer.Option(None)) -> None:
    """One dataset, every policy, because the divergence is the finding."""
    agg = run(_settings(identity, None))
    rows = []
    for key, claims in sorted(agg.contested().items()):
        comparison = compare_policies(claims)
        answers = {}
        for r in comparison.resolutions:
            answers[r.policy] = (
                "cannot run" if not r.usable else (r.value if r.value is not None else "declines")
            )
        rows.append((key, answers))
    render.show_policy_matrix(rows)


@app.command("compare-schemes")
def compare_schemes_cmd(size: int = typer.Option(3)) -> None:
    """Offset against keyset, under every mutation."""
    console.print()
    for mutation in Mutation:
        comparison = compare_schemes(ROWS, mutation, size)
        marker = "[green]agree[/green]" if comparison.schemes_agree else "[yellow]differ[/yellow]"
        console.print(f"  [bold]{mutation.value}[/bold] {marker}: {comparison.summary()}")
        console.print()


@app.command()
def report(
    identity: str = typer.Option(None),
    policy: str = typer.Option(None),
    out: Path = typer.Option(None, "--out", "-o", help="also write report.json here"),
) -> None:
    """The run you would actually do."""
    agg = run(_settings(identity, policy))
    render.show_report(agg)
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(json.dumps(payload(agg), indent=2, sort_keys=True), encoding="utf-8")
        console.print(f"[dim]wrote {out / 'report.json'}[/dim]")


@app.command()
def check(identity: str = typer.Option(None), policy: str = typer.Option(None)) -> None:
    """Fail on what a source owner can fix; report everything else."""
    gate = run_gate(run(_settings(identity, policy)))
    render.show_gate(gate)
    if gate.failures:
        raise typer.Exit(1)


@app.command()
def explain(
    source: str,
    identity: str = typer.Option(None),
    policy: str = typer.Option(None),
) -> None:
    """Walk one source through every step."""
    agg = run(_settings(identity, policy))
    report_for = next((r for r in agg.reports if r.source == source), None)
    if report_for is None:
        console.print(f"[red]no source {source}[/red]")
        raise typer.Exit(1)

    console.print()
    console.print(f"[bold]{source}[/bold] ({report_for.fmt})")
    console.print(f"  outcome     [bold]{report_for.outcome.value}[/bold]")
    console.print(f"  {report_for.detail}")
    console.print()
    if report_for.count_is_meaningful:
        console.print(f"  records     {report_for.records}")
    else:
        console.print(
            "  records     [red]unknown[/red] -- this source's count means nothing, and zero "
            "would be a claim nobody can support"
        )
    if report_for.read is not None:
        r = report_for.read
        console.print(f"  paging      {r.paging.value}, {r.pages} page(s) of {r.rows_read} row(s)")
        console.print(f"  mid-read    {r.mutation.value}")
        console.print(f"  loss        [bold]{r.loss.value}[/bold]")
        console.print(f"  {dedup_verdict(r)}")
    else:
        console.print("  paging      none -- one response, one instant")
    parsed = next((p for p in agg.parsed if p.source == source), None)
    if parsed and parsed.dropped_fields:
        console.print(
            f"  dropped     {', '.join(parsed.dropped_fields)} -- offered, and the shared model "
            "has nowhere to put them"
        )
    console.print()
    mine = [r for r in agg.records if r.source == source]
    contested = {k: v for k, v in agg.contested().items() if any(c.source == source for c in v)}
    console.print(f"  {len(mine)} record(s) reached the union; {len(contested)} of them contested")
    for key, claims in sorted(contested.items()):
        comparison = compare_policies(claims)
        console.print(f"    [bold]{key}[/bold]: {comparison.summary()}")
    console.print()


if __name__ == "__main__":  # pragma: no cover
    app()
