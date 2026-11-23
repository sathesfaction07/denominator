# ADR 002 — A conflict policy refuses rather than falls back, and that refusal is the finding

**Status:** accepted
**Date:** 2026-09-18

## Context

The uplift asks for conflict resolution as a strategy pattern: *latest wins / highest confidence
wins / flag for review*. Probed on three claims carrying everything each policy needs, they split —
latest gives `41.50`, confidence and majority both give `42.00`. Two answers from one input, and
nothing in the data preferring either.

That much was expected. What was not: **each policy needs something the claims may not carry.**
Latest wins needs a comparable timestamp on *every* claim. Highest confidence needs a confidence on
every claim, and those numbers are supplied by the sources being compared. Majority needs three or
more sources and no tie.

The obvious implementation returns a value regardless, falling back to first-one-wins when the
preferred key is missing. That is how a strategy pattern quietly stops being the strategy that was
chosen.

## Decision

**A policy whose precondition is absent returns `usable=False` and no value.** And keep that
strictly separate from `FLAG_FOR_REVIEW`, which returns no value *on purpose*:

- `usable=False` — **cannot run.** The claims do not support this strategy.
- `usable=True, value=None` — **declines to pick.** The only policy that invents nothing.

`FLAG_FOR_REVIEW` is the default.

`PolicyComparison.policies_agree` requires that every policy could run **and** they all reached the
same value. Comparing values alone was the first implementation and it was wrong — see below.

## Consequences

**Good, and this is the sharpest result in the project.** On the corpus's two contested entities,
exactly one policy can run on each, and it is a *different* policy each time:

| entity | `LATEST_WINS` | `HIGHEST_CONFIDENCE` | `MAJORITY` |
|:--|:--|:--|:--|
| `K-3` | cannot run | cannot run | **`41.00`** |
| `K-7` | **`42.00`** | cannot run | cannot run |

So the policy you configured decides not only what the answer is but **which entities you can answer
about at all.** A system set to `LATEST_WINS` answers `K-7` and silently fails `K-3`; one set to
`MAJORITY` does the opposite. Neither knows what it is not answering.

**Good.** A policy that refuses is visible. A policy that falls back is not, and produces a value
indistinguishable from one the chosen strategy actually computed.

**Bad, and this was a defect of mine.** `policies_agree` originally compared only the distinct
values, so it reported **zero** policy-sensitive entities on a corpus where the answers plainly
differed — because on claims where only one policy can run, the values trivially agree. Found by
reading output against the per-policy resolutions rather than by any test.

**Bad.** The default answers nothing, so a user running this expecting resolved values gets none
until they choose a policy and accept what it needs.

**Bad.** `MAJORITY` counts sources rather than weighing them. Two systems fed by one upstream outvote
one independent observation, and nothing here can detect that they share an origin. Stated in
`POLICY_NOTES` and not fixed.

## Alternatives

**Fall back to first-one-wins.** Always produces an answer. The answer is then attributable to
arrival order, which is a property of the network.

**Treat a missing confidence as zero.** Makes highest-confidence always runnable by inventing a
ranking. `0.0` is a claim of no confidence; absence is not a claim.

**Break a majority tie by source order.** Hands the decision to whichever source replied first.

**Pick a policy per field automatically, from what the claims carry.** Tempting and worse: the
policy would then vary by entity without anyone choosing it, so two rows in one report would be
resolved by different rules with nothing saying so.
