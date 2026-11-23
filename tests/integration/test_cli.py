"""The commands, and the sentences they are required to say.

Rich folds long words inside table cells, so `flat` collapses whitespace before matching.
"""

from __future__ import annotations

import json
import re

import pytest
from typer.testing import CliRunner

from multi_source_agg.cli import app

from ..conftest import CORPUS

runner = CliRunner()


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def run_cli(*args: str, env: dict | None = None):
    base = {"MSA_DATA_DIR": str(CORPUS)}
    base.update(env or {})
    return runner.invoke(app, list(args), env=base)


class TestPaging:
    @pytest.fixture
    def output(self):
        return run_cli("paging")

    def test_it_runs(self, output):
        assert output.exit_code == 0

    def test_it_refutes_the_briefs_verdict_directly(self, output):
        text = flat(output.stdout)
        assert "software engineering problem. It is not" in text

    def test_it_shows_every_scheme_and_every_mutation(self, output):
        for name in ("NONE", "OFFSET", "KEYSET", "INSERT_BEHIND", "INSERT_AHEAD", "DELETE_BEHIND"):
            assert name in output.stdout

    def test_it_names_the_two_opposite_offset_failures(self, output):
        text = flat(output.stdout)
        assert "read TWICE" in text
        assert "NEVER read" in text

    def test_it_reports_which_rows_were_lost(self, output):
        text = flat(output.stdout)
        assert "missed r04" in text
        assert "duped r03" in text
        assert "never saw r00" in text

    def test_it_separates_what_dedup_can_and_cannot_fix(self, output):
        text = flat(output.stdout)
        assert "repairs this failure completely" in text
        assert "cannot invent them" in text
        assert "Nothing repairs this, and nothing should" in text

    def test_it_states_that_completeness_has_no_denominator(self, output):
        text = flat(output.stdout)
        assert "cannot count what exists" in text
        assert "snapshot that never existed" in text

    def test_it_names_retry_as_the_cause(self, output):
        assert "not a repair" in flat(output.stdout)

    def test_a_different_page_size_loses_a_different_row(self):
        output = run_cli("paging", "--size", "4")
        assert output.exit_code == 0
        assert "missed r05" in flat(output.stdout)


class TestCompareSchemes:
    @pytest.fixture
    def output(self):
        return run_cli("compare-schemes")

    def test_it_runs(self, output):
        assert output.exit_code == 0

    def test_it_says_the_scheme_is_not_an_implementation_detail(self, output):
        text = flat(output.stdout)
        assert "decides which records exist" in text

    def test_agreement_is_attributed_to_the_mutation(self, output):
        assert "property of this mutation" in flat(output.stdout)


class TestSources:
    @pytest.fixture
    def output(self):
        return run_cli("sources")

    def test_it_runs(self, output):
        assert output.exit_code == 0

    def test_every_outcome_appears(self, output):
        for name in ("ANSWERED", "EMPTY", "TRUNCATED", "FAILED", "NEVER_ASKED"):
            assert name in output.stdout

    def test_a_source_whose_count_means_nothing_shows_a_question_mark(self, output):
        text = flat(output.stdout)
        assert "does NOT count" in text

    def test_it_distinguishes_empty_from_failed(self, output):
        text = flat(output.stdout)
        assert "opposite things" in text
        assert "placeholder for a number nobody knows" in text

    def test_all_four_formats_show(self, output):
        for fmt in ("json", "xml", "html", "csv"):
            assert fmt in output.stdout


class TestFanout:
    @pytest.fixture
    def output(self):
        return run_cli("fanout")

    def test_it_runs(self, output):
        assert output.exit_code == 0

    def test_the_headline_calls_the_number_a_floor(self, output):
        text = flat(output.stdout)
        assert "FLOOR, not a total" in text
        assert "at least 0, at most unbounded" in text

    def test_it_names_which_sources_but_not_how_much(self, output):
        text = flat(output.stdout)
        assert "name WHICH source is missing and never HOW MUCH" in text

    def test_it_grades_the_evidence_of_a_declared_total(self, output):
        text = flat(output.stdout)
        assert "WEAK evidence" in text
        assert "STRONG evidence" in text


class TestIdentity:
    @pytest.fixture
    def output(self):
        return run_cli("identity")

    def test_it_runs(self, output):
        assert output.exit_code == 0

    def test_every_scheme_gets_a_count(self, output):
        for name in ("NATIVE_ID", "SHARED_KEY", "NONE"):
            assert name in output.stdout

    def test_it_says_the_count_is_a_decision(self, output):
        text = flat(output.stdout)
        assert "answered by a decision about identity" in text

    def test_the_none_scheme_is_named_as_not_a_count_of_things(self, output):
        # Every corpus record carries a shared key, so the "unmatched, not unique" sentence does
        # not fire here -- that path is covered in the unit tests instead.
        assert "not a count of THINGS" in flat(output.stdout)


