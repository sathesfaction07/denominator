"""Paging, models, and the fan-out -- where 'complete' loses its meaning."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from multi_source_agg.fanout import (
    DeclaredTotalCheck,
    check_declared_totals,
    coverage,
    disagreeing_totals,
    empty_is_not_failure,
    outcome_counts,
    unknown_contributors,
)
from multi_source_agg.models import (
    LOSS_NOTES,
    OUTCOME_NOTES,
    PAGING_NOTES,
    Identity,
    Loss,
    Mutation,
    Paging,
    ReadResult,
    Record,
    SourceOutcome,
    SourceReport,
)
from multi_source_agg.paging import (
    apply_mutation,
    compare_schemes,
    completeness_has_no_denominator,
    dedup_verdict,
    keyset_read,
    offset_read,
    read,
    single_read,
    why_retry_makes_it_worse,
)


class TestEnumsCarryTheirExplanations:
    def test_every_paging_scheme_is_described(self):
        assert set(PAGING_NOTES) == set(Paging)
        for scheme in Paging:
            what, note = PAGING_NOTES[scheme]
            assert what.strip() and note.strip()

    def test_every_loss_is_described(self):
        assert set(LOSS_NOTES) == set(Loss)
        assert all(v.strip() for v in LOSS_NOTES.values())

    def test_every_outcome_says_whether_its_count_counts(self):
        assert set(OUTCOME_NOTES) == set(SourceOutcome)
        counting = {o for o in SourceOutcome if OUTCOME_NOTES[o][1]}
        assert counting == {SourceOutcome.ANSWERED, SourceOutcome.EMPTY}

    def test_duplication_is_named_as_the_repairable_one(self):
        assert "Deduplication repairs this one" in LOSS_NOTES[Loss.DUPLICATED]
        assert "cannot invent it" in LOSS_NOTES[Loss.MISSED]

    def test_offset_paging_declares_both_of_its_failures(self):
        _, note = PAGING_NOTES[Paging.OFFSET]
        assert "read TWICE" in note
        assert "NEVER read" in note

    def test_keyset_admits_what_it_still_cannot_do(self):
        _, note = PAGING_NOTES[Paging.KEYSET]
        assert "Never shifts" in note
        assert "never seen" in note


class TestOffsetPagingOverChangingData:
    """The finding. Two opposite failures, both from well-formed pages."""

    def test_nothing_changes_and_nothing_is_lost(self, rows):
        result = read("s", rows, Paging.OFFSET, 3, Mutation.NONE)
        assert result.rows_read == 10
        assert result.loss is Loss.CLEAN

    def test_an_insert_before_the_cursor_duplicates_a_row(self, rows):
        result = read("s", rows, Paging.OFFSET, 3, Mutation.INSERT_BEHIND)
        assert result.rows_read == 11
        assert result.distinct_read == 10
        assert result.duplicated == ("r03",)
        assert result.missed == ()
        assert result.loss is Loss.DUPLICATED

    def test_a_delete_before_the_cursor_loses_a_row_entirely(self, rows):
        result = read("s", rows, Paging.OFFSET, 3, Mutation.DELETE_BEHIND)
        assert result.rows_read == 9
        assert result.distinct_read == 9
        assert result.missed == ("r04",)
        assert result.duplicated == ()
        assert result.loss is Loss.MISSED

    def test_the_two_failures_are_opposite(self, rows):
        inserted = read("s", rows, Paging.OFFSET, 3, Mutation.INSERT_BEHIND)
        deleted = read("s", rows, Paging.OFFSET, 3, Mutation.DELETE_BEHIND)
        assert inserted.rows_read > 10 > deleted.rows_read
        assert bool(inserted.duplicated) != bool(inserted.missed)
        assert bool(deleted.missed) != bool(deleted.duplicated)

    def test_an_insert_ahead_of_the_cursor_is_simply_seen(self, rows):
        result = read("s", rows, Paging.OFFSET, 3, Mutation.INSERT_AHEAD)
        assert result.loss is Loss.CLEAN
        assert result.rows_read == 11

    def test_the_page_size_changes_which_row_is_lost(self, rows):
        by_three = read("s", rows, Paging.OFFSET, 3, Mutation.DELETE_BEHIND)
        by_four = read("s", rows, Paging.OFFSET, 4, Mutation.DELETE_BEHIND)
        assert by_three.missed == ("r04",)
        assert by_four.missed == ("r05",)
        assert by_three.missed != by_four.missed


class TestDedupRepairsOneFailureAndNotTheOther:
    def test_deduplication_recovers_the_right_count_after_a_duplicate(self, rows):
        result = read("s", rows, Paging.OFFSET, 3, Mutation.INSERT_BEHIND)
        assert result.dedup_repairs_it is True
        assert result.distinct_read == 10, "which is the correct number of original rows"
        assert "repairs this failure completely" in dedup_verdict(result)

    def test_deduplication_cannot_invent_a_row_that_was_never_read(self, rows):
        result = read("s", rows, Paging.OFFSET, 3, Mutation.DELETE_BEHIND)
        assert result.dedup_repairs_it is False
        assert "cannot invent them" in dedup_verdict(result)
        assert "looks exactly as plausible" in dedup_verdict(result)

    def test_a_clean_read_says_dedup_would_change_nothing(self, rows):
        result = read("s", rows, Paging.OFFSET, 3, Mutation.NONE)
        assert "would change nothing" in dedup_verdict(result)
        assert "not a guarantee about the scheme" in dedup_verdict(result)

    def test_an_unseen_insert_is_not_repairable_and_should_not_be(self, rows):
        result = read("s", rows, Paging.KEYSET, 3, Mutation.INSERT_BEHIND)
        assert result.loss is Loss.UNSEEN_INSERT
        assert "Nothing repairs this, and nothing should" in dedup_verdict(result)


class TestKeysetPagingIsBetterAndStillNotComplete:
    def test_it_survives_a_deletion_that_offset_paging_does_not(self, rows):
        keyset = read("s", rows, Paging.KEYSET, 3, Mutation.DELETE_BEHIND)
        offset = read("s", rows, Paging.OFFSET, 3, Mutation.DELETE_BEHIND)
        assert keyset.loss is Loss.CLEAN
        assert offset.loss is Loss.MISSED

    def test_it_never_duplicates(self, rows):
        for mutation in Mutation:
            assert read("s", rows, Paging.KEYSET, 3, mutation).duplicated == ()

    def test_a_row_inserted_behind_the_cursor_is_never_seen(self, rows):
        result = read("s", rows, Paging.KEYSET, 3, Mutation.INSERT_BEHIND)
        assert result.unseen_inserts == ("r00",)
        assert result.missed == (), "no pre-existing row was lost"
        assert result.loss is Loss.UNSEEN_INSERT

    def test_a_row_inserted_ahead_of_the_cursor_is_seen(self, rows):
        result = read("s", rows, Paging.KEYSET, 3, Mutation.INSERT_AHEAD)
        assert result.unseen_inserts == ()
        assert result.rows_read == 11

    def test_no_paging_is_the_only_unambiguously_complete_scheme(self, rows):
        for mutation in Mutation:
            result = read("s", rows, Paging.NONE, 3, mutation)
            assert result.loss is Loss.CLEAN
            assert result.rows_read == 10
            assert result.pages == 1

    def test_the_schemes_disagree_about_what_was_lost(self, rows):
        for mutation in (Mutation.INSERT_BEHIND, Mutation.DELETE_BEHIND):
            comparison = compare_schemes(rows, mutation)
            assert comparison.schemes_agree is False
            assert "decides which records exist" in comparison.summary()

    def test_and_agree_when_nothing_happened(self, rows):
        comparison = compare_schemes(rows, Mutation.NONE)
        assert comparison.schemes_agree
        assert "property of this mutation" in comparison.summary()

    def test_completeness_is_named_as_undefined(self):
        note = completeness_has_no_denominator()
        assert "cannot count what exists" in note
        assert "snapshot that never existed" in note

    def test_retry_is_named_as_the_cause_rather_than_the_cure(self):
        note = why_retry_makes_it_worse(9)
        assert "offset 9" in note
        assert "not a repair" in note
        assert "gone stale" in note


class TestTheReadersThemselves:
    def test_offset_read_returns_what_was_seen_and_the_final_collection(self, rows):
        seen, after = offset_read(rows, 3, Mutation.DELETE_BEHIND)
        assert len(seen) == 9
        assert len(after) == 9
        assert rows == [f"r{i:02d}" for i in range(1, 11)], "the input must not be mutated"

    def test_keyset_read_sorts_so_input_order_does_not_matter(self, rows):
        forward, _ = keyset_read(rows, 3, Mutation.NONE)
        backward, _ = keyset_read(list(reversed(rows)), 3, Mutation.NONE)
        assert forward == backward

    def test_single_read_ignores_the_mutation_because_there_is_no_window(self, rows):
        for mutation in Mutation:
            seen, _ = single_read(rows, 3, mutation)
            assert seen == rows

    @pytest.mark.parametrize(
        "mutation,expected_len", [(Mutation.NONE, 10), (Mutation.INSERT_BEHIND, 11),
                                  (Mutation.INSERT_AHEAD, 11), (Mutation.DELETE_BEHIND, 9)]
    )
    def test_apply_mutation_changes_the_collection_in_place(self, rows, mutation, expected_len):
        apply_mutation(rows, mutation)
        assert len(rows) == expected_len

    def test_deleting_from_an_empty_collection_does_not_raise(self):
        empty: list[str] = []
        apply_mutation(empty, Mutation.DELETE_BEHIND)
        assert empty == []

    def test_an_empty_collection_reads_as_empty(self):
        for scheme in Paging:
            result = read("s", [], scheme, 3, Mutation.NONE)
            assert result.rows_read == 0
            assert result.loss is Loss.CLEAN


class TestReadResultRefusesToMisdescribeItself:
    def test_distinct_cannot_exceed_rows(self):
        with pytest.raises(ValidationError, match="more distinct rows than rows"):
            ReadResult(source="s", paging=Paging.OFFSET, rows_read=3, distinct_read=4,
                       stable_rows=3)

    def test_a_duplicate_requires_distinct_to_be_lower(self):
        with pytest.raises(ValidationError, match="distinct_read must be below"):
            ReadResult(source="s", paging=Paging.OFFSET, rows_read=3, distinct_read=3,
                       stable_rows=3, duplicated=("r01",))

    def test_loss_reports_the_worst_thing_that_happened(self):
        both = ReadResult(source="s", paging=Paging.OFFSET, rows_read=3, distinct_read=2,
                          stable_rows=3, missed=("r09",), duplicated=("r01",))
        assert both.loss is Loss.MISSED, "the unrepairable one outranks the repairable one"

    def test_negative_counts_are_refused(self):
        with pytest.raises(ValidationError):
            ReadResult(source="s", paging=Paging.NONE, rows_read=-1, distinct_read=0,
                       stable_rows=0)


class TestSourceReportRefusesToOverstate:
    @pytest.mark.parametrize("outcome", [SourceOutcome.FAILED, SourceOutcome.NEVER_ASKED])
    def test_a_silent_source_cannot_carry_a_record_count(self, outcome):
        with pytest.raises(ValidationError, match="cannot carry a record count"):
            SourceReport(source="s", outcome=outcome, records=5)

    def test_an_empty_source_can_carry_a_zero(self):
        report = SourceReport(source="s", outcome=SourceOutcome.EMPTY, records=0)
        assert report.count_is_meaningful is True

    @pytest.mark.parametrize(
        "outcome,unknown",
        [(SourceOutcome.ANSWERED, False), (SourceOutcome.EMPTY, False),
         (SourceOutcome.TRUNCATED, True), (SourceOutcome.FAILED, True),
         (SourceOutcome.NEVER_ASKED, True)],
    )
    def test_which_outcomes_make_the_total_unknowable(self, outcome, unknown):
        report = SourceReport(source="s", outcome=outcome)
        assert report.contributes_unknown is unknown

    def test_a_truncated_count_is_not_meaningful_even_though_it_is_nonzero(self):
        report = SourceReport(source="s", outcome=SourceOutcome.TRUNCATED, records=7)
        assert report.records == 7
        assert report.count_is_meaningful is False


class TestFanoutHasNoDenominator:
    @pytest.fixture
    def mixed(self) -> list[SourceReport]:
        return [
            SourceReport(source="alpha", outcome=SourceOutcome.ANSWERED, records=120,
                         declared_total=120),
            SourceReport(source="beta", outcome=SourceOutcome.ANSWERED, records=80),
            SourceReport(source="gamma", outcome=SourceOutcome.ANSWERED, records=45,
                         declared_total=50),
            SourceReport(source="delta", outcome=SourceOutcome.FAILED),
        ]

    def test_the_headline_calls_the_number_a_floor(self, mixed):
        cov = coverage(mixed)
        assert cov.records == 245
        assert cov.total_is_knowable is False
        assert "FLOOR, not a total" in cov.headline()
        assert "at least 0, at most unbounded" in cov.headline()

    def test_with_everyone_answering_it_is_a_total(self):
        clean = [
            SourceReport(source="a", outcome=SourceOutcome.ANSWERED, records=3),
            SourceReport(source="b", outcome=SourceOutcome.EMPTY, records=0),
        ]
        cov = coverage(clean)
        assert cov.total_is_knowable is True
        assert "This is a total" in cov.headline()
        assert "not as of any single instant" in cov.headline()

    def test_a_failed_source_contributes_unknown_not_zero(self, mixed):
        names = [name for name, _ in unknown_contributors(mixed)]
        assert names == ["delta"]

    def test_every_kind_of_silence_is_counted_separately(self):
        reports = [SourceReport(source=o.value, outcome=o) for o in SourceOutcome]
        counts = outcome_counts(reports)
        assert set(counts) == set(SourceOutcome)
        assert all(v == 1 for v in counts.values())

    def test_never_asked_is_excluded_from_asked(self):
        reports = [
            SourceReport(source="a", outcome=SourceOutcome.ANSWERED, records=1),
            SourceReport(source="b", outcome=SourceOutcome.NEVER_ASKED),
        ]
        assert coverage(reports).asked == 1

    def test_empty_and_failed_are_distinguished_in_prose(self):
        note = empty_is_not_failure()
        assert "opposite things" in note
        assert "placeholder for a number nobody knows" in note


class TestASourcesOwnCountOfItself:
    def test_agreement_is_weak_evidence_and_says_so(self):
        verdict = DeclaredTotalCheck(source="a", declared=10, delivered=10)
        assert verdict.agrees is True
        assert "WEAK evidence" in verdict.verdict()
        assert "undercounts itself" in verdict.verdict()

    def test_disagreement_is_strong_evidence_of_nothing_in_particular(self):
        verdict = DeclaredTotalCheck(source="a", declared=10, delivered=9)
        assert verdict.agrees is False
        assert "STRONG evidence" in verdict.verdict()
        assert "no evidence at all about which number to trust" in verdict.verdict()

    def test_delivering_more_than_declared_is_reported_too(self):
        verdict = DeclaredTotalCheck(source="a", declared=9, delivered=10)
        assert "1 more" in verdict.verdict()

    def test_no_declared_total_is_not_a_defect(self):
        verdict = DeclaredTotalCheck(source="a", declared=None, delivered=9)
        assert verdict.agrees is None
        assert "Not a defect" in verdict.verdict()

    def test_only_meaningful_counts_are_checked(self):
        reports = [
            SourceReport(source="ok", outcome=SourceOutcome.ANSWERED, records=3,
                         declared_total=4),
            SourceReport(source="down", outcome=SourceOutcome.FAILED, declared_total=99),
        ]
        checked = [c.source for c in check_declared_totals(reports)]
        assert checked == ["ok"], "a failed source's declared total cannot be checked"
        assert disagreeing_totals(reports) == ("ok",)


class TestRecordIdentity:
    def test_native_id_is_scoped_to_its_source(self):
        a = Record(source="alpha", native_id="1", field_name="f", value="v")
        b = Record(source="beta", native_id="1", field_name="f", value="v")
        assert a.key(Identity.NATIVE_ID) != b.key(Identity.NATIVE_ID)

    def test_a_shared_key_crosses_sources(self):
        a = Record(source="alpha", native_id="1", shared_key="K", field_name="f", value="v")
        b = Record(source="beta", native_id="2", shared_key="K", field_name="f", value="v")
        assert a.key(Identity.SHARED_KEY) == b.key(Identity.SHARED_KEY) == "K"

    def test_a_record_with_no_shared_key_has_no_identity_under_that_scheme(self):
        r = Record(source="alpha", native_id="1", field_name="f", value="v")
        assert r.key(Identity.SHARED_KEY) is None

    def test_the_none_scheme_gives_nothing_an_identity(self):
        r = Record(source="alpha", native_id="1", shared_key="K", field_name="f", value="v")
        assert r.key(Identity.NONE) is None

    def test_confidence_is_bounded(self):
        with pytest.raises(ValidationError):
            Record(source="a", native_id="1", field_name="f", value="v", confidence=1.5)
        with pytest.raises(ValidationError):
            Record(source="a", native_id="1", field_name="f", value="v", confidence=-0.1)

    def test_records_are_frozen(self):
        r = Record(source="a", native_id="1", field_name="f", value="v")
        with pytest.raises(ValidationError):
            r.value = "x"
