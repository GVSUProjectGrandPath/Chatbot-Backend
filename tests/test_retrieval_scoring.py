"""Unit tests for the retrieval scorer's matching rules.

These cover the grading logic itself, not retrieval quality: multi-label golden rows, the
relabel-from-CSV step that lets a corrected label be re-scored without paying for a new run,
(module, lesson) pair matching, and the cohort/type partitions added when the set grew to 200.
"""

import csv

import pytest

from tests.score_retrieval_eval import (
    accepted,
    accepted_pairs,
    annotate,
    check_label_pairs,
    first_hit_rank,
    load_golden_labels,
    load_golden_meta,
    noise_questions,
    per_module,
    print_diff_table,
    relabel,
    score_level,
    subsets,
)


def result(rank: int, lesson: str, module: str) -> dict:
    return {"rank": rank, "score": 0.03, "lesson": lesson, "module": module, "text": ""}


# A row whose correct lesson sits at rank 2, plus an alternative acceptable lesson at rank 1.
RESULTS = [
    result(1, "Give Yourself Grace", "Money Management"),
    result(2, "Cycle of Socialization", "Money Mindset"),
    result(3, "Money and Relationships", "Money Mindset"),
]


class TestAccepted:
    def test_single_label(self):
        assert accepted("Cycle of Socialization", "lesson") == {"cycle of socialization"}

    def test_pipe_separated_labels_all_count(self):
        assert accepted("Cycle of Socialization|Give Yourself Grace", "lesson") == {
            "cycle of socialization",
            "give yourself grace",
        }

    def test_module_labels_drop_the_numeric_prefix(self):
        # Result rows carry the bare module name, golden rows carry "Module 3: ...".
        assert accepted("Module 1: Money Mindset|Module 3: Money Management", "module") == {
            "money mindset",
            "money management",
        }

    def test_blank_and_stray_separators_are_ignored(self):
        assert accepted("|Cycle of Socialization|", "lesson") == {"cycle of socialization"}

    def test_empty_cell_accepts_nothing(self):
        assert accepted("", "lesson") == set()


class TestFirstHitRank:
    def test_single_label_finds_its_own_rank(self):
        assert first_hit_rank(RESULTS, "Cycle of Socialization", "lesson") == 2

    def test_multi_label_takes_the_earliest_matching_alternative(self):
        rank = first_hit_rank(RESULTS, "Cycle of Socialization|Give Yourself Grace", "lesson")
        assert rank == 1

    def test_missing_lesson_returns_none(self):
        assert first_hit_rank(RESULTS, "Selecting a Financial Institution", "lesson") is None

    def test_matching_is_case_and_whitespace_insensitive(self):
        assert first_hit_rank(RESULTS, "  cycle of SOCIALIZATION  ", "lesson") == 2

    def test_module_level_matching(self):
        assert first_hit_rank(RESULTS, "Module 1: Money Mindset", "module") == 2

    def test_empty_results_return_none(self):
        assert first_hit_rank([], "Cycle of Socialization", "lesson") is None


class TestScoreLevel:
    def test_multi_label_row_scores_as_a_top_1_hit(self):
        records = [
            {
                "question": "q",
                "expected_module": "Module 1: Money Mindset|Module 3: Money Management",
                "expected_lesson": "Cycle of Socialization|Give Yourself Grace",
                "results": RESULTS,
            }
        ]
        out = score_level(records, "lesson")
        assert out["hit@1"] == {"hits": 1, "n": 1, "pct": 100.0}
        assert out["mrr"] == 1.0

    def test_row_with_no_results_counts_as_a_miss_not_a_crash(self):
        records = [
            {
                "question": "q",
                "expected_module": "Module 1: Money Mindset",
                "expected_lesson": "Cycle of Socialization",
                "results": [],
            }
        ]
        out = score_level(records, "lesson")
        assert out["hit@10"]["hits"] == 0
        assert out["mrr"] == 0.0


class TestPerModule:
    def test_multi_label_row_is_filed_under_its_first_module(self):
        """Otherwise the pipe-joined label shows up as its own bogus module in the breakdown."""
        records = [
            {
                "question": "q",
                "expected_module": "Module 1: Money Mindset|Module 3: Money Management",
                "expected_lesson": "Cycle of Socialization|Give Yourself Grace",
                "results": RESULTS,
            }
        ]
        assert list(per_module(records)) == ["Module 1: Money Mindset"]


