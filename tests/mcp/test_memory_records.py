"""Memory records: reading a curated note's lifecycle fields and its structured lines."""

from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp.memory_records import (
    count_notes,
    one_line,
    parse_record,
)
from basic_memory.mcp.note_targets import PathQualifier, open_project_target
from basic_memory.schemas.search import SearchItemType, SearchResult
from tests.mcp.test_tool_bm_facts import REPO, write_record

NOTE = """---
title: Retry on 429
type: lesson
status: active
repo: demo
authorities: AGENTS.md#14
applies_to:
- src/retry
- src/http
valid_for:
  branch: dev
last_verified: 2026-11-01
confidence: high
---

# Retry on 429

Some prose that is not the summary.

- [Summary] Rate limits recur.
- [recommended_action] Back off and retry.
- [verification] pytest tests/retry
- [summary] A second summary is ignored.
"""


def hit(**overrides: Any) -> SearchResult:
    values: dict[str, Any] = {
        "title": "Retry on 429",
        "type": SearchItemType.ENTITY,
        "score": 0.0,
        "permalink": "proj/lessons/retry",
        "file_path": "lessons/Retry on 429.md",
        "external_id": "ext-1",
    }
    return SearchResult(**{**values, **overrides})


PATHS = PathQualifier(prefix=None)


def test_parse_record_reads_frontmatter_and_observations():
    record = parse_record("lesson", hit(), NOTE, PATHS)

    assert record.kind == "lesson"
    assert record.title == "Retry on 429"
    assert record.file_path == "lessons/Retry on 429.md"
    assert record.permalink == "proj/lessons/retry"
    assert record.external_id == "ext-1"
    assert record.status == "active"
    assert record.repo == "demo"
    # A single string stands for a one-item list.
    assert record.authorities == ("AGENTS.md#14",)
    assert record.applies_to == ("src/retry", "src/http")
    assert record.branch == "dev"
    # YAML reads the unquoted date as a date; the record keeps its ISO text.
    assert record.last_verified == "2026-11-01"
    assert record.confidence == "high"
    assert record.summary == "Rate limits recur."
    assert record.recommended_action == "Back off and retry."
    assert record.verification == "pytest tests/retry"


def test_parse_record_qualifies_the_path_like_the_request_addressed_the_project():
    record = parse_record("lesson", hit(), NOTE, PathQualifier(prefix="team-project"))

    assert record.file_path == "team-project/lessons/Retry on 429.md"


def test_summary_falls_back_to_the_first_line_of_prose():
    body = "---\nstatus: active\n---\n\n# Heading\n\n- [fact] Bulleted first line.\n\nLater.\n"

    assert parse_record("fact", hit(), body, PATHS).summary == "Bulleted first line."
    assert parse_record("fact", hit(), "# Only a heading\n", PATHS).summary is None


@pytest.mark.parametrize(
    "content",
    [
        None,
        "no frontmatter at all",
        "---\nstatus: [unclosed\n---\n\nbody",
        "---\nstatus: active\n",
    ],
)
def test_notes_without_usable_frontmatter_still_yield_a_record(content):
    record = parse_record("fact", hit(), content, PATHS)

    assert record.title == "Retry on 429"
    assert record.status is None
    assert record.repo is None
    assert record.authorities == ()
    assert record.branch is None
    assert record.last_verified is None


def test_valid_for_that_is_not_a_mapping_means_every_branch():
    content = "---\nstatus: active\nvalid_for: dev\n---\n\nbody"

    assert parse_record("fact", hit(), content, PATHS).branch is None


def test_a_hit_without_an_external_id_is_refused():
    with pytest.raises(ToolError, match="too old for bm_facts"):
        parse_record("fact", hit(external_id=None), NOTE, PATHS)


def test_record_json_turns_sequences_into_lists():
    payload = parse_record("lesson", hit(), NOTE, PATHS).to_json()

    assert payload["authorities"] == ["AGENTS.md#14"]
    assert payload["applies_to"] == ["src/retry", "src/http"]
    assert payload["kind"] == "lesson"


def test_one_line_collapses_whitespace_and_marks_a_cut():
    assert one_line("a \n  b\t c") == "a b c"
    assert one_line("abcdef", limit=4) == "abc…"


@pytest.mark.asyncio
async def test_count_notes_counts_rows_across_pages(
    client, test_project, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("basic_memory.mcp.memory_records.COUNT_PAGE_SIZE", 2)
    for index in range(5):
        await write_record(test_project.name, kind="fact", title=f"Fact {index}")
    await write_record(test_project.name, kind="fact", title="Elsewhere", repo="other")

    async with open_project_target("", test_project.name, None) as target:
        in_repo = await count_notes(target, note_types=["fact"], metadata_filters={"repo": REPO})
        nothing = await count_notes(
            target, note_types=["fact"], metadata_filters={"repo": REPO}, status="draft"
        )

    assert in_repo == 5
    assert nothing == 0
