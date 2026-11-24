# Denominator

Pulling records from an XML feed, a JSON API and a scraped HTML page is a solved engineering
problem: adapters, schema normalisation, token refresh, retries. Denominator does all of
that, then takes on the part that is not solved: showing that nothing was missed. You can count
what you received but not what exists, and offset paging over a collection that changes
mid-read drops or duplicates rows without a single error.

```bash
./setup.sh
```

---

## "Complete" Has No Denominator

The requirements note this project started from said:

> **Worst input:** one source returns XML, another JSON, another HTML. One is paginated, one isn't.
> One uses auth tokens that expire mid-request.
> **Accuracy concern:** complete data aggregation from all sources without missing records.
> **Best approach:** per-source adapter pattern with schema normalization, auth refresh, retry.
> **Verdict:** local-only. **Software engineering problem, not accuracy problem.**

Every mechanism it names is built here. And the verdict is wrong, because *"without missing
records"* is not a property you can engineer your way to — it is a claim with no denominator.

**You can count what you received. You cannot count what exists.** Asking the source for a total
gets another claim from the thing you are auditing. And even an honest total is true for an instant,
while a paginated read occupies a stretch of time.

---

## The finding: offset paging over a changing collection

Ten rows, page size three, something changes after page one. Both pages are individually
well-formed. Nothing errors. Nothing logs.

| paging | what changed | read | distinct | outcome | which rows |
|:--|:--|--:|--:|:--|:--|
| `NONE` | *anything* | 10 | 10 | `CLEAN` | — |
| `OFFSET` | nothing | 10 | 10 | `CLEAN` | — |
| `OFFSET` | **insert behind** | 11 | 10 | `DUPLICATED` | duped `r03`, never saw `r00` |
| `OFFSET` | insert ahead | 11 | 11 | `CLEAN` | — |
| `OFFSET` | **delete behind** | 9 | 9 | `MISSED` | **`r04` never read** |
| `KEYSET` | nothing | 10 | 10 | `CLEAN` | — |
| `KEYSET` | **insert behind** | 10 | 10 | `UNSEEN_INSERT` | never saw `r00` |
| `KEYSET` | insert ahead | 11 | 11 | `CLEAN` | — |
| `KEYSET` | delete behind | 10 | 10 | `CLEAN` | — |

An insert before the cursor shifts everything down, so a row is read **twice**. A delete before the
cursor shifts everything up, so a row is **never read**. Two opposite failures, from the same
mechanism, on the same data.

### And deduplication repairs exactly one of them

> **INSERT:** 11 read, 10 distinct. Taking the distinct set recovers 10, which *is* the right
> answer — deduplication repairs this failure completely.
>
> **DELETE:** 9 read, 9 distinct. Deduplication cannot invent `r04`, so the distinct count is
> simply wrong — **and it looks exactly as plausible as a correct one.**

Which is the dangerous half. A pipeline that deduplicates its own output has silently converted the
visible failure into an invisible one, and left the invisible one untouched.

### Keyset paging is strictly better and still not complete

It never shifts and never duplicates, and it survives the deletion that offset paging does not. But
a row inserted **behind** the cursor is never seen, because the cursor has already passed where it
sorts. That is not a bug. **Keyset paging is complete with respect to a snapshot that never
existed.**

There is no scheme under which *complete* has a value for a collection that changes while you read
it. The only unambiguously complete read is a single response — complete as of that one instant,
which is the most any scheme achieves.

### The prescribed retry is what causes the miss

> A token expiring mid-read forces a re-authentication and a resume at offset 9. That offset was
> computed against the collection as it was **before** the interruption. If anything was deleted
> while the retry was in flight, the resumed offset now points past a row nobody read.

Retry is not a repair. It is a second chance to be wrong at a cursor that has gone stale.

---

## "Note what's missing" can name the source and never the amount

The uplift asks for partial failure handling: *3 of 4 sources respond → still produce output, note
what's missing.*

