"""bm_project_context: a session-start pack of what a project remembers about one repository.

The pack composes searches the other tools already make: notes of each kind for the
repository (`repo` frontmatter), counts of drafts and unverified notes, and the latest
session notes. It adds no endpoint. `pack_composition` decides what fits the token budget.
"""

import re
from datetime import datetime, timezone
from typing import Annotated, Any

from fastmcp import Context
from pydantic import BeforeValidator, Field

from basic_memory.mcp.index_readiness import project_index_required
from basic_memory.mcp.memory_records import (
    FACT_KINDS,
    MemoryRecord,
    count_notes,
    fetch_records,
    search_notes,
)
from basic_memory.mcp.note_targets import ProjectTarget, open_project_target
from basic_memory.mcp.pack_composition import (
    MAX_BUDGET_TOKENS,
    PACK_ORDER,
    RENDERABLE_STATUSES,
    KindNotes,
    PackData,
    PackRequest,
    compose_pack,
    select_records,
    stale_cutoff,
)
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import OutputFormat, ProjectParam
from basic_memory.utils import coerce_list

# Notes read per kind. A kind with more matching notes ends with a bm_facts call.
NOTES_PER_KIND = 50
SESSIONS_SHOWN = 5
# Kinds the task text ranks: what happened and what to do about it.
TASK_RANKED_KINDS = frozenset({"lesson", "bug"})
_QUERY_OPERATORS = frozenset({"AND", "OR", "NOT", "NEAR"})
_MAX_TASK_TERMS = 12


@mcp.tool(
    name="bm_project_context",
    title="Project context pack",
    description=(
        "Fetch at session start; complements repository AGENTS.md. Returns a bounded pack "
        "of what the Basic Memory `project` remembers about one code repository, `repo`: "
        "pointers to the rules that bind (they live in the repo, so read them there), "
        "recalled decisions, bugs, lessons, best practices, facts and glossary terms as "
        "non-binding evidence to verify, the latest sessions, and a count of drafts and "
        "unverified notes. `project` is the Basic Memory project that holds the notes; "
        "`repo` is the code repository they are about. `task` ranks lessons and bugs by "
        "text match; `affected_paths` ranks notes whose `applies_to` covers them; `branch` "
        "drops notes that hold only on another branch. Sections that do not fit "
        "`budget_tokens` end with the call that fetches the rest. Read-only."
    ),
    tags={"context"},
    annotations={
        "title": "Project context pack",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def bm_project_context(
    repo: Annotated[
        str,
        Field(
            min_length=1,
            description="The code repository the notes are about (their `repo:` value).",
        ),
    ],
    task: Annotated[
        str | None,
        Field(description="What the session will do; lessons and bugs matching it come first."),
    ] = None,
    affected_paths: Annotated[
        list[str] | None,
        BeforeValidator(coerce_list),
        Field(
            description=(
                "Repository paths the work touches; notes whose `applies_to` covers them "
                "rank first."
            )
        ),
    ] = None,
    branch: Annotated[
        str | None,
        Field(
            min_length=1,
            description=(
                "Current branch; notes limited to another branch (`valid_for.branch`) are left out."
            ),
        ),
    ] = None,
    budget_tokens: Annotated[
        int,
        Field(
            ge=500,
            le=MAX_BUDGET_TOKENS,
            description=(
                "Approximate size of the pack in tokens (500-20000), estimated at 4 "
                "characters each."
            ),
        ),
    ] = 4000,
    recheck_days: Annotated[
        int,
        Field(ge=1, description="A note not verified within this many days counts as stale."),
    ] = 90,
    output_format: OutputFormat = "text",
    *,
    project: ProjectParam,
    context: Context | None = None,
) -> str | dict[str, Any]:
    """Compose the project context pack for one repository."""
    request = PackRequest(
        project=project,
        repo=repo,
        branch=branch,
        affected_paths=tuple(affected_paths or ()),
        budget_tokens=budget_tokens,
        recheck_days=recheck_days,
        generated_at=datetime.now(timezone.utc),
        task=task,
    )
    async with open_project_target("", project, context, output_format=output_format) as target:
        data = await _gather(target, request)
        # An empty pack is a trustworthy miss only after the project has been indexed.
        if not (any(notes.records for notes in data.kinds) or data.session_titles or data.drafts):
            guidance = await project_index_required(target.client, target.project)
            if guidance is not None:
                return (
                    {"results": [], "guidance": guidance} if output_format == "json" else guidance
                )

    pack = compose_pack(request, data)
    return pack.to_json() if output_format == "json" else pack.to_text()


def _task_query(task: str | None) -> str | None:
    """The task as an OR of its words, so a note matching any of them is found.

    Search joins bare words with AND, which no sentence satisfies; query operators
    written in the task are dropped so the words stay words.
    """
    if task is None:
        return None
    words = dict.fromkeys(word.lower() for word in re.findall(r"\w{3,}", task))
    terms = [word for word in words if word.upper() not in _QUERY_OPERATORS]
    if not terms:
        return None
    return " OR ".join(f'"{term}"' for term in terms[:_MAX_TASK_TERMS])


def _unique(records: list[MemoryRecord]) -> list[MemoryRecord]:
    return list({record.external_id: record for record in records}.values())


async def _gather(target: ProjectTarget, request: PackRequest) -> PackData:
    """Run the searches the pack is made of, one kind at a time."""
    task_query = _task_query(request.task)
    kinds: list[KindNotes] = []
    for kind in PACK_ORDER:
        # A fixed bug is shown only when the task or paths point at it, which the
        # selection decides; the search returns both so it can.
        status = {"$in": sorted(RENDERABLE_STATUSES)} if kind == "bug" else "active"
        filters = {"repo": request.repo, "status": status}
        latest = await fetch_records(
            target, kind=kind, metadata_filters=filters, page_size=NOTES_PER_KIND
        )
        candidates = list(latest.records)
        text_order: list[str] = []
        if task_query is not None and kind in TASK_RANKED_KINDS:
            # Trigger: the task names words and this kind is ranked by them.
            # Why: the most recent notes may not include the ones that match the task.
            # Outcome: matches join the candidates and sort ahead of the rest.
            matched = await fetch_records(
                target,
                kind=kind,
                metadata_filters=filters,
                text=task_query,
                page_size=NOTES_PER_KIND,
            )
            text_order = [record.external_id for record in matched.records]
            candidates = _unique(candidates + list(matched.records))
        kinds.append(
            KindNotes(
                kind=kind,
                records=select_records(
                    candidates,
                    text_order=text_order,
                    affected_paths=request.affected_paths,
                    branch=request.branch,
                ),
                more_upstream=latest.pagination.has_more,
            )
        )

    repo_filter = {"repo": request.repo}
    sessions = await search_notes(
        target, note_types=["session"], metadata_filters=repo_filter, page_size=SESSIONS_SHOWN
    )
    drafts = await count_notes(
        target, note_types=list(FACT_KINDS), metadata_filters=repo_filter, status="draft"
    )
    # A note is stale when its last_verified is older than the cutoff or missing. The
    # two cases are disjoint, so their counts add.
    cutoff = stale_cutoff(request.generated_at, request.recheck_days)
    stale = 0
    for freshness in ({"$lt": cutoff}, None):
        stale += await count_notes(
            target,
            note_types=list(FACT_KINDS),
            metadata_filters={**repo_filter, "last_verified": freshness},
            status="active",
        )
    return PackData(
        kinds=tuple(kinds),
        session_titles=tuple(hit.title for hit in sessions.results),
        drafts=drafts,
        stale=stale,
    )
