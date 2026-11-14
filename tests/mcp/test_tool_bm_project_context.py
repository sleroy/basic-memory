"""bm_project_context: the session-start pack, composed from real notes.

These run the real ASGI stack through the shared `client` fixture, writing notes with
bm_write the way an agent does and reading the pack back.
"""

import re
from datetime import date, timedelta
from typing import Any

import pytest
from pydantic import ValidationError, validate_call

from basic_memory.mcp.pack_composition import (
    CODE_POINTER_LABEL,
    EPISODIC_LABEL,
    HYGIENE_LABEL,
    POLICY_LABEL,
    RECALLED_LABEL,
)
from basic_memory.mcp.tools import bm_facts, bm_project_context, bm_search, bm_write
from tests.mcp.test_pack_composition import continuation_call
from tests.mcp.test_tool_bm_facts import REPO, write_record

TODAY = date.today()


async def pack_text(**arguments: Any) -> str:
    result = await bm_project_context(**arguments)
    assert isinstance(result, str)
    return result


async def pack_json(**arguments: Any) -> dict[str, Any]:
    result = await bm_project_context(output_format="json", **arguments)
    assert isinstance(result, dict)
    return result


def lesson_body(summary: str, action: str, verification: str | None = None) -> str:
    lines = ["# Lesson", "", f"- [summary] {summary}", f"- [recommended_action] {action}"]
    if verification:
        lines.append(f"- [verification] {verification}")
    return "\n".join(lines)


async def seed_pilot_notes(project: str) -> None:
    await write_record(
        project,
        kind="decision",
        title="Use SQLite for the index",
        authorities=["docs/adr/ADR-003.md", "AGENTS.md#14"],
        last_verified=TODAY.isoformat(),
        confidence="high",
        body="# Use SQLite for the index\n\n- [summary] The index is SQLite.",
    )
    await write_record(
        project,
        kind="bug",
        title="Loader drops rows",
        authorities=["AGENTS.md#14"],
        body="# Loader drops rows\n\nThe loader loses the last batch.",
    )
    await write_record(
        project,
        kind="lesson",
        title="Retry on 429",
        last_verified=TODAY.isoformat(),
        confidence="medium",
        body=lesson_body("Rate limits recur.", "Back off and retry.", "pytest tests/retry"),
    )
    await write_record(project, kind="best_practice", title="Small PRs")
    await write_record(project, kind="fact", title="CI takes ten minutes")
    await write_record(project, kind="glossary_term", title="BRE")


@pytest.mark.asyncio
async def test_pack_has_every_section_in_the_fixed_order(client, test_project):
    await seed_pilot_notes(test_project.name)
    await write_record(
        test_project.name, kind="session", title="Session 2026-11-14", status="active", repo=REPO
    )

    text = await pack_text(repo=REPO, branch="dev", project=test_project.name)

    positions = [
        text.index(label) for label in (POLICY_LABEL, RECALLED_LABEL, EPISODIC_LABEL, HYGIENE_LABEL)
    ]
    assert positions == sorted(positions)
    assert CODE_POINTER_LABEL not in text
    header = text.splitlines()[0]
    assert re.fullmatch(
        r"Project context \| repo: demo-repo \| branch: dev \| "
        r"generated: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ \| tokens: \d+/4000 \(estimate, chars/4\)",
        header,
    )
    recalled = text[text.index(RECALLED_LABEL) : text.index(EPISODIC_LABEL)]
    kind_headings = [
        recalled.index(heading)
        for heading in ("Decisions:", "Bugs (", "Lessons:", "Best practices:", "Facts:", "Glossary")
    ]
    assert kind_headings == sorted(kind_headings)
    assert "- Session 2026-11-14" in text.split(EPISODIC_LABEL)[1]


@pytest.mark.asyncio
async def test_policy_pointers_are_deduplicated_with_counts_and_carry_no_note_content(
    client, test_project
):
    await seed_pilot_notes(test_project.name)

    text = await pack_text(repo=REPO, project=test_project.name)

    policy = text[text.index(POLICY_LABEL) : text.index(RECALLED_LABEL)]
    assert "- AGENTS.md#14 (2 notes)" in policy
    assert "- docs/adr/ADR-003.md (1 notes)" in policy
    assert policy.index("AGENTS.md#14") < policy.index("docs/adr/ADR-003.md")
    assert "The index is SQLite" not in policy


