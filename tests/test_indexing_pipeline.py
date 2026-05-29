"""
Unit tests for the local indexing pipeline (no Azure required).
Tests chunk_text(), strip_header(), and build_chunks() from index_modules.py.
Run from project root: uv run pytest test/test_indexing_pipeline.py -v
"""

import json
import os
import sys

import pytest
import tiktoken

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.preprocessing.index_modules import (
    CHUNK_TOKENS,
    ENCODING,
    OVERLAP_TOKENS,
    build_chunks,
    chunk_text,
    strip_header,
)

CHUNKS_JSON = os.path.join(os.path.dirname(__file__), "..", "data", "chunks", "chunks.json")

REQUIRED_FIELDS = {"id", "module_number", "module", "lesson", "lesson_number",
                   "source_url", "chunk_index", "chunk_count", "text"}

MODULE_NUMBER_MAP = {
    "Money Mindset": 1,
    "Building Healthy Habits": 2,
    "Money Management": 3,
    "Navigating Credit": 4,
    "Planning for the Future": 5,
    "Financial Independence": 6,
}

@pytest.fixture(scope="module")
def enc():
    return tiktoken.get_encoding(ENCODING)

@pytest.fixture(scope="module")
def chunks():
    # build_chunks() reads from data/cleaned/ and data/video_manifest.csv
    # must run from project root so relative paths resolve
    original_dir = os.getcwd()
    os.chdir(os.path.join(os.path.dirname(__file__), ".."))
    result = build_chunks()
    os.chdir(original_dir)
    return result

@pytest.fixture(scope="module")
def saved_chunks():
    with open(CHUNKS_JSON, encoding="utf-8") as f:
        return json.load(f)


# chunk_text() tests

class TestChunkText:
    def test_short_text_produces_single_chunk(self, enc):
        text = "This is a short sentence."
        result = chunk_text(text, enc)
        assert len(result) == 1
        assert result[0] == text

    def test_long_text_produces_multiple_chunks(self, enc):
        # 900 tokens of repeated words → should split into at least 3 chunks
        long_text = ("financial planning " * 450).strip()
        result = chunk_text(long_text, enc)
        assert len(result) > 1

    def test_no_chunk_exceeds_token_limit(self, enc):
        long_text = ("budgeting savings investing " * 300).strip()
        for chunk in chunk_text(long_text, enc):
            token_count = len(enc.encode(chunk))
            assert token_count <= CHUNK_TOKENS, (
                f"Chunk has {token_count} tokens, exceeds limit of {CHUNK_TOKENS}"
            )

    def test_consecutive_chunks_share_overlap(self, enc):
        # Overlap means the end of chunk N and the start of chunk N+1 share tokens
        long_text = ("word " * 600).strip()
        chunks = chunk_text(long_text, enc)
        if len(chunks) < 2:
            pytest.skip("Text too short to produce overlapping chunks")
        end_tokens = enc.encode(chunks[0])[-OVERLAP_TOKENS:]
        start_tokens = enc.encode(chunks[1])[:OVERLAP_TOKENS]
        assert end_tokens == start_tokens

    def test_empty_string_returns_empty_list(self, enc):
        # Empty input produces no chunks (nothing to index)
        result = chunk_text("", enc)
        assert result == []


# strip_header() tests

class TestStripHeader:
    SAMPLE_HEADER = (
        "Module: Money Mindset\n"
        "Lesson: Introduction\n"
        "─────────────────────\n"
        "This is the actual lesson content.\n"
        "It continues here."
    )

    def test_strips_header_lines(self):
        result = strip_header(self.SAMPLE_HEADER)
        assert "Module:" not in result
        assert "Lesson:" not in result

    def test_body_content_preserved(self):
        result = strip_header(self.SAMPLE_HEADER)
        assert "This is the actual lesson content." in result
        assert "It continues here." in result

    def test_separator_line_removed(self):
        result = strip_header(self.SAMPLE_HEADER)
        assert "─" not in result

    def test_text_without_header_returns_empty(self):
        # No separator line → strip_header returns empty (header never ends)
        result = strip_header("Module: X\nLesson: Y\nNo separator here")
        assert result == ""

    def test_body_only_after_separator(self):
        text = "─────\nFirst line.\nSecond line."
        result = strip_header(text)
        assert result == "First line.\nSecond line."


# build_chunks() tests

class TestBuildChunks:
    EXPECTED_CHUNK_COUNT = 111

    def test_correct_total_count(self, chunks):
        assert len(chunks) == self.EXPECTED_CHUNK_COUNT, (
            f"Expected {self.EXPECTED_CHUNK_COUNT} chunks, got {len(chunks)}"
        )

    def test_all_required_fields_present(self, chunks):
        for i, chunk in enumerate(chunks):
            missing = REQUIRED_FIELDS - set(chunk.keys())
            assert not missing, f"Chunk {i} missing fields: {missing}"

    def test_no_duplicate_ids(self, chunks):
        ids = [c["id"] for c in chunks]
        assert len(ids) == len(set(ids)), "Duplicate chunk IDs found"

    def test_no_empty_text(self, chunks):
        empty = [c for c in chunks if not c["text"].strip()]
        assert not empty, f"{len(empty)} chunks have empty text"

    def test_module_numbers_are_correct(self, chunks):
        for chunk in chunks:
            expected = MODULE_NUMBER_MAP.get(chunk["module"])
            if expected is not None:
                assert chunk["module_number"] == expected, (
                    f"Module '{chunk['module']}' should be {expected}, got {chunk['module_number']}"
                )

    def test_chunk_index_starts_at_zero(self, chunks):
        # For each lesson, chunk_index should start at 0
        from collections import defaultdict
        lesson_chunks = defaultdict(list)
        for c in chunks:
            lesson_chunks[c["lesson"]].append(c["chunk_index"])
        for lesson, indices in lesson_chunks.items():
            assert 0 in indices, f"Lesson '{lesson}' has no chunk_index=0"

    def test_chunk_count_is_positive(self, chunks):
        # chunk_count is set per source file, not per lesson name
        # (lesson names can repeat across modules, chunk_count reflects per-file count)
        for chunk in chunks:
            assert chunk["chunk_count"] > 0, (
                f"Lesson '{chunk['lesson']}' chunk_index={chunk['chunk_index']} has chunk_count=0"
            )

    def test_no_chunk_text_exceeds_token_limit(self, chunks):
        enc = tiktoken.get_encoding(ENCODING)
        violations = [
            (c["lesson"], c["chunk_index"], len(enc.encode(c["text"])))
            for c in chunks
            if len(enc.encode(c["text"])) > CHUNK_TOKENS
        ]
        assert not violations, f"Chunks exceeding token limit: {violations}"

    def test_all_modules_represented(self, chunks):
        modules_in_chunks = {c["module"] for c in chunks}
        for module in MODULE_NUMBER_MAP:
            assert module in modules_in_chunks, f"Module '{module}' has no chunks"

    def test_source_url_not_empty(self, chunks):
        empty_urls = [c for c in chunks if not c.get("source_url", "").strip()]
        assert not empty_urls, f"{len(empty_urls)} chunks have empty source_url"


# saved chunks.json consistency test

class TestSavedChunksJson:
    def test_saved_chunks_match_rebuilt_chunks(self, chunks, saved_chunks):
        # IDs from build_chunks() should exactly match what's saved in chunks.json
        built_ids = sorted(c["id"] for c in chunks)
        saved_ids = sorted(c["id"] for c in saved_chunks)
        assert built_ids == saved_ids, (
            "chunks.json is out of sync with current data/cleaned/ files. "
            "Re-run index_modules.py to regenerate."
        )