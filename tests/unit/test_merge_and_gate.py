"""Adapters, identity, conflict policies, the gate, and a guard on the corpus."""

from __future__ import annotations

import pytest

from multi_source_agg.adapters import (
    MODEL_FIELDS,
    ceiling,
    from_csv,
    from_html,
    from_json,
    from_xml,
    parse,
)
from multi_source_agg.aggregate import payload, run
from multi_source_agg.config import Settings, provenance, what_is_never_measured
from multi_source_agg.gate import check
from multi_source_agg.merge import (
    compare_identities,
    compare_policies,
    policy_requirements,
    resolve,
    union,
)
from multi_source_agg.models import (
    POLICY_NOTES,
    ConflictPolicy,
    Identity,
    Loss,
    SourceOutcome,
)

from ..conftest import CORPUS, claim


class TestAdaptersParseFourFormats:
    def test_json(self):
        parsed = from_json("a", '[{"native_id": "1", "value": "9", "shared_key": "K"}]')
        assert parsed.fmt == "json"
        assert len(parsed.records) == 1
        assert parsed.records[0].shared_key == "K"

    def test_xml_reads_attributes_and_children(self):
        payload_xml = (
            '<records><record native_id="1"><value>9</value>'
            "<shared_key>K</shared_key></record></records>"
        )
        parsed = from_xml("a", payload_xml)
        assert len(parsed.records) == 1
        assert parsed.records[0].native_id == "1", "from an attribute"
        assert parsed.records[0].value == "9", "from a child element"

    def test_html_uses_the_header_row_for_field_names(self):
        html = (
            "<table><tr><th>native_id</th><th>value</th></tr>"
            "<tr><td>1</td><td>9</td></tr></table>"
        )
        parsed = from_html("a", html)
        assert len(parsed.records) == 1
        assert parsed.records[0].value == "9"

    def test_html_with_no_rows_gives_nothing(self):
        parsed = from_html("a", "<p>nothing here</p>")
        assert parsed.records == ()
        assert parsed.offered_fields == ()

    def test_html_skips_a_row_whose_cell_count_does_not_match_the_header(self):
        html = (
            "<table><tr><th>native_id</th><th>value</th></tr>"
            "<tr><td>1</td><td>9</td></tr>"
            "<tr><td>2</td></tr></table>"
        )
        assert len(from_html("a", html).records) == 1

    def test_csv(self):
        parsed = from_csv("a", "native_id,value\n1,9\n")
        assert len(parsed.records) == 1
        assert parsed.fmt == "csv"

    def test_an_unknown_format_is_refused(self):
        with pytest.raises(ValueError, match="no adapter for format"):
            parse("a", "yaml", "x: 1")

    def test_parse_dispatches_on_format(self):
        assert parse("a", "json", "[]").fmt == "json"

    def test_a_missing_confidence_stays_missing_rather_than_becoming_zero(self):
        parsed = from_json("a", '[{"native_id": "1", "value": "9"}]')
        assert parsed.records[0].confidence is None

    def test_an_empty_string_confidence_is_also_absence(self):
        parsed = from_csv("a", "native_id,value,confidence\n1,9,\n")
        assert parsed.records[0].confidence is None

    def test_a_real_confidence_survives(self):
        parsed = from_csv("a", "native_id,value,confidence\n1,9,0.5\n")
        assert parsed.records[0].confidence == 0.5

    def test_an_empty_shared_key_becomes_none_not_empty_string(self):
        parsed = from_csv("a", "native_id,value,shared_key\n1,9,\n")
        assert parsed.records[0].shared_key is None