On the corpus: **13 records from 4 sources that answered in full. 3 sources contributed an UNKNOWN
number, so 13 is a FLOOR, not a total. Records missing: at least 0, at most unbounded.**

A source that did not answer did not say how much it was holding. So the report names *which*
source is silent and never *how much*, and it never prints 13 without the word *floor* beside it.

Five outcomes, because collapsing any two of them is how a report claims a total it has no basis
for:

| | | count means something |
|:--|:--|:--|
| `ANSWERED` | responded, paging ran to the end | yes |
| `EMPTY` | responded, and had nothing — **which is information** | yes |
| `TRUNCATED` | stopped part-way, so its count is a floor | no |
| `FAILED` | no usable response, so its contribution is unknown | no |
| `NEVER_ASKED` | we did not ask | no |

`EMPTY` and `FAILED` both contribute zero and mean opposite things. The model refuses to construct
a `FAILED` report carrying a record count at all.

### A source's own count of itself

`gamma` declares 4 records and delivers 3 — **strong evidence that something is wrong, and no
evidence about which number to trust.** `alpha` declares 4 and delivers 4, which is **weak**
evidence: both numbers come from the same source, so a source that undercounts itself produces
exactly this agreement.

---

## The union depends on the identity key

Same 20 rows from four formats:

```
NATIVE_ID = 20      SHARED_KEY = 9      NONE = 20
```

*How many records do we have* is answered by a decision about identity, not by the data. A row
carrying no key under the chosen scheme is kept separate — which looks like being unique and is
only being **unmatched**.

---

## The conflict policies disagree, and each answers a different entity

The uplift asks for a strategy pattern: latest wins / highest confidence / flag for review. On one
set of three claims they split — latest gives `41.50`, confidence and majority both give `42.00`.
**Two answers from one input, and nothing in the data prefers either.**

But the sharper result came out of the corpus. On the two contested entities:

| entity | `LATEST_WINS` | `HIGHEST_CONFIDENCE` | `MAJORITY` | `FLAG_FOR_REVIEW` |
|:--|:--|:--|:--|:--|
| `K-3` | cannot run | cannot run | **`41.00`** | declines |
| `K-7` | **`42.00`** | cannot run | cannot run | declines |

Exactly one policy can run on each — and it is a **different** policy each time. A system
configured with `LATEST_WINS` answers `K-7` and silently fails `K-3`; one configured with
`MAJORITY` does the opposite.

**So the policy you chose decides not only what the answer is, but which entities you can answer
about at all.** Each policy needs something the claims may not carry, and returning a value anyway
by falling back to "first one wins" is how a strategy pattern quietly stops being the strategy that
was chosen. So a policy whose precondition is absent reports `cannot run` rather than guessing.

`FLAG_FOR_REVIEW` is the default. It answers nothing, and it is the only one that invents nothing.

---

## The adapters, and the ceiling they cannot exceed

Four formats, four adapters — JSON, XML (`ElementTree`), HTML (scraped with regex, because that is
what a scraper against an HTML source actually is), and CSV. None of the sibling projects parses
XML, so it is new ground here.

**The finding about normalization is not new, and it would be dishonest to pitch it as such.**
Shoehorn already established that a common target model sets a ceiling the parser cannot
exceed. The same holds: `Record` is the intersection of what four formats can say, so
`warehouse_region`, `supplier_note` and `tax_band` — each offered by exactly one source — have
nowhere to go. Every adapter reported success and the information is gone.

What this adds is the count, per source, and the observation that **fields offered by exactly one
source are the first things an intersection cannot hold.**

---

## Commands

| | |
|:--|:--|
| `paging` | every scheme against every mutation, and what each loses |
| `compare-schemes` | offset against keyset, side by side |
| `sources` | every source, its format, and whether its count means anything |
| `fanout` | what came back, and what the gap is worth |
| `identity` | the same rows counted under every identity scheme |
| `conflict` | every policy on every contested entity, with its precondition |
| `compare-policies` | one dataset, every policy, as a matrix |
| `normalize` | what each adapter offered and what the shared model kept |
| `report` | the run you would actually do; `-o DIR` writes `report.json` |
| `doctor` | what was measured, what was chosen |
| `explain <source>` | walk one source through every step |
| `check` | fail on what a source owner can fix; report everything else |

