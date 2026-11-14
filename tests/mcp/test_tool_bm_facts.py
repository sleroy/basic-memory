"""bm_facts: curated knowledge notes by kind, repo and status.

These run the real ASGI stack through the shared `client` fixture, writing notes with
bm_write the way an agent does.
"""

from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from pydantic import ValidationError, validate_call

from basic_memory.mcp.tools import bm_facts, bm_write

REPO = "demo-repo"


async def write_record(
    project: str,
    *,
    kind: str,
    title: str,
    status: str = "active",
    repo: str | None = REPO,
    body: str = "",
    **frontmatter: Any,
) -> None:
    metadata: dict[str, Any] = {"status": status, **frontmatter}
    if repo is not None:
        metadata["repo"] = repo
    await bm_write(
        title=title,
        directory=f"{kind}s",
        content=body or f"# {title}\n\nBody of {title}.",
        note_type=kind,
        metadata=metadata,
        project=project,
    )


async def facts_text(**arguments: Any) -> str:
    result = await bm_facts(**arguments)
    assert isinstance(result, str)
    return result


async def facts_json(**arguments: Any) -> dict[str, Any]:
    result = await bm_facts(output_format="json", **arguments)
    assert isinstance(result, dict)
    return result


@pytest.mark.asyncio
async def test_lists_one_kind_with_lifecycle_fields(client, test_project):
    await write_record(
        test_project.name,
        kind="lesson",
        title="Retry on 429",
        last_verified="2026-11-14",
        confidence="high",
    )
    await write_record(test_project.name, kind="lesson", title="Bare lesson")
    await write_record(test_project.name, kind="decision", title="Use SQLite")

    text = await facts_text(kind="lesson", project=test_project.name)

    assert text.startswith("2 lesson notes on page 1:")
    assert "Retry on 429 | status active | last_verified 2026-11-14 | confidence high" in text
    assert "Bare lesson | status active" in text
    assert "Use SQLite" not in text


@pytest.mark.asyncio
async def test_repo_and_status_narrow_the_listing(client, test_project):
    await write_record(test_project.name, kind="bug", title="Mine open")
    await write_record(test_project.name, kind="bug", title="Mine draft", status="draft")
    await write_record(test_project.name, kind="bug", title="Other repo", repo="other-repo")

    drafts = await facts_text(kind="bug", repo=REPO, status="draft", project=test_project.name)
    in_repo = await facts_text(kind="bug", repo=REPO, project=test_project.name)

    assert "Mine draft" in drafts
    assert "Mine open" not in drafts
    assert drafts.startswith("1 bug notes with repo 'demo-repo' and status 'draft' on page 1:")
    assert "Other repo" not in in_repo
    assert "Mine open" in in_repo
    assert "Mine draft" in in_repo


@pytest.mark.asyncio
async def test_frontmatter_filters_combine_with_repo(client, test_project):
    await write_record(test_project.name, kind="fact", title="High", confidence="high")
    await write_record(test_project.name, kind="fact", title="Low", confidence="low")

    text = await facts_text(
        kind="fact", repo=REPO, filters={"confidence": "high"}, project=test_project.name
    )

    assert "High" in text
    assert "Low" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "key", "owner"),
    [
        ({"repo": REPO, "filters": {"repo": "x"}}, "repo", "repo"),
        ({"status": "draft", "filters": {"status": "active"}}, "status", "status"),
        ({"filters": {"type": "bug"}}, "type", "kind"),
        ({"filters": {"note_type": "bug"}}, "note_type", "kind"),
    ],
)
async def test_filters_that_contradict_an_argument_are_refused(
    client, test_project, arguments, key, owner
):
    with pytest.raises(ToolError) as refused:
        await bm_facts(kind="lesson", project=test_project.name, **arguments)

    message = str(refused.value)
    assert message.startswith("INVALID_ARGUMENTS")
    assert f"`filters` has '{key}', which `{owner}` already sets." in message


@pytest.mark.asyncio
async def test_filters_may_name_repo_when_the_argument_is_absent(client, test_project):
    await write_record(test_project.name, kind="fact", title="In repo")

    text = await facts_text(
        kind="fact", filters={"repo": {"$in": [REPO, "x"]}}, project=test_project.name
    )

    assert "In repo" in text


