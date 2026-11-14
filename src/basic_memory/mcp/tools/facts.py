"""bm_facts: list curated knowledge notes of one kind.

A thin wrapper over the project search: the kind becomes the note type, `repo` and
`status` become frontmatter filters, and each hit is read once more so the lifecycle
fields (`status`, `last_verified`, `confidence`) can be shown beside its path and
title. `bm_project_context` composes the same reads into a session-start pack.
"""

from typing import Annotated, Any

from fastmcp import Context
from pydantic import Field

from basic_memory.mcp.index_readiness import project_index_required
from basic_memory.mcp.memory_records import (
    FACT_STATUSES,
    FactKind,
    FactStatus,
    MemoryRecord,
    fetch_records,
)
from basic_memory.mcp.note_targets import open_project_target
from basic_memory.mcp.refusals import refusal
from basic_memory.mcp.server import mcp
from basic_memory.mcp.tool_params import (
    Filters,
    OutputFormat,
    Page,
    PageSize,
    ProjectParam,
)

# `type` is set by `kind`; `repo` and `status` by their own arguments. A filter on any
# of them while the argument is also given would be ANDed with it or silently lose to
# it, and neither is what the caller wrote.
_KIND_KEYS = ("type", "note_type")


@mcp.tool(
    name="bm_facts",
    title="List project knowledge",
    description=(
        "List curated knowledge notes of one `kind` (fact, bug, lesson, best_practice, "
        "decision, glossary_term) in a Basic Memory project, most recently edited first. "
        "`repo` limits them to one code repository; `status` limits them to one lifecycle "
        "status ('draft' lists notes awaiting review; omit it for every status). Each line "
        "shows the note's path, title, status, last_verified and confidence. `filters` "
        "adds frontmatter conditions. Read a note with bm_read; search by text with "
        "bm_search; fetch a session-start pack with bm_project_context."
    ),
    tags={"search"},
    annotations={
        "title": "List project knowledge",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def bm_facts(
    kind: Annotated[FactKind, Field(description="Which kind of knowledge note to list.")],
    repo: Annotated[
        str | None,
        Field(min_length=1, description="Only notes about this code repository (`repo:`)."),
    ] = None,
    status: Annotated[
        FactStatus | None,
        Field(
            description=(
                f"Only notes with this status: {', '.join(FACT_STATUSES[:-1])} "
                f"or {FACT_STATUSES[-1]}. Omit for all."
            ),
        ),
    ] = None,
    filters: Filters = None,
    page: Page = 1,
    page_size: PageSize = 20,
    output_format: OutputFormat = "text",
    *,
    project: ProjectParam,
    context: Context | None = None,
) -> str | dict[str, Any]:
    """List knowledge notes of one kind."""
    # --- Argument combinations, before any I/O ---
    # Trigger: `filters` names a field that `kind`, `repo` or `status` already sets.
    # Why: the caller's two statements about one field can disagree; honoring either
    #      would answer a question they did not ask.
    # Outcome: a refusal naming the field and the argument that owns it.
    owned = {key: "kind" for key in _KIND_KEYS}
    if repo is not None:
        owned["repo"] = "repo"
    if status is not None:
        owned["status"] = "status"
    for key, argument in owned.items():
        if filters and key in filters:
            raise refusal(
                "INVALID_ARGUMENTS",
                f"`filters` has '{key}', which `{argument}` already sets.",
                try_instead=f"give it only as `{argument}`, or drop it from `filters`",
                output_format=output_format,
            )
    metadata_filters = dict(filters or {})
    if repo is not None:
        metadata_filters["repo"] = repo

    async with open_project_target("", project, context, output_format=output_format) as target:
        found = await fetch_records(
            target,
            kind=kind,
            metadata_filters=metadata_filters,
            status=status,
            page=page,
            page_size=page_size,
        )
        # An empty page is a trustworthy miss only after the project has been indexed.
        if not found.records:
            guidance = await project_index_required(target.client, target.project)
            if guidance is not None:
                return (
                    {"results": [], "guidance": guidance} if output_format == "json" else guidance
                )

    if output_format == "json":
        return {
            "results": [record.to_json() for record in found.records],
            "page": found.pagination.page,
            "page_size": found.pagination.page_size,
            "has_more": found.pagination.has_more,
        }
    next_call = found.pagination.next_call(
        "bm_facts",
        {"kind": kind, "repo": repo, "status": status, "filters": filters, "project": project},
    )
    return _format_records(
        found.records,
        kind=kind,
        page=found.pagination.page,
        scope=_scope(repo, status),
        next_call=next_call,
    )


def _scope(repo: str | None, status: str | None) -> str:
    parts = [f"repo '{repo}'"] if repo is not None else []
    if status is not None:
        parts.append(f"status '{status}'")
    return f" with {' and '.join(parts)}" if parts else ""


def _format_records(
    records: tuple[MemoryRecord, ...],
    *,
    kind: str,
    page: int,
    scope: str,
    next_call: str | None,
) -> str:
    """One line per note: path, title, status, then last_verified and confidence if set."""
    if not records:
        return f"No {kind} notes{scope}."
    lines = [f"{len(records)} {kind} notes shown{scope} (page {page}, total unknown):", ""]
    for record in records:
        line = f"- {record.file_path} | {record.title} | status {record.status or 'none'}"
        if record.last_verified is not None:
            line += f" | last_verified {record.last_verified}"
        if record.confidence is not None:
            line += f" | confidence {record.confidence}"
        lines.append(line)
    if next_call is not None:
        lines.extend(["", f"More results available. Call {next_call} to continue."])
    return "\n".join(lines)
