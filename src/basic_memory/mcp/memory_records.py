"""Curated knowledge notes, read for bm_facts and bm_project_context.

A project that keeps agent memory in Basic Memory writes one note per fact, bug,
lesson, best practice, decision or glossary term, tagged in frontmatter with the code
repository it is about, its lifecycle status, where the binding rule lives
(`authorities`), when it was last checked (`last_verified`) and how sure the reviewer
was (`confidence`). This module reads those notes through the existing search and
accepted-content endpoints and gives them a typed shape. It adds no endpoint and no
query of its own: the index answers the search, the accepted Markdown answers the
frontmatter and observation reads.
"""

import re
from dataclasses import asdict, dataclass
from typing import Any, Literal, get_args

from fastmcp.exceptions import ToolError

from basic_memory.file_utils import has_frontmatter, remove_frontmatter
from basic_memory.mcp.clients import KnowledgeClient, SearchClient
from basic_memory.mcp.metadata_find import projection_metadata
from basic_memory.mcp.note_targets import PathQualifier, ProjectTarget
from basic_memory.mcp.tool_params import MAX_PAGE_SIZE, Pagination
from basic_memory.schemas.search import SearchItemType, SearchQuery, SearchResponse, SearchResult

type FactKind = Literal["fact", "bug", "lesson", "best_practice", "decision", "glossary_term"]

FACT_KINDS: tuple[str, ...] = get_args(FactKind.__value__)

# The lifecycle states a knowledge note moves through (schema v2). The one list that
# `bm_facts` validates against, the pack selects from and the manual describes.
type FactStatus = Literal["draft", "active", "fixed", "superseded", "invalidated", "promoted"]

FACT_STATUSES: tuple[str, ...] = get_args(FactStatus.__value__)

# The index orders rows of equal score by `updated_at`, newest first, only when the
# query carries an `after_date`. Filter-only searches score every row zero, so this
# earliest-possible date turns "any order" into "most recently edited first" without
# excluding a note.
ALL_TIME = "1970-01-01"

# Rows per request when counting: the most one request may return.
COUNT_PAGE_SIZE = MAX_PAGE_SIZE

# `- [category] text`, the observation form notes use for structured lines.
_OBSERVATION = re.compile(r"^\s*[-*+]\s+\[([^\[\]()]+)\]\s+(.+?)\s*$")
_BULLET_PREFIX = re.compile(r"^\s*[-*+]\s+(?:\[[^\[\]()]+\]\s+)?")
_WHITESPACE = re.compile(r"\s+")


def one_line(text: str, limit: int = 200) -> str:
    """Collapse whitespace and cut `text` to `limit` characters, marking the cut."""
    collapsed = _WHITESPACE.sub(" ", text).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """One knowledge note: its identity, lifecycle fields and the lines worth quoting."""

    kind: str
    title: str
    file_path: str
    permalink: str | None
    external_id: str
    status: str | None
    repo: str | None
    authorities: tuple[str, ...]
    applies_to: tuple[str, ...]
    # `valid_for.branch`: None means the note holds on every branch.
    branch: str | None
    last_verified: str | None
    confidence: str | None
    # The `[summary]` observation, or the first line of the body when there is none.
    summary: str | None
    recommended_action: str | None
    verification: str | None

    def to_json(self) -> dict[str, Any]:
        """The record as a JSON object: sequences become lists."""
        return {
            name: list(value) if isinstance(value, tuple) else value
            for name, value in asdict(self).items()
        }


@dataclass(frozen=True, slots=True)
class RecordPage:
    """One page of records and the continuation the search reported for it."""

    records: tuple[MemoryRecord, ...]
    pagination: Pagination


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _texts(value: Any) -> tuple[str, ...]:
    """A frontmatter field that is a list, or a single string standing for a one-item list."""
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return (str(value),)


def _branch(valid_for: Any, file_path: str) -> str | None:
    """`valid_for.branch`: one branch name, or None when the note holds on every branch."""
    if not isinstance(valid_for, dict):
        return None
    branch = valid_for.get("branch")
    # A list would read as one odd name that matches no branch, silently hiding the
    # note from every branch-scoped pack. Refuse it so the note gets fixed.
    if isinstance(branch, (list, dict)):
        raise ToolError(
            f"{file_path}: `valid_for.branch` must be one branch name, not a "
            f"{type(branch).__name__}. Edit the note to name a single branch, or drop it."
        )
    return _text(branch)


