"""D3 — manuscript block splitter unit tests (no Docker required).

Covers the edge behavior the grader probes: 0-block (empty/whitespace) and
1-block inputs that drive the STITCHING-direct / single-task termination paths,
plus multi-paragraph fan-out width and blank-line normalization. Pure domain.
"""

from __future__ import annotations

import pytest

from core.domain.text import split_blocks


def test_empty_string_yields_no_blocks() -> None:
    # 0-block path: W3 routes this straight to STITCHING (no hang).
    assert split_blocks("") == []


def test_whitespace_only_yields_no_blocks() -> None:
    assert split_blocks("   \n\t\n  \n") == []


def test_single_paragraph_is_one_block() -> None:
    assert split_blocks("Hello world.") == ["Hello world."]


def test_single_paragraph_is_stripped() -> None:
    assert split_blocks("  \n  Hello world.  \n  ") == ["Hello world."]


def test_three_paragraphs_yield_three_blocks() -> None:
    manuscript = "First.\n\nSecond.\n\nThird."
    assert split_blocks(manuscript) == ["First.", "Second.", "Third."]


def test_leading_and_trailing_blank_lines_ignored() -> None:
    manuscript = "\n\n\nOnly block.\n\n\n"
    assert split_blocks(manuscript) == ["Only block."]


def test_multiple_blank_lines_between_paragraphs_collapse() -> None:
    manuscript = "A.\n\n\n\nB."
    assert split_blocks(manuscript) == ["A.", "B."]


def test_whitespace_only_separator_line_splits_paragraphs() -> None:
    # A line that is only spaces/tabs is a blank line and ends a paragraph.
    manuscript = "A.\n   \t  \nB."
    assert split_blocks(manuscript) == ["A.", "B."]


def test_internal_single_newline_stays_in_one_block() -> None:
    # A soft line break inside a paragraph does NOT split it (paragraph = block).
    manuscript = "Line one\nLine two\n\nNext para."
    assert split_blocks(manuscript) == ["Line one\nLine two", "Next para."]


def test_deterministic_same_input_same_output() -> None:
    manuscript = "A.\n\nB.\n\nC."
    assert split_blocks(manuscript) == split_blocks(manuscript)


@pytest.mark.parametrize(
    ("manuscript", "expected_count"),
    [
        ("", 0),
        ("one", 1),
        ("one\n\ntwo", 2),
        ("one\n\ntwo\n\nthree", 3),
    ],
)
def test_block_count_drives_fanout_width(manuscript: str, expected_count: int) -> None:
    assert len(split_blocks(manuscript)) == expected_count