@pytest.mark.asyncio
async def test_recalled_items_show_summary_freshness_and_confidence(client, test_project):
    await seed_pilot_notes(test_project.name)

    text = await pack_text(repo=REPO, project=test_project.name)

    recalled = text[text.index(RECALLED_LABEL) : text.index(EPISODIC_LABEL)]
    assert (
        f"- Use SQLite for the index: The index is SQLite. "
        f"[last_verified {TODAY.isoformat()}; confidence high] "
    ) in recalled
    # No [summary] observation: the first line of the body stands in.
    assert (
        "- Loader drops rows: The loader loses the last batch. [unverified; confidence n/a]"
        in recalled
    )
    # A lesson says what to do and how to check it.
    assert (
        "- Retry on 429: Back off and retry. (verify: pytest tests/retry) "
        f"[last_verified {TODAY.isoformat()}; confidence medium] "
    ) in recalled


@pytest.mark.asyncio
async def test_drafts_superseded_and_invalidated_are_never_rendered(client, test_project):
    await write_record(test_project.name, kind="lesson", title="Shown lesson")
    for status in ("draft", "superseded", "invalidated", "promoted"):
        await write_record(
            test_project.name, kind="lesson", title=f"Hidden {status}", status=status
        )
        await write_record(
            test_project.name, kind="bug", title=f"Hidden bug {status}", status=status
        )

    text = await pack_text(repo=REPO, project=test_project.name)
    payload = await pack_json(repo=REPO, project=test_project.name)

    assert "Shown lesson" in text
    assert "Hidden" not in text
    assert "Hidden" not in str(payload)
    assert "- 2 drafts awaiting review" in text.split(HYGIENE_LABEL)[1]


@pytest.mark.asyncio
async def test_other_repositories_are_left_out(client, test_project):
    await write_record(test_project.name, kind="fact", title="Mine")
    await write_record(test_project.name, kind="fact", title="Theirs", repo="other-repo")
    await write_record(test_project.name, kind="fact", title="Unscoped", repo=None)

    text = await pack_text(repo=REPO, project=test_project.name)

    assert "Mine" in text
    assert "Theirs" not in text
    assert "Unscoped" not in text


@pytest.mark.asyncio
async def test_branch_keeps_matching_and_branch_agnostic_notes(client, test_project):
    await write_record(test_project.name, kind="fact", title="On dev", valid_for={"branch": "dev"})
    await write_record(
        test_project.name, kind="fact", title="On main", valid_for={"branch": "main"}
    )
    await write_record(test_project.name, kind="fact", title="Any branch")

    on_dev = await pack_text(repo=REPO, branch="dev", project=test_project.name)
    no_branch = await pack_text(repo=REPO, project=test_project.name)

    assert "On dev" in on_dev and "Any branch" in on_dev
    assert "On main" not in on_dev
    assert all(title in no_branch for title in ("On dev", "On main", "Any branch"))
    assert "branch: any" in no_branch.splitlines()[0]


@pytest.mark.asyncio
async def test_affected_paths_rank_by_applies_to_prefix_and_add_the_code_pointer(
    client, test_project
):
    await write_record(test_project.name, kind="lesson", title="Unscoped lesson")
    await write_record(
        test_project.name, kind="lesson", title="Other module", applies_to=["src/billing"]
    )
    await write_record(
        test_project.name, kind="lesson", title="Loader lesson", applies_to=["src/loader/"]
    )

    text = await pack_text(
        repo=REPO, affected_paths=["src/loader/batch.py"], project=test_project.name
    )

    lessons = (
        text[text.index("Lessons:") : text.index("Best practices:")]
        if "Best practices:" in text
        else text[text.index("Lessons:") :]
    )
    assert lessons.index("Loader lesson") < lessons.index("Unscoped lesson")
    assert lessons.index("Loader lesson") < lessons.index("Other module")
    assert f"{CODE_POINTER_LABEL}\nFor symbols, callers and dependencies" in text
    assert "memory does not store code structure." in text