class TestTheSharedModelIsACeiling:
    def test_a_field_outside_the_model_is_reported_as_dropped(self):
        parsed = from_json("a", '[{"native_id": "1", "value": "9", "warehouse": "eu"}]')
        assert parsed.dropped_fields == ("warehouse",)
        assert parsed.lossless is False
        assert "nowhere to go" in parsed.summary()
        assert "adapter succeeded and the information is gone" in parsed.summary()

    def test_a_payload_that_fits_says_so_without_claiming_the_format_is_safe(self):
        parsed = from_json("a", '[{"native_id": "1", "value": "9"}]')
        assert parsed.lossless
        assert "fact about this payload rather than about the format" in parsed.summary()

    def test_every_model_field_is_accepted(self):
        row = {f: "x" for f in MODEL_FIELDS if f != "confidence"}
        row["native_id"] = "1"
        del row["source"]
        parsed = from_json("a", __import__("json").dumps([row]))
        assert parsed.dropped_fields == ()

    def test_the_ceiling_aggregates_across_sources(self):
        a = from_json("a", '[{"native_id": "1", "value": "9", "warehouse": "eu"}]')
        b = from_json("b", '[{"native_id": "2", "value": "8", "tax": "std"}]')
        result = ceiling([a, b])
        assert result.all_dropped == ("tax", "warehouse")
        assert set(result.dropped_by_source) == {"a", "b"}
        assert "DocConsolidator" in result.summary()

    def test_no_ceiling_reached_says_so(self):
        a = from_json("a", '[{"native_id": "1", "value": "9"}]')
        assert "No ceiling was reached" in ceiling([a]).summary()

    def test_fields_only_one_source_offers_are_the_first_casualties(self):
        a = from_json("a", '[{"native_id": "1", "value": "9", "warehouse": "eu"}]')
        b = from_json("b", '[{"native_id": "2", "value": "8"}]')
        only_one = ceiling([a, b]).fields_only_one_source_offers
        assert "warehouse" in only_one
        assert "native_id" not in only_one, "both offer it"


class TestTheUnionDependsOnTheIdentityKey:
    @pytest.fixture
    def overlapping(self):
        return [
            claim("alpha", "1", native_id="A-1", shared_key="K-1"),
            claim("beta", "1", native_id="B-9", shared_key="K-1"),
            claim("beta", "1", native_id="B-8", shared_key="K-3"),
        ]

    def test_native_ids_never_merge_across_sources(self, overlapping):
        result = union(overlapping, Identity.NATIVE_ID)
        assert result.distinct == 3
        assert result.collapsed == 0

    def test_a_shared_key_merges_them(self, overlapping):
        result = union(overlapping, Identity.SHARED_KEY)
        assert result.distinct == 2
        assert result.collapsed == 1
        assert "collapse to 2" in result.summary()

    def test_no_scheme_means_no_count_of_things(self, overlapping):
        result = union(overlapping, Identity.NONE)
        assert result.distinct == 3
        assert "not a count of THINGS" in result.summary()

    def test_the_schemes_disagree_and_say_the_choice_is_a_decision(self, overlapping):
        comparison = compare_identities(overlapping)
        assert comparison.schemes_agree is False
        assert "answered by a decision about identity" in comparison.summary()
        assert comparison.totals[Identity.SHARED_KEY] == 2

    def test_when_there_is_no_overlap_agreement_is_about_the_data(self):
        distinct_rows = [
            claim("alpha", "1", native_id="A-1", shared_key="K-1"),
            claim("beta", "1", native_id="B-1", shared_key="K-2"),
        ]
        comparison = compare_identities(distinct_rows)
        assert comparison.schemes_agree
        assert "property of this data" in comparison.summary()

    def test_an_unkeyed_row_is_unmatched_not_unique(self):
        rows = [
            claim("alpha", "1", shared_key="K-1"),
            claim("beta", "1", shared_key=None),
        ]
        result = union(rows, Identity.SHARED_KEY)
        assert len(result.unkeyed) == 1
        assert "NOT the same as being unique, only unmatched" in result.summary()