@pytest.mark.asyncio
async def test_most_recently_edited_note_comes_first(client, test_project):
    await write_record(test_project.name, kind="fact", title="First written")
    await write_record(test_project.name, kind="fact", title="Second written")

    text = await facts_text(kind="fact", project=test_project.name)

    assert text.index("Second written") < text.index("First written")


@pytest.mark.asyncio
async def test_text_continues_with_a_bm_facts_call(client, test_project):
    for index in range(3):
        await write_record(test_project.name, kind="fact", title=f"Fact {index}")

    text = await facts_text(
        kind="fact", repo=REPO, status="active", page_size=2, project=test_project.name
    )

    assert (
        "More results available. Call bm_facts(kind='fact', repo='demo-repo', status='active', "
        f"project='{test_project.name}', page=2, page_size=2) to continue."
    ) in text
    second = await facts_text(
        kind="fact", repo=REPO, page=2, page_size=2, project=test_project.name
    )
    assert second.count("\n- ") == 1
    assert "More results" not in second


@pytest.mark.asyncio
async def test_json_rows_carry_the_record(client, test_project):
    await write_record(
        test_project.name,
        kind="decision",
        title="Pick Postgres",
        last_verified="2026-11-01",
        confidence="medium",
        authorities=["docs/adr/ADR-003.md"],
        valid_for={"branch": "dev"},
        body="# Pick Postgres\n\n- [summary] Postgres is the system of record.",
    )

    payload = await facts_json(kind="decision", repo=REPO, project=test_project.name)

    assert payload["has_more"] is False
    assert (payload["page"], payload["page_size"]) == (1, 20)
    [row] = payload["results"]
    assert row["title"] == "Pick Postgres"
    assert row["kind"] == "decision"
    assert row["status"] == "active"
    assert row["repo"] == REPO
    assert row["authorities"] == ["docs/adr/ADR-003.md"]
    assert row["branch"] == "dev"
    assert row["last_verified"] == "2026-11-01"
    assert row["confidence"] == "medium"
    assert row["summary"] == "Postgres is the system of record."


@pytest.mark.asyncio
async def test_no_matches(client, test_project):
    await write_record(test_project.name, kind="fact", title="Elsewhere", repo="other-repo")

    assert (
        await facts_text(kind="fact", repo=REPO, status="draft", project=test_project.name)
        == "No fact notes with repo 'demo-repo' and status 'draft'."
    )
    assert (
        await facts_text(kind="fact", repo="nobody", project=test_project.name)
        == "No fact notes with repo 'nobody'."
    )
    assert await facts_text(kind="glossary_term", project=test_project.name) == (
        "No glossary_term notes."
    )


@pytest.mark.asyncio
async def test_empty_result_in_an_unindexed_project_says_so(
    client, test_project, monkeypatch: pytest.MonkeyPatch
):
    async def never_indexed(*_: Any, **__: Any) -> str:
        return "# Project Index Required"

    monkeypatch.setattr("basic_memory.mcp.tools.facts.project_index_required", never_indexed)

    assert await facts_text(kind="fact", project=test_project.name) == "# Project Index Required"
    assert await facts_json(kind="fact", project=test_project.name) == {
        "results": [],
        "guidance": "# Project Index Required",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"kind": "rumor"},
        {"kind": "fact", "page": 0},
        {"kind": "fact", "page_size": 101},
        {"kind": "fact", "repo": ""},
        {"kind": "fact", "status": ""},
    ],
)
async def test_arguments_are_validated(client, test_project, arguments):
    call: Any = validate_call(bm_facts)
    with pytest.raises(ValidationError):
        await call(project=test_project.name, **arguments)


@pytest.mark.asyncio
async def test_filters_given_as_a_json_string_are_read_as_an_object(client, test_project):
    await write_record(test_project.name, kind="fact", title="High", confidence="high")
    await write_record(test_project.name, kind="fact", title="Low", confidence="low")
    call: Any = validate_call(bm_facts)

    # Outside an MCP request there is no FastMCP context, as in the CLI.
    text = await call(
        kind="fact", filters='{"confidence": "low"}', project=test_project.name, context=None
    )

    assert "Low" in text and "High" not in text
