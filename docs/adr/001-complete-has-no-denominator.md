# ADR 001 — "Complete" is a numerator with no denominator

**Status:** accepted
**Date:** 2026-09-18

## Context

The brief's accuracy concern is *complete data aggregation from all sources without missing records*,
and its verdict is **"software engineering problem, not accuracy problem."** Building the recommended
mechanisms — per-source adapters, schema normalization, auth refresh, retry — raised the question of
how anyone would know whether the result was complete.

You can count what you received. You cannot count what exists. Every route to the denominator turns
out to be another claim from the thing being audited:

- **Ask the source for a total.** Headcount already established this much in a different domain,
  with its `declared_total` labelled "what the SOURCE claims". Nothing new here.
- **Compare the total to the rows delivered.** Agreement is *weak* evidence, because both numbers
  come from the same source — a source that undercounts itself produces exactly that agreement.
  Disagreement is strong evidence of something, and no evidence about which number to trust.
- **Count what a failed source was holding.** At least zero, at most unbounded.

And a fourth route fails for a reason none of the others do. **Even an honest total is true for an
instant, while a paginated read occupies a stretch of time.** Probed, page size 3 over 10 rows:

| mutation after page 1 | offset | keyset |
|:--|:--|:--|
| none | 10 read, clean | 10 read, clean |
| insert behind the cursor | 11 read, `r03` twice, `r00` never seen | 10 read, `r00` never seen |
| delete behind the cursor | 9 read, **`r04` never seen** | 10 read, clean |

## Decision

**Treat completeness as a property that has no value, and make every count say which population it
is about.**

- `Loss` has four members. `DUPLICATED` and `MISSED` are separate because dedup repairs one
  completely and cannot touch the other; `loss` reports the worst, with `MISSED` outranking.
- `SourceOutcome` has five. `EMPTY` and `FAILED` both contribute zero and mean opposite things, so
  `OUTCOME_NOTES` carries "does this count mean anything" per member and a validator refuses to
  build a `FAILED` report with a record count.
- `Coverage.total_is_knowable` is false whenever any source contributed an unknown, and
  `headline()` then prints the word **FLOOR** beside the number and "records missing: at least 0, at
  most unbounded".
- No count is printed without the words that qualify it.

## Consequences

**Good.** The headline cannot mislead. On the corpus it reads "13 records … so 13 is a FLOOR, not a
total", which is the honest form of the number the brief wanted.

**Good.** The two populations stayed separate. `coverage.records` (13) excludes a `TRUNCATED`
source's count; `union.rows` (20) includes its records, because they are real records we hold. Both
are right and they differ, which the headline now says explicitly — an earlier version printed them
adjacent and implied one collapsed into the other.

**Good.** It makes the brief's verdict testable rather than arguable. `check` fails a source that
pages by offset over a mutating collection, because that *is* fixable engineering; it reports and
does not fail a source that was down, because that is not.

**Bad.** The output is longer and less quotable. Nobody can paste "we aggregated 13 records" into a
status update without the caveat travelling with it, which is the intent and is also friction.

**Bad.** The mutations are declared in the manifest rather than observed. A real aggregator does not
know a row was deleted behind its cursor — that is the entire problem — so the accounting here is
checkable in a way production never is. Stated in the README rather than papered over.

## Alternatives

**Trust the declared total.** Simplest, and it makes an incomplete read look complete whenever the
source is the thing that is wrong. Rejected: the check and the thing checked share an origin.

**Report a percentage.** "97% complete" requires the denominator that does not exist. It would have
been the most confident-looking output available and the least defensible.

**Treat a failed source as zero.** Turns an unknown into a number, which is the specific failure the
five-member `SourceOutcome` exists to prevent.