class TestConflict:
    @pytest.fixture
    def output(self):
        return run_cli("conflict")

    def test_it_runs(self, output):
        assert output.exit_code == 0

    def test_every_policy_declares_its_precondition(self, output):
        text = flat(output.stdout)
        assert "needs a comparable timestamp" in text
        assert "needs a confidence per claim" in text
        assert "two copies of one upstream outvote one independent observation" in text

    def test_a_policy_that_cannot_run_says_so(self, output):
        assert "could not run" in flat(output.stdout)

    def test_flag_for_review_declines_rather_than_failing(self, output):
        assert "declines to pick" in flat(output.stdout)

    def test_it_reports_that_the_policy_decides_the_outcome(self, output):
        text = flat(output.stdout)
        assert "ruled out options somebody thought they had chosen between" in text


class TestComparePolicies:
    @pytest.fixture
    def output(self):
        return run_cli("compare-policies")

    def test_it_runs(self, output):
        assert output.exit_code == 0

    def test_it_shows_every_policy_as_a_column(self, output):
        for policy in ("LATEST_WINS", "HIGHEST_CONFIDENCE", "MAJORITY", "FLAG_FOR_REVIEW"):
            assert policy in output.stdout

    def test_it_states_the_sharpest_finding(self, output):
        text = flat(output.stdout)
        assert "The claims never changed." in text
        assert "WHICH entities you can answer about at all" in text

    def test_both_contested_entities_appear(self, output):
        assert "K-3" in output.stdout
        assert "K-7" in output.stdout


class TestNormalize:
    @pytest.fixture
    def output(self):
        return run_cli("normalize")

    def test_it_runs(self, output):
        assert output.exit_code == 0

    def test_it_lists_the_dropped_fields(self, output):
        for field in ("warehouse_region", "supplier_note", "tax_band"):
            assert field in output.stdout

    def test_it_credits_docconsolidator_rather_than_claiming_the_finding(self, output):
        text = flat(output.stdout)
        assert "same ceiling Shoehorn found" in text

    def test_it_names_the_fields_only_one_source_offers(self, output):
        assert "offered by exactly one source" in flat(output.stdout)


class TestReport:
    @pytest.fixture
    def output(self):
        return run_cli("report")

    def test_it_runs(self, output):
        assert output.exit_code == 0

    def test_the_headline_refuses_a_bare_count(self, output):
        text = flat(output.stdout)
        assert "FLOOR, not a total" in text
        assert "a different identity scheme gives a different number" in text

    def test_it_flags_the_unrepairable_loss(self, output):
        text = flat(output.stdout)
        assert "no deduplication can recover" in text
        assert "looks perfectly plausible" in text

    def test_it_names_the_policy_sensitive_entities(self, output):
        assert "conflict policy decides the outcome" in flat(output.stdout)

    def test_it_writes_a_payload_when_asked(self, tmp_path):
        output = run_cli("report", "-o", str(tmp_path))
        assert output.exit_code == 0
        result = json.loads((tmp_path / "report.json").read_text())
        assert result["sources"] == 8
        assert result["records"] == 13
        assert result["total_is_knowable"] is False
        assert result["unknown_contributors"] == ["zeta", "eta", "theta"]
        assert result["disagreeing_declared_totals"] == ["gamma"]
        assert result["lossy_reads"] == {
            "alpha": "MISSED", "beta": "UNSEEN_INSERT", "delta": "DUPLICATED"
        }
        assert result["unrepairable_reads"] == ["alpha"]
        assert result["dedup_repairable"] == ["delta"]
        assert result["formats"] == ["csv", "html", "json", "xml"]
        assert result["policy_sensitive"] == ["K-3", "K-7"]

    def test_the_identity_option_changes_the_distinct_count(self, tmp_path):
        run_cli("report", "--identity", "NATIVE_ID", "-o", str(tmp_path))
        native = json.loads((tmp_path / "report.json").read_text())
        run_cli("report", "--identity", "SHARED_KEY", "-o", str(tmp_path))
        shared = json.loads((tmp_path / "report.json").read_text())
        assert native["distinct_under_identity"] > shared["distinct_under_identity"]

    def test_the_policy_option_changes_what_gets_resolved(self, tmp_path):
        run_cli("report", "--policy", "MAJORITY", "-o", str(tmp_path))
        majority = json.loads((tmp_path / "report.json").read_text())
        run_cli("report", "--policy", "LATEST_WINS", "-o", str(tmp_path))
        latest = json.loads((tmp_path / "report.json").read_text())
        assert majority["resolved"]["K-3"] == "41.00"
        assert majority["resolved"]["K-7"] is None
        assert latest["resolved"]["K-7"] == "42.00"
        assert latest["resolved"]["K-3"] is None
        assert majority["resolved"] != latest["resolved"]