def _observations(body: str) -> dict[str, str]:
    """The first observation of each category in a note body."""
    found: dict[str, str] = {}
    for line in body.splitlines():
        match = _OBSERVATION.match(line)
        if match:
            found.setdefault(match.group(1).strip().lower(), match.group(2))
    return found


def _first_body_line(body: str) -> str | None:
    """The first line of prose: not blank, not a heading."""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return _BULLET_PREFIX.sub("", stripped, count=1)
    return None


def parse_record(
    kind: str, hit: SearchResult, content: str | None, paths: PathQualifier
) -> MemoryRecord:
    """Read a search hit and its accepted Markdown as one record.

    A hit with no accepted Markdown, or with frontmatter that does not parse, still
    yields a record from the index's own title and path; its lifecycle fields are
    then absent and the note reads as unverified.
    """
    if hit.external_id is None:
        raise ToolError("Search hits carry no external_id; this server is too old for bm_facts.")
    identity: dict[str, Any] = {"title": hit.title}
    frontmatter = projection_metadata(content, identity)
    body = ""
    if content is not None:
        body = remove_frontmatter(content) if has_frontmatter(content) else content
    observations = _observations(body)
    return MemoryRecord(
        kind=kind,
        title=hit.title,
        file_path=paths.path(hit.file_path),
        permalink=hit.permalink,
        external_id=hit.external_id,
        status=_text(frontmatter.get("status")),
        repo=_text(frontmatter.get("repo")),
        authorities=_texts(frontmatter.get("authorities")),
        applies_to=_texts(frontmatter.get("applies_to")),
        branch=_branch(frontmatter.get("valid_for"), hit.file_path),
        last_verified=_text(frontmatter.get("last_verified")),
        confidence=_text(frontmatter.get("confidence")),
        summary=observations.get("summary") or _first_body_line(body),
        recommended_action=observations.get("recommended_action"),
        verification=observations.get("verification"),
    )


async def search_notes(
    target: ProjectTarget,
    *,
    note_types: list[str],
    metadata_filters: dict[str, Any] | None = None,
    status: str | None = None,
    text: str | None = None,
    page: int = 1,
    page_size: int,
) -> SearchResponse:
    """One full-text search over notes of the given types, most recently edited first.

    Without `text` the rows all score zero, so `ALL_TIME` makes the index order them by
    edit time. With `text`, relevance orders them and edit time breaks ties.
    """
    query = SearchQuery(
        text=text,
        note_types=note_types,
        entity_types=[SearchItemType.ENTITY],
        metadata_filters=metadata_filters or None,
        status=status,
        after_date=ALL_TIME,
    )
    return await SearchClient(target.client, target.project.external_id).search(
        query.model_dump(), page=page, page_size=page_size
    )


async def count_notes(
    target: ProjectTarget,
    *,
    note_types: list[str],
    metadata_filters: dict[str, Any] | None = None,
    status: str | None = None,
) -> int:
    """How many notes match, counted page by page.

    The search reports only a lower bound for its total, never an exact count, so the
    exact figure is the number of rows across all pages.
    """
    count = 0
    page = 1
    while True:
        response = await search_notes(
            target,
            note_types=note_types,
            metadata_filters=metadata_filters,
            status=status,
            page=page,
            page_size=COUNT_PAGE_SIZE,
        )
        count += len(response.results)
        if not response.has_more:
            return count
        page += 1


async def fetch_records(
    target: ProjectTarget,
    *,
    kind: str,
    metadata_filters: dict[str, Any] | None = None,
    status: str | None = None,
    text: str | None = None,
    page: int = 1,
    page_size: int,
) -> RecordPage:
    """One page of notes of `kind`, each read from its accepted Markdown."""
    response = await search_notes(
        target,
        note_types=[kind],
        metadata_filters=metadata_filters,
        status=status,
        text=text,
        page=page,
        page_size=page_size,
    )
    contents = await KnowledgeClient(
        target.client, target.project.external_id
    ).accepted_note_contents([hit.external_id for hit in response.results if hit.external_id])
    paths = target.paths
    return RecordPage(
        records=tuple(
            parse_record(kind, hit, contents.get(hit.external_id), paths)
            for hit in response.results
        ),
        pagination=Pagination(
            page=response.current_page,
            page_size=response.page_size,
            has_more=response.has_more,
        ),
    )