class TestPairMatching:
    """ "Know Your Rights" is a lesson in BOTH Module 3 and Module 4.

    Matching on the lesson name alone scored a Module 3 question as a hit when retrieval returned
    Module 4's lesson. These pin the fix.
    """

    KYR_M4 = [result(1, "Know Your Rights", "Navigating Credit")]
    KYR_M3 = [result(1, "Know Your Rights", "Money Management")]

    def test_same_named_lesson_in_the_wrong_module_is_not_a_hit(self):
        rank = first_hit_rank(
            self.KYR_M4,
            "Know Your Rights",
            "lesson",
            expected_module="Module 3: Money Management",
        )
        assert rank is None

    def test_same_named_lesson_in_the_right_module_is_a_hit(self):
        rank = first_hit_rank(
            self.KYR_M3,
            "Know Your Rights",
            "lesson",
            expected_module="Module 3: Money Management",
        )
        assert rank == 1

    def test_without_a_module_it_falls_back_to_name_only_matching(self):
        assert first_hit_rank(self.KYR_M4, "Know Your Rights", "lesson") == 1

    def test_pairs_are_the_cross_product_of_both_cells(self):
        pairs = accepted_pairs(
            "Module 1: Money Mindset|Module 3: Money Management",
            "Cycle of Socialization|Give Yourself Grace",
        )
        assert ("money mindset", "cycle of socialization") in pairs
        assert ("money management", "give yourself grace") in pairs
        assert len(pairs) == 4

    def test_multi_label_row_still_matches_its_alternative(self):
        records = [
            {
                "question": "q",
                "expected_module": "Module 1: Money Mindset|Module 3: Money Management",
                "expected_lesson": "Cycle of Socialization|Give Yourself Grace",
                "results": RESULTS,
            }
        ]
        assert score_level(records, "lesson")["hit@1"]["hits"] == 1


class TestCheckLabelPairs:
    """A wrong module label is an automatic miss under pair matching, so it must be reported."""

    def test_pair_that_does_not_exist_in_the_index_is_flagged(self):
        records = [
            {
                "question": "a mislabelled row",
                "expected_module": "Module 1: Money Mindset",
                "expected_lesson": "Income Taxes",
                "results": [],
            }
        ]
        assert len(check_label_pairs(records)) == 1

    def test_real_pair_is_not_flagged(self):
        records = [
            {
                "question": "a correct row",
                "expected_module": "Module 3: Money Management",
                "expected_lesson": "Income Taxes",
                "results": [],
            }
        ]
        assert check_label_pairs(records) == []


class TestNoiseThreshold:
    """A fixed 2-question floor was right at n=27 and far too tight at n=200."""

    def test_small_set_keeps_the_two_question_floor(self):
        assert noise_questions(27, 81.5) == 2

    def test_threshold_grows_with_the_set(self):
        assert noise_questions(200, 81.5) == 5

    def test_never_drops_below_the_floor(self):
        assert noise_questions(200, 100.0) == 2
        assert noise_questions(0, 80.0) == 2


class TestDiffTable:
    """Verdicts must not compare raw hit counts across sets of different sizes."""

    def test_identical_rate_at_a_different_n_is_not_a_regression(self, capsys):
        # 27/27 vs 200/200 is the same 100% rate but a 173-question difference in counts.
        print_diff_table(
            {"module.hit@10": (100.0, 200, 200)},
            {"module.hit@10": (100.0, 27, 27)},
        )
        assert "within noise" in capsys.readouterr().out

    def test_real_movement_at_the_same_n_is_still_flagged(self, capsys):
        print_diff_table(
            {"lesson.hit@1": (55.5, 111, 200)},
            {"lesson.hit@1": (81.5, 163, 200)},
        )
        assert "REGRESSION" in capsys.readouterr().out