@pytest.mark.asyncio
async def test_fixed_bugs_appear_only_when_task_or_paths_match(client, test_project):
    await write_record(test_project.name, kind="bug", title="Open bug")
    await write_record(
        test_project.name,
        kind="bug",
        title="Fixed loader bug",
        status="fixed",
        applies_to=["src/loader"],
        body="# Fixed loader bug\n\nRows vanished during batching.",
    )
    await write_record(
        test_project.name,
        kind="bug",
        title="Fixed unrelated bug",
        status="fixed",
        body="# Fixed unrelated bug\n\nSomething else entirely.",
    )

    plain = await pack_text(repo=REPO, project=test_project.name)
    by_path = await pack_text(repo=REPO, affected_paths=["src/loader"], project=test_project.name)
    by_task = await pack_text(
        repo=REPO, task="rows vanished while batching", project=test_project.name
    )

    assert "Open bug" in plain
    assert "Fixed" not in plain
    assert "- [fixed] Fixed loader bug" in by_path
    assert "Fixed unrelated bug" not in by_path
    assert "- [fixed] Fixed loader bug" in by_task
    assert "Fixed unrelated bug" not in by_task


@pytest.mark.asyncio
async def test_task_text_ranks_lessons_ahead_of_newer_ones(client, test_project):
    await write_record(
        test_project.name,
        kind="lesson",
        title="Pagination cursors",
        body=lesson_body("Cursors expire.", "Refresh the cursor."),
    )
    for index in range(3):
        await write_record(
            test_project.name,
            kind="lesson",
            title=f"Newer lesson {index}",
            body=lesson_body("Something else.", "Do other things."),
        )

    unranked = await pack_text(repo=REPO, project=test_project.name)
    ranked = await pack_text(
        repo=REPO, task="fix the pagination cursor bug", project=test_project.name
    )

    assert unranked.index("Pagination cursors") > unranked.index("Newer lesson 2")
    assert ranked.index("Pagination cursors") < ranked.index("Newer lesson 2")


@pytest.mark.asyncio
async def test_task_without_searchable_words_ranks_nothing(client, test_project):
    await write_record(test_project.name, kind="lesson", title="A lesson")

    text = await pack_text(repo=REPO, task="?! AND OR", project=test_project.name)

    assert "A lesson" in text


@pytest.mark.asyncio
async def test_hygiene_counts_drafts_and_unverified_active_notes(client, test_project):
    recent = (TODAY - timedelta(days=5)).isoformat()
    old = (TODAY - timedelta(days=200)).isoformat()
    await write_record(test_project.name, kind="fact", title="Fresh", last_verified=recent)
    await write_record(test_project.name, kind="fact", title="Old", last_verified=old)
    await write_record(test_project.name, kind="lesson", title="Never verified")
    await write_record(test_project.name, kind="bug", title="Never verified bug")
    await write_record(test_project.name, kind="decision", title="Draft", status="draft")
    await write_record(test_project.name, kind="decision", title="Draft two", status="draft")
    await write_record(
        test_project.name,
        kind="fact",
        title="Old but superseded",
        status="superseded",
        last_verified=old,
    )

    text = await pack_text(repo=REPO, project=test_project.name)
    wider = await pack_json(repo=REPO, recheck_days=400, project=test_project.name)

    hygiene = text.split(HYGIENE_LABEL)[1]
    assert (
        '- 2 drafts awaiting review (one bm_facts call per kind, with status="draft", '
        f'project="{test_project.name}", repo="demo-repo")'
    ) in hygiene
    cutoff = (TODAY - timedelta(days=90)).isoformat()
    assert (
        f"- 3 active notes not verified in 90 days (last_verified absent or before {cutoff})"
        in hygiene
    )
    assert wider["hygiene"]["stale"] == 2
    assert wider["hygiene"]["drafts"] == 2