### `check` gates only on what a source owner can fix

**Fails on** offset paging over a collection that changed mid-read, a declared total that
contradicts the rows delivered, and records carrying no identity key. Three failures on the corpus.
All three are fixable by whoever runs that source.

**Reports and does not fail on** a source that failed, an unknown denominator, keyset paging losing
a row that appeared mid-read, fields the shared model cannot hold, policy sensitivity, and the
identity count depending on the scheme. Seven of those.

Verified both ways in CI: a clean single-source manifest **passes**, and one source paging by
offset over a mutating collection **fails**.

---

## Honest limits

- **The denominator does not exist.** Nothing here can tell you how many records a failed source was
  holding, and nothing ever will.
- **The identity scheme and the conflict policy are chosen, never discovered.** Both change the
  output; `doctor` lists them under *chosen, not measured*.
- **The mutations are declared in the manifest, not observed.** A real aggregator does not know that
  a row was deleted behind its cursor — which is the whole problem. Here they are stated so the
  accounting can be checked; in production the loss is exactly as invisible as this project claims.
- **`TRUNCATED` is planted, not detected.** Nothing here implements token expiry; the manifest says
  a source stopped part-way and the tool treats its count as a floor.
- **The HTML adapter is regex-based and will break.** Deliberately: a source that renames a column
  silently produces records with a field nobody asked for, and that fragility belongs in the open.
- **Normalisation loss is Shoehorn's finding**, restated in a new domain with counts.
- **Nothing here fixes anything.** Page by a stable key, emit a shared identity, say whether your
  count is a total or a floor, and expect *complete* to be a shape of words rather than a fact.

---

## Tests

**198 tests, 100% coverage** (`render.py` excluded — it prints; the claims live in the modules it
prints from).

```
tests/unit/test_paging_and_fanout.py   64   paging, losses, outcomes, the denominator
tests/unit/test_merge_and_gate.py      70   adapters, identity, policies, the gate, the corpus
tests/integration/test_cli.py          64   every command and the sentences it must say
```

`TestTheCorpusStillProducesEveryOutcome` asserts every `SourceOutcome`, every `Loss` mode and all
four formats still occur, so no assertion elsewhere can quietly become vacuous. `data/generate.py`
owns `data/` and prunes any payload not in its manifest.

---

## Four defects found while building this

**1. `policies_agree` compared only values, and missed the real disagreement.** On the corpus it
reported *zero* policy-sensitive entities while the per-policy answers plainly differed. The cause:
on claims where only one policy can run, the values trivially "agree" while the choice of policy
decides everything. `inoperable` now counts as a disagreement, kept separate from
`FLAG_FOR_REVIEW`, which returns nothing **on purpose** — *cannot run* and *declines to pick* are
not the same answer.

**2. The corpus declared totals described the wrong population.** `alpha` declared 10 and delivered
4, because 10 was the count of *paged rows* while `records` counts *parsed payload records*. Three
sources fired a "declared total disagrees" failure that was my bookkeeping, not theirs. Now exactly
one source disagrees, and it does so on purpose.

**3. Two unreachable branches and one unused property, found by reading coverage rather than by
chasing it.** `_outcome_for(parsed=None)` can never fire because `run` handles a parse failure where
the exception is caught; the `len(claims) < 3` majority branch can never fire because two differing
claims always tie and return earlier. Both deleted rather than tested around.

**4. Class-scoped fixtures captured 80-column output.** Ten integration fixtures were
`scope="class"`, and higher-scoped fixtures are instantiated *before* function-scoped autouse ones —
so they ran before the console was widened, and four assertions failed on wrapped text. The
diagnosis was more useful than the fix: a test that depends on terminal width is a test that passes
on your machine.
