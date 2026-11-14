"""bm_facts and bm_project_context end to end over a vault written through the API.

Notes are written with bm_write as an agent writes them, frontmatter and observations
included, then read back through the MCP protocol so tool registration, argument
validation and both database backends are in the path.
"""

import json
from datetime import date, timedelta
from typing import Any

import pytest
from fastmcp import Client

REPO = "monolith-breaker"
TODAY = date.today()


async def write(client: Client[Any], project: str, kind: str, title: str, **fields: object) -> None:
    body = fields.pop("body", f"# {title}\n\nBody of {title}.")
    result = await client.call_tool(
        "bm_write",
        {
            "project": project,
            "title": title,
            "directory": f"memory/{kind}s",
            "note_type": kind,
            "content": body,
            "metadata": {"repo": REPO, "status": "active", **fields},
        },
    )
    assert not result.is_error


@pytest.mark.asyncio
async def test_pack_renders_a_real_vault_within_budget(mcp_server, app, test_project):
    verified = (TODAY - timedelta(days=3)).isoformat()
    stale = (TODAY - timedelta(days=300)).isoformat()
    async with Client(mcp_server) as client:
        project = test_project.name
        await write(
            client,
            project,
            "decision",
            "Use ADR-003 layering",
            authorities=["docs/adr/ADR-003.md"],
            last_verified=verified,
            confidence="high",
            body="# Use ADR-003 layering\n\n- [summary] Layers depend downward only.",
        )
        await write(
            client,
            project,
            "lesson",
            "Run the slice before pushing",
            authorities=["AGENTS.md#14"],
            applies_to=["src/slice"],
            valid_for={"branch": "dev"},
            last_verified=stale,
            confidence="medium",
            body=(
                "# Run the slice before pushing\n\n"
                "- [summary] CI failures were avoidable.\n"
                "- [problem] Pushes without a local run broke CI.\n"
                "- [recommended_action] Run `make slice` first.\n"
                "- [verification] make slice"
            ),
        )
        await write(client, project, "lesson", "Other branch lesson", valid_for={"branch": "main"})
        await write(client, project, "lesson", "Pending lesson", status="draft")
        await write(client, project, "lesson", "Old lesson", status="superseded")
        await write(client, project, "fact", "Other repo fact", repo="elsewhere")
        await write(client, project, "session", "Session 2026-11-13", status="active")

        arguments = {
            "project": project,
            "repo": REPO,
            "branch": "dev",
            "affected_paths": ["src/slice/runner.py"],
            "task": "run the slice before a push",
        }
        text = (await client.call_tool("bm_project_context", arguments)).content[0].text
        data = json.loads(
            (await client.call_tool("bm_project_context", {**arguments, "output_format": "json"}))
            .content[0]
            .text
        )

    assert text.startswith(f"Project context | repo: {REPO} | branch: dev | generated: ")
    assert "POLICY POINTERS (binding; lives in the repo — read it there):" in text
    assert "- docs/adr/ADR-003.md (1 notes)" in text
    assert "- AGENTS.md#14 (1 notes)" in text
    assert "- Use ADR-003 layering: Layers depend downward only." in text
    assert (
        "- Run the slice before pushing: Run `make slice` first. (verify: make slice) "
        f"[last_verified {stale}; confidence medium]"
    ) in text
    assert "Other branch lesson" not in text
    assert "Pending lesson" not in text
    assert "Old lesson" not in text
    assert "Other repo fact" not in text
    assert "EPISODIC:\n" in text and "Session 2026-11-13" not in text.split("EPISODIC:")[0]
    assert "CODE POINTER:" in text
    assert "- 1 drafts awaiting review (" in text
    # The stale lesson and the other-branch lesson, which was never verified.
    assert "- 2 active notes not verified in 90 days" in text
    assert 0 < data["tokens_used"] <= data["budget_tokens"] == 4000
    assert data["hygiene"]["drafts"] == 1
    assert data["hygiene"]["stale"] == 2


@pytest.mark.asyncio
async def test_pack_truncates_to_a_small_budget_and_points_at_the_rest(
    mcp_server, app, test_project
):
    async with Client(mcp_server) as client:
        for index in range(25):
            await write(
                client,
                test_project.name,
                "lesson",
                f"Lesson {index:02d}",
                body=f"# Lesson {index:02d}\n\n- [recommended_action] {'Act carefully. ' * 8}",
            )
        text = (
            (
                await client.call_tool(
                    "bm_project_context",
                    {"project": test_project.name, "repo": REPO, "budget_tokens": 500},
                )
            )
            .content[0]
            .text
        )

    assert (
        f'… continue with bm_facts(kind="lesson", project="{test_project.name}", repo="{REPO}")'
    ) in text
    assert text.count("- Lesson ") < 25


@pytest.mark.asyncio
async def test_facts_pages_a_kind_and_triages_drafts(mcp_server, app, test_project):
    async with Client(mcp_server) as client:
        for index in range(3):
            await write(client, test_project.name, "bug", f"Bug {index}")
        await write(client, test_project.name, "bug", "Draft bug", status="draft")

        first = (
            (
                await client.call_tool(
                    "bm_facts",
                    {"kind": "bug", "project": test_project.name, "repo": REPO, "page_size": 2},
                )
            )
            .content[0]
            .text
        )
        drafts = (
            (
                await client.call_tool(
                    "bm_facts",
                    {"kind": "bug", "project": test_project.name, "status": "draft"},
                )
            )
            .content[0]
            .text
        )
        refused = await client.call_tool(
            "bm_facts",
            {
                "kind": "bug",
                "project": test_project.name,
                "repo": REPO,
                "filters": {"repo": "x"},
            },
            raise_on_error=False,
        )

    assert "More results available. Call bm_facts(kind='bug', repo='monolith-breaker'" in first
    assert first.count("\n- ") == 2
    assert "Draft bug" in drafts and "Bug 0" not in drafts
    assert refused.is_error
    assert "`filters` has 'repo', which `repo` already sets." in refused.content[0].text


@pytest.mark.asyncio
async def test_unknown_kind_is_rejected_by_the_schema(mcp_server, app, test_project):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "bm_facts", {"kind": "rumor", "project": test_project.name}, raise_on_error=False
        )

    assert result.is_error
