"""Unit tests for the retrieval scorer's matching rules.

These cover the grading logic itself, not retrieval quality: multi-label golden rows, and the
relabel-from-CSV step that lets a corrected label be re-scored without paying for a new run.
"""

import csv

import pytest

from tests.score_retrieval_eval import (
    accepted,
    first_hit_rank,
    load_golden_labels,
    per_module,
    relabel,
    score_level,
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