class TestConflictPoliciesDisagree:
    def test_every_policy_declares_what_it_needs(self):
        assert set(POLICY_NOTES) == set(ConflictPolicy)
        assert len(policy_requirements()) == len(ConflictPolicy)

    def test_the_three_answer_producing_policies_split(self, timed_and_scored):
        latest = resolve(timed_and_scored, ConflictPolicy.LATEST_WINS)
        confident = resolve(timed_and_scored, ConflictPolicy.HIGHEST_CONFIDENCE)
        majority = resolve(timed_and_scored, ConflictPolicy.MAJORITY)
        assert latest.value == "41.50"
        assert confident.value == "42.00"
        assert majority.value == "42.00"
        assert len({latest.value, confident.value, majority.value}) == 2

    def test_the_comparison_reports_the_split(self, timed_and_scored):
        comparison = compare_policies(timed_and_scored)
        assert comparison.policies_agree is False
        assert comparison.distinct_answers == 2
        assert "2 different answers from one set of claims" in comparison.summary()
        assert "hidden the decision that produced it" in comparison.summary()

    def test_flag_for_review_is_the_only_one_that_invents_nothing(self, timed_and_scored):
        flagged = resolve(timed_and_scored, ConflictPolicy.FLAG_FOR_REVIEW)
        assert flagged.usable is True
        assert flagged.value is None
        assert "declines to pick" in flagged.detail

    def test_latest_wins_refuses_when_a_claim_has_no_timestamp(self):
        claims = [claim("a", "1", seen_at="2025-01-01"), claim("b", "2")]
        result = resolve(claims, ConflictPolicy.LATEST_WINS)
        assert result.usable is False
        assert "no 'latest'" in result.detail
        assert "silently exclude" in result.detail

    def test_highest_confidence_refuses_when_a_claim_has_no_confidence(self):
        claims = [claim("a", "1", confidence=0.9), claim("b", "2")]
        result = resolve(claims, ConflictPolicy.HIGHEST_CONFIDENCE)
        assert result.usable is False
        assert "would invent a ranking" in result.detail

    def test_majority_refuses_a_tie(self):
        claims = [claim("a", "1"), claim("b", "2")]
        result = resolve(claims, ConflictPolicy.MAJORITY)
        assert result.usable is False
        assert "no majority" in result.detail
        assert "whichever source replied first" in result.detail

    def test_majority_with_two_sources_warns_it_is_just_the_bigger_half(self):
        claims = [claim("a", "1"), claim("b", "1"), claim("c", "2")]
        result = resolve(claims[:2] + [claim("c", "2")], ConflictPolicy.MAJORITY)
        assert result.value == "1"
        assert "counts sources rather than weighing them" in result.detail

    def test_a_two_claim_majority_is_named_as_the_bigger_half(self):
        # One value twice, one once, from only two DISTINCT values but three claims is the
        # normal case; this is the sub-three-source case.
        claims = [claim("a", "1"), claim("b", "1")]
        result = resolve(claims, ConflictPolicy.MAJORITY)
        assert result.value == "1", "they agree, so the policy did not matter"
        assert "did not matter here" in result.detail

    def test_unanimous_claims_make_the_policy_irrelevant(self):
        claims = [claim("a", "1"), claim("b", "1"), claim("c", "1")]
        for policy in ConflictPolicy:
            result = resolve(claims, policy)
            assert result.value == "1"
            assert "how a badly chosen policy survives" in result.detail

    def test_no_claims_at_all(self):
        result = resolve([], ConflictPolicy.MAJORITY)
        assert result.usable is False
        assert "nothing to resolve" in result.detail

    def test_a_policy_that_cannot_run_counts_as_a_disagreement(self):
        """The defect this guards: comparing values alone missed this case.

        Only MAJORITY can run here -- one claim carries neither a timestamp nor a confidence --
        so the values trivially "agree" while the choice of policy decides everything.
        """
        claims = [
            claim("a", "1"),
            claim("b", "1", seen_at="2025-01-02", confidence=0.5),
            claim("c", "2", seen_at="2025-01-03", confidence=0.6),
        ]
        comparison = compare_policies(claims)
        assert comparison.distinct_answers == 1, "only one policy produced a value"
        assert comparison.policies_agree is False, "and yet the policy decides everything"
        assert comparison.policy_decides_the_outcome is True
        assert set(comparison.inoperable) == {
            ConflictPolicy.LATEST_WINS, ConflictPolicy.HIGHEST_CONFIDENCE
        }
        assert "Only 1 policy could produce an answer at all" in comparison.summary()
        assert "could not run here" in comparison.summary()
        assert "ruled out options somebody thought they had chosen between" in comparison.summary()

    def test_agreement_requires_every_policy_to_have_run(self):
        claims = [claim("a", "1", seen_at="2025-01-01", confidence=0.5),
                  claim("b", "1", seen_at="2025-01-02", confidence=0.6),
                  claim("c", "1", seen_at="2025-01-03", confidence=0.7)]
        comparison = compare_policies(claims)
        assert comparison.inoperable == ()
        assert comparison.policies_agree is True
        assert "Every policy could run" in comparison.summary()

    def test_when_nothing_can_run_it_says_so(self):
        claims = [claim("a", "1"), claim("b", "2")]
        comparison = compare_policies(claims)
        assert "do not carry what any of these strategies need" in comparison.summary()