class TestSubsets:
    RECORDS = [
        {
            "question": "old",
            "cohort": "core27",
            "type": "standard",
            "expected_module": "Module 1: Money Mindset",
            "expected_lesson": "Cycle of Socialization",
            "results": RESULTS,
        },
        {
            "question": "new",
            "cohort": "phase6",
            "type": "vague",
            "expected_module": "Module 1: Money Mindset",
            "expected_lesson": "Cycle of Socialization",
            "results": [],
        },
    ]

    def test_cohorts_are_scored_separately(self):
        out = subsets(self.RECORDS)["by_cohort"]
        assert out["core27"]["n"] == 1
        assert out["core27"]["lesson"]["hit@5"]["hits"] == 1
        # The phase6 row has no results, so it misses — the point is it does not drag core27 down.
        assert out["phase6"]["lesson"]["hit@5"]["hits"] == 0

    def test_hard_types_are_broken_out(self):
        out = subsets(self.RECORDS)["by_type"]
        assert set(out) == {"standard", "vague"}
        assert out["vague"]["n"] == 1

    def test_records_without_metadata_fall_back(self):
        out = subsets([dict(self.RECORDS[0], cohort=None, type=None) | {"cohort": "unknown"}])
        assert "unknown" in out["by_cohort"]


class TestGoldenMeta:
    @pytest.fixture
    def meta_csv(self, tmp_path):
        path = tmp_path / "golden.csv"
        with path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["question", "module", "lesson", "cohort", "type"])
            w.writeheader()
            w.writerow(
                {
                    "question": "tagged",
                    "module": "Module 1: Money Mindset",
                    "lesson": "Cycle of Socialization",
                    "cohort": "phase6",
                    "type": "typo",
                }
            )
        return path

    def test_meta_is_read_from_the_csv(self, meta_csv):
        assert load_golden_meta(meta_csv)["tagged"] == ("phase6", "typo")

    def test_annotate_attaches_cohort_and_type(self, meta_csv):
        records = [{"question": "tagged"}]
        annotate(records, meta_csv)
        assert (records[0]["cohort"], records[0]["type"]) == ("phase6", "typo")

    def test_row_absent_from_the_csv_gets_defaults(self, meta_csv):
        records = [{"question": "not in the csv"}]
        annotate(records, meta_csv)
        assert (records[0]["cohort"], records[0]["type"]) == ("unknown", "standard")


class TestRelabel:
    @pytest.fixture
    def golden_csv(self, tmp_path):
        path = tmp_path / "golden.csv"
        with path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["question", "expected_answer", "module", "lesson"])
            w.writeheader()
            w.writerow(
                {
                    "question": "Why do I feel bad?",
                    "expected_answer": "…",
                    "module": "Module 1: Money Mindset",
                    "lesson": "Cycle of Socialization|Give Yourself Grace",
                }
            )
        return path

    def test_loads_labels_keyed_by_question(self, golden_csv):
        labels = load_golden_labels(golden_csv)
        assert labels["Why do I feel bad?"] == (
            "Module 1: Money Mindset",
            "Cycle of Socialization|Give Yourself Grace",
        )

    def test_stale_dump_labels_are_replaced_and_counted(self, golden_csv):
        records = [
            {
                "question": "Why do I feel bad?",
                "expected_module": "Module 1: Money Mindset",
                "expected_lesson": "Introduction to Money Mindsets",
                "results": RESULTS,
            }
        ]
        assert relabel(records, golden_csv) == 1
        assert records[0]["expected_lesson"] == "Cycle of Socialization|Give Yourself Grace"

    def test_matching_labels_report_no_change(self, golden_csv):
        records = [
            {
                "question": "Why do I feel bad?",
                "expected_module": "Module 1: Money Mindset",
                "expected_lesson": "Cycle of Socialization|Give Yourself Grace",
                "results": RESULTS,
            }
        ]
        assert relabel(records, golden_csv) == 0

    def test_question_absent_from_csv_keeps_its_dump_label(self, golden_csv):
        """A dump taken before a question was reworded must still score, not silently miss."""
        records = [
            {
                "question": "a question no longer in the CSV",
                "expected_module": "Module 1: Money Mindset",
                "expected_lesson": "Generational Legacies",
                "results": RESULTS,
            }
        ]
        assert relabel(records, golden_csv) == 0
        assert records[0]["expected_lesson"] == "Generational Legacies"
