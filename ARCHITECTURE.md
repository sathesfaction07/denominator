# Architecture

## The shape of the problem

The brief asks for *complete data aggregation from all sources without missing records* and calls it
a software engineering problem. The module layout is the argument that it is not: each stage adds an
unknown, and no later stage removes an earlier one.

```
  manifest.json ──> 8 sources in 4 formats
                         │
                         ▼
                   adapters.py        JSON / XML / HTML / CSV -> one Record model
                         │            and a count of what the model could not hold
                         ▼
                    paging.py         read each source, and account for what the READ lost
                         │            -> Loss: CLEAN | DUPLICATED | MISSED | UNSEEN_INSERT
                         ▼
                    fanout.py         who answered, and whose silence makes the total unknowable
                         │            -> SourceOutcome x5, and total_is_knowable
                         ▼
                     merge.py         union (needs an IDENTITY decision)
                         │            conflict (needs a POLICY decision)
                         ▼
                 aggregate.py ──> render.py / gate.py
```

Read left to right, every arrow is a place where a naive implementation would have produced a
number and moved on.

## Why `Loss` has four members and not a boolean

`DUPLICATED` and `MISSED` are not degrees of one thing:

| | repairable by dedup | how it looks afterwards |
|:--|:--|:--|
| `DUPLICATED` | **yes**, completely | count is too high, and visibly so |
| `MISSED` | **no** | count is plausible, and wrong |
| `UNSEEN_INSERT` | no, and nothing should | count is plausible, and arguably right |

So `loss` reports the *worst* thing that happened, with `MISSED` outranking `DUPLICATED`
deliberately — the unrepairable one is the one worth knowing about, and a pipeline that deduplicates
its own output has converted the visible failure into an invisible one while leaving the invisible
one untouched.

`UNSEEN_INSERT` exists because a row that appeared mid-read behind the cursor is not a reader
defect. There was no instant at which it was both present and ahead of the cursor. That case is
reported and never gated.

## Why `SourceOutcome` has five members

Because `EMPTY` and `FAILED` both contribute zero records and mean opposite things. An empty source
told you something and its zero belongs in the total; a failed source told you nothing and its zero
is a placeholder for a number nobody knows.

`OUTCOME_NOTES` carries a boolean per member — *does a record count from this source mean anything*
— and `SourceReport` has a validator refusing to construct a `FAILED` or `NEVER_ASKED` report that
carries a count at all. `Coverage.total_is_knowable` is then just "did any source contribute an
unknown", and the headline branches on it. That is the whole mechanism preventing a floor from being
printed as a total.

## Two populations, deliberately not merged

`coverage.records` counts only sources whose count is meaningful. `union.rows` counts every `Record`
actually held — which is **larger**, because a `TRUNCATED` source's records are real even though its
count is only a floor.

Both numbers are correct and they differ. Printing them adjacent without saying why was a defect in
the first version of `headline()`; it now names both populations and the reason.

## Where the answer comes from outside the data

| setting | what it decides | if it is wrong |
|:--|:--|:--|
| `identity` | how many distinct records exist | `NATIVE_ID` 20, `SHARED_KEY` 9, `NONE` 20 |
| `policy` | which value wins, and **which entities are answerable at all** | a different entity goes unanswered |
| `page_size` | which row a mid-read mutation lands on | size 3 loses `r04`, size 4 loses `r05` |
| `skip` | which sources are `NEVER_ASKED` | the total becomes a floor rather than shrinking |

`doctor` prints all four under a heading reading **chosen, not measured**, beside a table of what
actually was measured, because the two are indistinguishable in a report otherwise.

Defaults are the ones that invent least: `SHARED_KEY` (which merges, so the count is smaller and
harder to overstate) and `FLAG_FOR_REVIEW` (which answers nothing).

## Policies refuse rather than fall back

Each conflict policy needs something the claims may not carry — a comparable timestamp, a
confidence, three or more sources. `resolve` returns `usable=False` when the precondition is absent,
rather than silently falling back to first-one-wins. That distinction is load-bearing:

- `usable=False` — **cannot run.** The claims do not support this strategy.
- `usable=True, value=None` — **declines to pick.** `FLAG_FOR_REVIEW`, on purpose.

Conflating those was the first version's defect. `PolicyComparison.policies_agree` originally
compared only values, so on claims where one policy could run the values trivially agreed while the
policy decided everything. It now requires every policy to have run *and* agreed.

## The gate's dividing line

One question: **can the owner of a source fix it at the source?**

```
FAILS (fixable at source)              REPORTS (not fixable there)
  offset paging on mutable data          a source that failed
  declared total contradicts rows        an unknown denominator
  records carrying no identity key       keyset losing a mid-read insert
                                         fields the shared model cannot hold
                                         policy sensitivity
                                         identity-dependent counts
```

Three failures and seven reports on the corpus. Verified both ways in CI: a clean single-source
manifest passes; one source paging by offset over a mutating collection fails.

## The corpus

`data/generate.py` writes a manifest plus eight payloads in four formats, each source named for the
outcome it produces — `alpha` loses a row, `beta` never sees one, `zeta` will not parse, `theta` is
never asked. Hand-authored, so a reader can check the tool's claim against the input by eye.

The generator **owns** `data/` and prunes any payload not in its manifest. CI plants a stale file and
asserts it is removed, because a leftover payload from an older convention parses cleanly and
quietly changes every count.

## What is deliberately absent

- **No network, no credentials, no model.** The accuracy problem is arithmetic and read semantics.
- **No real auth flow.** `TRUNCATED` is declared in the manifest. The point being made is what a
  truncated read does to a total, not how OAuth works.
- **No concurrency.** Fan-out is sequential. A `ThreadPoolExecutor` would change the timing and none
  of the findings, and it exists in five other projects here.
- **No HTML parser dependency.** The regex scraper is what a scraper against an HTML source
  actually is, and its fragility is part of the argument.
- **No pandas.** The join semantics under examination are plain Python.
