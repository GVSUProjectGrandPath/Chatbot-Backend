"""
Retrieval quality eval — run before and after each retrieval change to track hit rate.

Usage:
    uv run python tests/eval_retrieval.py

Prints:
    Hit rate @5  — correct module in top 5 chunks
    Top-1 match  — correct module is the #1 chunk
    Per-question breakdown with pass/fail

Baseline (hybrid search + query rewriting, no semantic reranker):
    Hit rate @5:  27/27 (100%)
    Top-1 match:  24/27 (88%)
    Run date:     2026-06-25
"""

import os
import sys

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 27 student-phrased questions across all 6 modules, each labeled with its module number and expected source lesson.
GOLDEN_QA = [
    # Module 1 — Money Mindset
    {"question": "why do I feel guilty spending money on myself", "module": 1, "lesson": "Introduction to Money Mindsets"},
    {"question": "how does my family background affect how I handle money", "module": 1, "lesson": "Generational Legacies"},
    {"question": "what behaviors stop people from making financial progress", "module": 1, "lesson": "Behavior That Prevent Progress"},
    {"question": "how do money arguments affect relationships", "module": 1, "lesson": "Money and Relationships"},

    # Module 2 — Building Healthy Habits
    {"question": "how do I stop overthinking and just start saving", "module": 2, "lesson": "Just Start"},
    {"question": "what is the difference between subsidized and unsubsidized student loans", "module": 2, "lesson": "Student Loans"},
    {"question": "how do I avoid overdraft fees on my bank account", "module": 2, "lesson": "Avoiding Fees and Overdrafts"},
    {"question": "how do I choose a bank or credit union", "module": 2, "lesson": "Selecting a Financial Institution"},
    {"question": "how do I navigate financial aid as a college student", "module": 2, "lesson": "Navigating Financial Aid"},

    # Module 3 — Money Management
    {"question": "how do I make a budget that actually works", "module": 3, "lesson": "Spending Plan Foundations"},
    {"question": "what are some tips for saving money as a student", "module": 3, "lesson": "Saving Tips & Tricks"},
    {"question": "how do I negotiate my starting salary", "module": 3, "lesson": "Salary Negotiation"},
    {"question": "how do income taxes work for college students", "module": 3, "lesson": "Income Taxes"},
    {"question": "what should I do when I overspend and go off budget", "module": 3, "lesson": "Give Yourself Grace"},

    # Module 4 — Navigating Credit
    {"question": "what factors make up a FICO credit score", "module": 4, "lesson": "What Makes a Good FICO Score"},
    {"question": "how do credit cards work and when should I use one", "module": 4, "lesson": "Credit Cards as a Financial Tool"},
    {"question": "what is the difference between installment loans and revolving credit", "module": 4, "lesson": "Installment Loans"},
    {"question": "how do I pay down debt faster", "module": 4, "lesson": "Paying Down Debt"},
    {"question": "what do loan officers look at when reviewing a loan application", "module": 4, "lesson": "What Factors Do Loan Officers Consider"},
    {"question": "how do I get a good rate on a car loan", "module": 4, "lesson": "Auto Loans Deep Dive"},

    # Module 5 — Planning for the Future
    {"question": "how do I spot a financial scam", "module": 5, "lesson": "Identifying Common Scams"},
    {"question": "how do I stay safe from fraud online", "module": 5, "lesson": "Online Financial Safety"},
    {"question": "what kind of insurance do I need as a student", "module": 5, "lesson": "Auto Home and Renters Insurance"},
    {"question": "how do I file a dispute on my credit report", "module": 5, "lesson": "Filing a Dispute"},

    # Module 6 — Financial Independence
    {"question": "what is ethical investing and how do I get started", "module": 6, "lesson": "Ethical Investing"},
    {"question": "how do I build financial consistency over time", "module": 6, "lesson": "Consistency is Key"},
    {"question": "how can I use my money to make a positive impact", "module": 6, "lesson": "Use Your Power for Good"},
]


def run_eval():
    # Import here so the script only needs Azure creds, not the full app stack
    from app.services.chain import retrieve

    hits_at_5 = 0
    top1_matches = 0
    total = len(GOLDEN_QA)
    failures = []

    print(f"\nRunning retrieval eval — {total} questions\n")
    print(f"{'#':<4} {'Pass':>5} {'Top-1':>6}  Question")
    print("-" * 72)

    for i, qa in enumerate(GOLDEN_QA, 1):
        try:
            chunks = retrieve(qa["question"])
        except Exception as e:
            print(f"{i:<4} ERROR: {e}")
            failures.append((i, qa["question"], str(e)))
            continue

        returned_modules = [c["module"] for c in chunks]
        expected_module_name = {
            1: "Money Mindset",
            2: "Building Healthy Habits",
            3: "Money Management",
            4: "Navigating Credit",
            5: "Planning for the Future",
            6: "Financial Independence",
        }[qa["module"]]

        hit = any(expected_module_name in m for m in returned_modules)
        top1 = bool(returned_modules) and expected_module_name in returned_modules[0]

        if hit:
            hits_at_5 += 1
        if top1:
            top1_matches += 1

        hit_marker = "YES" if hit else "NO "
        top1_marker = "YES" if top1 else "NO "
        print(f"{i:<4} {hit_marker:>5} {top1_marker:>6}  {qa['question'][:60]}")

        if not hit:
            failures.append((i, qa["question"], f"Expected module {qa['module']} ({expected_module_name}), got: {returned_modules}"))

    print("\n" + "=" * 72)
    print(f"Hit rate @5:   {hits_at_5}/{total} ({100 * hits_at_5 // total}%)")
    print(f"Top-1 match:   {top1_matches}/{total} ({100 * top1_matches // total}%)")

    if failures:
        print(f"\nMisses ({len(failures)}):")
        for num, q, reason in failures:
            print(f"  [{num}] {q}")
            print(f"       {reason}")

    print()
    return hits_at_5, top1_matches, total


if __name__ == "__main__":
    run_eval()
