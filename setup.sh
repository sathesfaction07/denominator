#!/usr/bin/env bash
# Sets the project up, then re-derives every claim in the README from scratch.
#
# Nothing here is quoted from a previous run. If a finding has stopped being true, this script
# fails or prints a different number, and either way you find out.

set -euo pipefail
cd "$(dirname "$0")"

export COLUMNS=200
export MSA_DATA_DIR=data

banner() { printf '\n\033[1;34m%s\033[0m\n' "$*"; }
note()   { printf '  \033[2m%s\033[0m\n' "$*"; }

banner "1. Environment"
if [ ! -d .venv ]; then
    python3 -m venv .venv
    note "created .venv"
fi
.venv/bin/pip install -q -e ".[dev]"
note "installed denominator and its dev extras"
PY=.venv/bin/python
CLI=.venv/bin/denominator

banner "2. Corpus"
$PY data/generate.py

banner "3. Tests and lint"
$PY -m pytest -q --cov=multi_source_agg --cov-report=term-missing 2>&1 | tail -16
.venv/bin/ruff check src tests data >/dev/null && note "ruff clean"
rm -f .coverage

banner "FINDING 0 -- 'complete' is a numerator with no denominator"
$PY -c "
from multi_source_agg.paging import completeness_has_no_denominator
print('  ' + completeness_has_no_denominator())
"

banner "FINDING 1 and 2 -- offset paging fails two opposite ways; dedup fixes only one"
$PY - <<'PY'
from multi_source_agg.models import Mutation, Paging
from multi_source_agg.paging import dedup_verdict, read

rows = [f"r{i:02d}" for i in range(1, 11)]
print("  10 rows, page size 3, something changes after page 1:")
for scheme in Paging:
    for mutation in Mutation:
        r = read("s", rows, scheme, 3, mutation)
        bits = []
        if r.missed:
            bits.append(f"MISSED {','.join(r.missed)}")
        if r.duplicated:
            bits.append(f"DUPED {','.join(r.duplicated)}")
        if r.unseen_inserts:
            bits.append(f"never saw {','.join(r.unseen_inserts)}")
        print(f"    {scheme.value:7} {mutation.value:14} read={r.rows_read:2} "
              f"distinct={r.distinct_read:2}  {r.loss.value:14} {'; '.join(bits)}")
print()
dup = read("s", rows, Paging.OFFSET, 3, Mutation.INSERT_BEHIND)
miss = read("s", rows, Paging.OFFSET, 3, Mutation.DELETE_BEHIND)
assert dup.dedup_repairs_it and not miss.dedup_repairs_it
print(f"  {dedup_verdict(dup)}")
print(f"  {dedup_verdict(miss)}")
PY

banner "FINDING 3 -- keyset paging is strictly better and still not complete"
$CLI compare-schemes

banner "FINDING 4 -- the retry the brief prescribes is what causes the miss"
$PY -c "
from multi_source_agg.paging import why_retry_makes_it_worse
print('  ' + why_retry_makes_it_worse(9))
"

banner "FINDING 5 -- 'note what's missing' can name the source, never the amount"
$CLI fanout | grep -E "records from|WHICH source" || true

banner "FINDING 6 -- the conflict policies disagree, and each answers a DIFFERENT entity"
$CLI compare-policies

banner "FINDING 7 -- the union depends on the identity key"
$CLI identity | tail -8

banner "The normalisation ceiling (Shoehorn's finding, in a new domain)"
$CLI normalize | grep -E "nowhere to go|Shoehorn|exactly one source" || true

banner "What the tool says about itself"
$CLI doctor

banner "The report you would actually run"
$CLI report

banner "The gate"
if $CLI check; then
    note "check passed"
else
    note "check FAILED, which is correct -- the corpus plants three source-fixable defects"
fi

banner "Done"
cat <<'EOF'
  Try these:
    denominator paging                     every scheme against every mutation
    denominator compare-schemes            offset against keyset
    denominator compare-policies           one dataset, every conflict policy
    denominator identity                   one set of rows, every identity scheme
    denominator explain alpha              a source that lost a row unrepairably
    denominator explain zeta               a source whose count means nothing
    denominator report -o /tmp/out
EOF