@pytest.mark.asyncio
async def test_budget_truncation_ends_each_cut_section_with_a_continuation(client, test_project):
    for index in range(30):
        await write_record(
            test_project.name,
            kind="lesson",
            title=f"Lesson number {index:02d}",
            authorities=[f"docs/rule-{index:02d}.md"],
            body=lesson_body("A lesson with some length " * 3, "Do the careful thing " * 4),
        )
    await write_record(test_project.name, kind="fact", title="Late fact")

    text = await pack_text(repo=REPO, budget_tokens=500, project=test_project.name)
    payload = await pack_json(repo=REPO, budget_tokens=500, project=test_project.name)

    assert payload["tokens_used"] <= 500
    assert re.search(r"tokens: \d+/500 ", text.splitlines()[0])
    assert (
        f'… continue with bm_facts(kind="lesson", status="active", '
        f'project="{test_project.name}", repo="demo-repo") (full list; includes notes shown above)'
    ) in text
    # Search order is most recently edited first, so the pack keeps a prefix of that
    # order: the newest lessons (29, 28, ...) and none of the oldest.
    shown = [int(number) for number in re.findall(r"^- Lesson number (\d\d)", text, re.M)]
    assert 0 < len(shown) < 30
    assert shown == list(range(29, 29 - len(shown), -1))
    assert "Lesson number 00" not in text
    # The pack is a prefix of the priority order: once lessons are cut, a fact that
    # would have fit is held back, and its section says how to get it.
    assert (
        f'… continue with bm_facts(kind="fact", status="active", '
        f'project="{test_project.name}", repo="demo-repo") (full list; includes notes shown above)'
    ) in text
    assert "Late fact" not in text
    assert payload["recalled_knowledge"]["fact"]["lines"] == []
    assert payload["recalled_knowledge"]["fact"]["continuation"].startswith("… continue with")


@pytest.mark.asyncio
async def test_a_larger_budget_keeps_everything_without_continuations(client, test_project):
    await seed_pilot_notes(test_project.name)

    text = await pack_text(repo=REPO, budget_tokens=20000, project=test_project.name)

    assert "… continue with" not in text
    assert "CI takes ten minutes" in text