class TestDoctor:
    @pytest.fixture
    def output(self):
        return run_cli("doctor")

    def test_it_runs(self, output):
        assert output.exit_code == 0

    def test_it_separates_measured_from_chosen(self, output):
        text = flat(output.stdout)
        assert "measured from the payloads" in text
        assert "chosen, not measured" in text

    def test_it_says_whether_the_count_is_a_total(self, output):
        assert "no, a floor" in flat(output.stdout)

    def test_it_lists_what_is_never_measured(self, output):
        text = flat(output.stdout)
        assert "Never measured, by anything here" in text
        assert "at most unbounded" in text

    def test_it_closes_with_the_denominator_problem(self, output):
        assert "snapshot that never existed" in flat(output.stdout)


class TestCheck:
    def test_the_corpus_fails_on_fixable_source_defects(self):
        output = run_cli("check")
        assert output.exit_code == 1
        text = flat(output.stdout)
        assert "Fixable at the source" in text
        assert "offset paging over a collection that changed mid-read" in text
        assert "declared 4 records and delivered 3" in text

    def test_it_separates_what_it_will_not_fail_on(self):
        text = flat(run_cli("check").stdout)
        assert "Reported and NOT failed on" in text
        assert "UNKNOWN number of records" in text
        assert "Nobody can fix a source being down" in text

    def test_keyset_paging_is_not_a_failure(self):
        text = flat(run_cli("check").stdout)
        assert "paged by key and still missed a row" in text
        assert "Not a defect" in text

    def test_a_manifest_with_only_clean_sources_passes(self, tmp_path):
        (tmp_path / "clean.json").write_text(
            json.dumps([{"native_id": "1", "shared_key": "K-1", "field_name": "f",
                         "value": "v"}])
        )
        (tmp_path / "manifest.json").write_text(json.dumps({
            "rows": ["r01", "r02"],
            "sources": [{"name": "clean", "fmt": "json", "file": "clean.json",
                         "paging": "KEYSET", "page_size": 2, "mutation": "NONE",
                         "declared_total": 1, "ask": True}],
        }))
        output = run_cli("check", env={"MSA_DATA_DIR": str(tmp_path)})
        assert output.exit_code == 0
        text = flat(output.stdout)
        assert "Nothing fixable at source" in text

    def test_offset_paging_alone_is_enough_to_fail(self, tmp_path):
        (tmp_path / "s.json").write_text(
            json.dumps([{"native_id": "1", "shared_key": "K-1", "field_name": "f", "value": "v"}])
        )
        (tmp_path / "manifest.json").write_text(json.dumps({
            "rows": ["r01", "r02", "r03", "r04"],
            "sources": [{"name": "s", "fmt": "json", "file": "s.json", "paging": "OFFSET",
                         "page_size": 2, "mutation": "DELETE_BEHIND", "ask": True}],
        }))
        output = run_cli("check", env={"MSA_DATA_DIR": str(tmp_path)})
        assert output.exit_code == 1
        assert "page by a stable key instead of by position" in flat(output.stdout)


class TestExplain:
    def test_a_source_that_lost_a_row_unrepairably(self):
        output = run_cli("explain", "alpha")
        assert output.exit_code == 0
        text = flat(output.stdout)
        assert "MISSED" in text
        assert "cannot invent them" in text
        assert "warehouse_region" in text

    def test_a_source_that_only_duplicated(self):
        text = flat(run_cli("explain", "delta").stdout)
        assert "DUPLICATED" in text
        assert "repairs this failure completely" in text

    def test_a_failed_source_reports_unknown_rather_than_zero(self):
        text = flat(run_cli("explain", "zeta").stdout)
        assert "FAILED" in text
        assert "records unknown" in text
        assert "zero would be a claim nobody can support" in text

    def test_a_never_asked_source(self):
        text = flat(run_cli("explain", "theta").stdout)
        assert "NEVER_ASKED" in text
        assert "nothing here is about it" in text

    def test_an_unpaginated_source_says_one_response_one_instant(self):
        text = flat(run_cli("explain", "gamma").stdout)
        assert "one response, one instant" in text

    def test_a_keyset_source_reports_the_unseen_insert(self):
        text = flat(run_cli("explain", "beta").stdout)
        assert "UNSEEN_INSERT" in text
        assert "nothing should" in text

    def test_a_truncated_source_says_its_count_is_a_floor(self):
        text = flat(run_cli("explain", "eta").stdout)
        assert "TRUNCATED" in text
        assert "floor" in text

    def test_an_empty_source(self):
        text = flat(run_cli("explain", "epsilon").stdout)
        assert "EMPTY" in text

    def test_an_unknown_source_exits_one(self):
        assert run_cli("explain", "nope").exit_code == 1