class TestSettings:
    def test_the_identity_default_merges_across_sources(self):
        assert Settings().identity is Identity.SHARED_KEY

    def test_the_policy_default_declines_to_pick(self):
        assert Settings().policy is ConflictPolicy.FLAG_FOR_REVIEW

    def test_skip_is_a_comma_string_not_a_list_field(self):
        assert Settings(skip="a, b ,c").skipped == frozenset({"a", "b", "c"})

    def test_nothing_skipped_by_default(self):
        assert Settings().skipped == frozenset()

    def test_page_size_must_be_positive(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Settings(page_size=0)

    def test_the_manifest_path_is_built_from_the_data_dir(self):
        assert str(Settings(data_dir="/tmp/x").manifest_path) == "/tmp/x/manifest.json"

    def test_every_setting_that_changes_the_answer_has_a_provenance_entry(self):
        names = {name for name, _, _ in provenance()}
        assert {"identity", "policy", "page_size", "skip"} == names
        for _, source, risk in provenance():
            assert source.strip() and risk.strip()

    def test_identity_is_labelled_never_discovered(self):
        entry = next(p for p in provenance() if p[0] == "identity")
        assert "never discovered" in entry[1]

    def test_what_is_never_measured_names_the_denominator(self):
        lines = " ".join(what_is_never_measured())
        assert "at most unbounded" in lines
        assert "come from the source" in lines


class TestTheGateOnlyFailsOnWhatASourceOwnerCanFix:
    def test_the_corpus_fails_on_its_planted_source_defects(self, corpus_settings):
        gate = check(run(corpus_settings))
        assert not gate.passed
        headlines = " ".join(h for h, _ in gate.failures)
        assert "offset paging over a collection that changed mid-read" in headlines
        assert "declared 4 records and delivered 3" in headlines

    def test_it_reports_and_does_not_fail_on_what_nobody_can_fix(self, corpus_settings):
        gate = check(run(corpus_settings))
        headlines = " ".join(h for h, _ in gate.reported)
        assert "UNKNOWN number of records" in headlines
        assert "nowhere to go in the shared model" in headlines
        assert "depends on the identity scheme" in headlines

    def test_a_failed_source_is_reported_never_gated(self, corpus_settings):
        gate = check(run(corpus_settings))
        detail = " ".join(d for h, d in gate.reported if "UNKNOWN" in h)
        assert "Nobody can fix a source being down" in detail
        assert "cannot fix" not in " ".join(h for h, _ in gate.failures)

    def test_keyset_paging_losing_a_new_row_is_not_a_defect(self, corpus_settings):
        gate = check(run(corpus_settings))
        entry = next((d for h, d in gate.reported if "paged by key" in h), None)
        assert entry is not None
        assert "Not a defect" in entry
        assert "no instant at which the read could have caught it" in entry

    def test_policy_sensitivity_is_an_operators_problem(self, corpus_settings):
        gate = check(run(corpus_settings))
        entry = next((d for h, d in gate.reported if "conflict policy decides" in h), None)
        assert entry is not None
        assert "no source owner can fix it" in entry

    def test_keyset_paging_alone_does_not_fail_the_gate(self, corpus_settings):
        gate = check(run(corpus_settings))
        assert not any("beta" in h for h, _ in gate.failures), "beta pages by key"

    def test_the_fail_summary_says_what_it_gated_on(self, corpus_settings):
        gate = check(run(corpus_settings))
        assert "a source owner can fix at the source" in gate.summary()
        assert "reported and NOT failed on" in gate.summary()

    def test_an_unkeyed_record_fails_because_the_source_can_emit_the_key(self, corpus_settings):
        # gamma's rows all carry K-*, so force the failure with a scheme nothing satisfies.
        agg = run(corpus_settings)
        agg.identity = Identity.SHARED_KEY
        stripped = [r.model_copy(update={"shared_key": None}) for r in agg.records]
        agg.records = stripped
        gate = check(agg)
        assert any("carry no SHARED_KEY" in h for h, _ in gate.failures)

    def test_the_none_identity_cannot_produce_an_unkeyed_failure(self, corpus_settings):
        agg = run(corpus_settings.model_copy(update={"identity": Identity.NONE}))
        gate = check(agg)
        assert not any("carry no" in h for h, _ in gate.failures)


class TestTheCorpusStillProducesEveryOutcome:
    """If the corpus stops planting these, assertions elsewhere go vacuous."""

    def test_the_manifest_and_its_payloads_are_the_only_files(self):
        import json

        manifest = json.loads((CORPUS / "manifest.json").read_text())
        expected = {"manifest.json"} | {s["file"] for s in manifest["sources"]}
        on_disk = {
            p.name for p in CORPUS.iterdir()
            if p.suffix in {".json", ".xml", ".html", ".csv"}
        }
        assert on_disk == expected

    def test_every_source_outcome_appears(self, corpus_settings):
        counts = payload(run(corpus_settings))["outcomes"]
        assert all(counts[o.value] > 0 for o in SourceOutcome), counts

    def test_every_loss_mode_appears(self, corpus_settings):
        losses = set(payload(run(corpus_settings))["lossy_reads"].values())
        assert losses == {Loss.MISSED.value, Loss.DUPLICATED.value, Loss.UNSEEN_INSERT.value}

    def test_all_four_formats_are_exercised(self, corpus_settings):
        assert payload(run(corpus_settings))["formats"] == ["csv", "html", "json", "xml"]

    def test_the_total_is_a_floor_not_a_total(self, corpus_settings):
        result = payload(run(corpus_settings))
        assert result["total_is_knowable"] is False
        assert len(result["unknown_contributors"]) == 3

    def test_exactly_one_source_contradicts_its_own_declared_total(self, corpus_settings):
        assert payload(run(corpus_settings))["disagreeing_declared_totals"] == ["gamma"]

    def test_one_read_is_unrepairable_and_one_is_repairable(self, corpus_settings):
        result = payload(run(corpus_settings))
        assert result["unrepairable_reads"] == ["alpha"]
        assert result["dedup_repairable"] == ["delta"]

    def test_the_identity_schemes_disagree(self, corpus_settings):
        counts = payload(run(corpus_settings))["distinct_by_identity"]
        assert len(set(counts.values())) > 1, counts
        assert counts["SHARED_KEY"] < counts["NATIVE_ID"]

    def test_three_sources_offer_a_field_the_model_cannot_hold(self, corpus_settings):
        result = payload(run(corpus_settings))
        assert result["dropped_fields"] == ["supplier_note", "tax_band", "warehouse_region"]
        assert len(result["dropped_by_source"]) == 3

    def test_contested_entities_exist_and_are_policy_sensitive(self, corpus_settings):
        result = payload(run(corpus_settings))
        assert result["contested_entities"] == ["K-3", "K-7"]
        assert result["policy_sensitive"] == ["K-3", "K-7"]

    def test_each_contested_entity_is_answerable_by_a_different_policy(self, corpus_settings):
        """The sharpest result: the policy decides WHICH entities you can answer about."""
        agg = run(corpus_settings)
        operable = {}
        for key, claims in sorted(agg.contested().items()):
            comparison = compare_policies(claims)
            operable[key] = {
                r.policy for r in comparison.resolutions
                if r.usable and r.value is not None
            }
        assert operable["K-3"] == {ConflictPolicy.MAJORITY}
        assert operable["K-7"] == {ConflictPolicy.LATEST_WINS}
        assert operable["K-3"] != operable["K-7"]

    def test_skipping_a_source_makes_it_never_asked(self, corpus_settings):
        result = payload(run(corpus_settings.model_copy(update={"skip": "alpha"})))
        assert result["outcomes"][SourceOutcome.NEVER_ASKED.value] == 2
        assert "alpha" in result["unknown_contributors"]

    def test_the_headline_never_gives_a_bare_count(self, corpus_settings):
        headline = run(corpus_settings).headline()
        assert "FLOOR, not a total" in headline
        assert "a different identity scheme gives a different number" in headline
        assert "no deduplication can recover" in headline

    def test_the_headline_distinguishes_the_two_populations(self, corpus_settings):
        """The coverage count and the union row count differ, and the headline says why."""
        result = payload(run(corpus_settings))
        assert result["records"] == 13
        assert result["union_rows"] == 20
        assert result["union_rows"] > result["records"]
        headline = run(corpus_settings).headline()
        assert "20 record(s) reached the union" in headline
        assert "a TRUNCATED source's records are real" in headline
        assert "its count is only a floor" in headline