@pytest.mark.asyncio
async def test_a_kind_with_more_notes_than_the_pack_reads_ends_with_a_continuation(
    client, test_project, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("basic_memory.mcp.tools.context_pack.NOTES_PER_KIND", 2)
    for index in range(3):
        await write_record(test_project.name, kind="fact", title=f"Fact {index}")

    text = await pack_text(repo=REPO, project=test_project.name)

    assert text.count("- Fact ") == 2
    assert (
        f'… continue with bm_facts(kind="fact", status="active", '
        f'project="{test_project.name}", repo="demo-repo") (full list; includes notes shown above)'
    ) in text


@pytest.mark.asyncio
async def test_json_pack_mirrors_the_text_sections(client, test_project):
    await seed_pilot_notes(test_project.name)

    payload = await pack_json(
        repo=REPO, branch="dev", affected_paths=["src"], project=test_project.name
    )

    assert payload["repo"] == REPO
    assert payload["branch"] == "dev"
    assert payload["budget_tokens"] == 4000
    assert 0 < payload["tokens_used"] <= 4000
    assert payload["code_pointer"].startswith("For symbols, callers and dependencies")
    assert payload["policy_pointers"]["lines"][0] == "- AGENTS.md#14 (2 notes)"
    assert list(payload["recalled_knowledge"]) == [
        "decision",
        "bug",
        "lesson",
        "best_practice",
        "fact",
        "glossary_term",
    ]
    assert payload["hygiene"]["recheck_days"] == 90
    assert payload["episodic"] == {"lines": [], "continuation": None}


@pytest.mark.asyncio
async def test_empty_project_says_none_for_every_section(client, test_project):
    await write_record(test_project.name, kind="fact", title="Elsewhere", repo="other-repo")

    text = await pack_text(repo=REPO, project=test_project.name)

    assert text.count("(none)") == 3
    assert "- 0 drafts awaiting review" in text
    assert "- 0 active notes not verified in 90 days" in text


@pytest.mark.asyncio
async def test_empty_pack_in_an_unindexed_project_says_so(
    client, test_project, monkeypatch: pytest.MonkeyPatch
):
    async def never_indexed(*_: Any, **__: Any) -> str:
        return "# Project Index Required"

    monkeypatch.setattr("basic_memory.mcp.tools.context_pack.project_index_required", never_indexed)

    assert await pack_text(repo=REPO, project=test_project.name) == "# Project Index Required"
    assert await pack_json(repo=REPO, project=test_project.name) == {
        "results": [],
        "guidance": "# Project Index Required",
    }


@pytest.mark.asyncio
async def test_episodic_lists_the_five_latest_sessions_of_the_repo(client, test_project):
    for index in range(7):
        await bm_write(
            title=f"Session {index}",
            directory="sessions",
            content=f"# Session {index}",
            note_type="session",
            metadata={"repo": REPO},
            project=test_project.name,
        )
    await bm_write(
        title="Other repo session",
        directory="sessions",
        content="# x",
        note_type="session",
        metadata={"repo": "other-repo"},
        project=test_project.name,
    )

    text = await pack_text(repo=REPO, project=test_project.name)

    episodic = text.split(EPISODIC_LABEL)[1].split(HYGIENE_LABEL)[0]
    assert [line for line in episodic.splitlines() if line.startswith("- ")] == [
        f"- Session {index}" for index in (6, 5, 4, 3, 2)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [{"budget_tokens": 499}, {"budget_tokens": 20001}, {"recheck_days": 0}, {"repo": ""}],
)
async def test_arguments_are_validated(client, test_project, arguments):
    call: Any = validate_call(bm_project_context)
    with pytest.raises(ValidationError):
        await call(**{"repo": REPO, "project": test_project.name, **arguments})


@pytest.mark.asyncio
async def test_a_string_for_affected_paths_is_read_as_one_path_or_a_json_list(client, test_project):
    await write_record(
        test_project.name, kind="lesson", title="Loader lesson", applies_to=["src/loader"]
    )
    await write_record(test_project.name, kind="lesson", title="Newer lesson")
    call: Any = validate_call(bm_project_context)

    # Outside an MCP request there is no FastMCP context, as in the CLI.
    one_path = await call(
        repo=REPO, affected_paths="src/loader", project=test_project.name, context=None
    )
    json_list = await call(
        repo=REPO, affected_paths='["src/loader"]', project=test_project.name, context=None
    )

    for text in (one_path, json_list):
        assert text.index("Loader lesson") < text.index("Newer lesson")
        assert CODE_POINTER_LABEL in text


@pytest.mark.asyncio
async def test_observations_past_the_search_snippet_cap_still_reach_the_pack(client, test_project):
    """Task 3.6 repro: the pack reads accepted Markdown, so the snippet cap does not bite.

    Search rows carry at most 4000 characters of a note (CONTENT_DISPLAY_LIMIT). The
    structured lines of this lesson sit past 100,000 characters, far beyond that cap.
    """
    filler = "Investigation notes that go on and on. " * 2600
    await write_record(
        test_project.name,
        kind="lesson",
        title="Very long lesson",
        body=(
            f"# Very long lesson\n\n{filler}\n\n"
            "- [summary] Found at the very end.\n"
            "- [recommended_action] Read the whole note.\n"
            "- [verification] make verify"
        ),
    )

    searched = await bm_search(
        query="Investigation", project=test_project.name, output_format="json"
    )
    text = await pack_text(repo=REPO, project=test_project.name)

    assert isinstance(searched, dict)
    [hit] = searched["results"]
    assert hit["content_truncated"] is True
    assert hit["content_length"] > 100_000
    assert "Found at the very end" not in hit["content"]
    assert "- Very long lesson: Read the whole note. (verify: make verify) [" in text


@pytest.mark.asyncio
async def test_following_a_continuation_lists_the_pack_statuses_and_nothing_else(
    client, test_project
):
    project = test_project.name
    for index in range(25):
        await write_record(
            project,
            kind="bug",
            title=f"Open bug {index:02d}",
            body=f"# Open bug {index:02d}\n\n- [summary] {'It breaks in a long way. ' * 6}",
        )
    await write_record(project, kind="bug", title="Fixed bug", status="fixed")
    for index in range(5):
        await write_record(project, kind="lesson", title=f"Active lesson {index}")
    for status in ("draft", "superseded", "invalidated"):
        await write_record(project, kind="lesson", title=f"Lesson {status}", status=status)
        await write_record(project, kind="bug", title=f"Bug {status}", status=status)

    payload = await pack_json(repo=REPO, budget_tokens=500, project=project)

    facts: Any = bm_facts
    listed: dict[str, set[str]] = {}
    for kind in ("bug", "lesson"):
        tool, arguments, _ = continuation_call(payload["recalled_knowledge"][kind]["continuation"])
        assert tool == "bm_facts"
        result = await facts(**arguments, page_size=100, output_format="json")
        assert isinstance(result, dict) and not result["has_more"]
        listed[kind] = {row["title"] for row in result["results"]}
        assert {row["status"] for row in result["results"]} <= {"active", "fixed"}

    assert listed["lesson"] == {f"Active lesson {index}" for index in range(5)}
    # The bug call reaches every open bug (the pack showed only a prefix of them) and
    # the fixed one, but no draft, superseded or invalidated bug.
    assert {f"Open bug {index:02d}" for index in range(25)} | {"Fixed bug"} == listed["bug"]
