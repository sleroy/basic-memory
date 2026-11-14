"""Composing the session-start context pack: selection, ranking and the token budget.

Pure functions over typed values. `bm_project_context` gathers the notes; this module
decides which of them the pack shows, in what order, and where the budget cuts it.

The pack carries two different kinds of statement and labels them differently. Policy
pointers say where the repository's binding rules live; they are pointers only. Recalled
knowledge is evidence from earlier work: it may be stale, and the repository outranks it.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from basic_memory.markdown.sections import CHARS_PER_TOKEN
from basic_memory.mcp.memory_records import FactStatus, MemoryRecord, one_line

# --- Vocabulary ---

# The order sections fill the budget in: what constrains the work comes first.
PACK_ORDER: tuple[str, ...] = (
    "decision",
    "bug",
    "lesson",
    "best_practice",
    "fact",
    "glossary_term",
)
KIND_HEADINGS: dict[str, str] = {
    "decision": "Decisions:",
    "bug": "Bugs (active; fixed ones only when they match the task or paths):",
    "lesson": "Lessons:",
    "best_practice": "Best practices:",
    "fact": "Facts:",
    "glossary_term": "Glossary terms:",
}

# Only these lifecycle states are ever shown. Drafts await review; superseded and
# invalidated notes stopped being true.
RENDERABLE_STATUSES: frozenset[FactStatus] = frozenset({"active", "fixed"})

MAX_BUDGET_TOKENS = 20000

POLICY_LABEL = "POLICY POINTERS (binding; lives in the repo — read it there):"
RECALLED_LABEL = (
    "RECALLED KNOWLEDGE (non-binding evidence; verify before acting; "
    "the repository outranks memory):"
)
EPISODIC_LABEL = "EPISODIC:"
CODE_POINTER_LABEL = "CODE POINTER:"
CODE_POINTER_TEXT = (
    "For symbols, callers and dependencies of these paths use deterministic code "
    "intelligence (LSP/codegraph); memory does not store code structure."
)
HYGIENE_LABEL = "HYGIENE:"
EMPTY_SECTION = "(none)"
OVER_BUDGET_NOTE = "the header and hygiene lines alone exceed it, so no section is shown"


# --- Tokens ---


def estimate_tokens(text: str) -> int:
    """Approximate tokens at the repository's shared characters-per-token ratio."""
    return -(-len(text) // CHARS_PER_TOKEN)


def _line_cost(line: str) -> int:
    return estimate_tokens(line + "\n")


# --- Selecting and ranking notes ---


def _normalize_path(path: str) -> str:
    return path.strip().removeprefix("./").rstrip("/")


def paths_overlap(left: str, right: str) -> bool:
    """Whether one path is the other or lies under it, compared by whole segments."""
    first, second = _normalize_path(left), _normalize_path(right)
    return first == second or first.startswith(f"{second}/") or second.startswith(f"{first}/")


def select_records(
    records: Sequence[MemoryRecord],
    *,
    text_order: Sequence[str],
    affected_paths: Sequence[str],
    branch: str | None,
) -> tuple[MemoryRecord, ...]:
    """The notes the pack may show, best match first.

    `text_order` is the external ids of the notes that matched the task text, in the
    search's relevance order. Notes keep the search's own order (most recently edited
    first) within equal rank.

    A note is kept when its status is renderable and, if `branch` is given, its
    `valid_for.branch` is that branch or absent. A fixed bug is kept only when the task
    text or the affected paths point at it.
    """
    text_rank = {external_id: rank for rank, external_id in enumerate(text_order)}
    ranked: list[tuple[int, int, MemoryRecord]] = []
    for record in records:
        if record.status not in RENDERABLE_STATUSES:
            continue
        if branch is not None and record.branch is not None and record.branch != branch:
            continue
        on_path = any(
            paths_overlap(entry, path) for entry in record.applies_to for path in affected_paths
        )
        text_match = record.external_id in text_rank
        if record.status == "fixed" and not (text_match or on_path):
            continue
        ranked.append(
            (text_rank.get(record.external_id, len(text_rank)), 0 if on_path else 1, record)
        )
    ranked.sort(key=lambda item: item[:2])
    return tuple(record for *_, record in ranked)


def stale_cutoff(generated_at: datetime, recheck_days: int) -> str:
    """The date before which a `last_verified` is stale. ISO dates compare as strings."""
    return (generated_at.date() - timedelta(days=recheck_days)).isoformat()


# --- Inputs and outputs ---


@dataclass(frozen=True, slots=True)
class PackRequest:
    """What the caller asked the pack for."""

    project: str
    repo: str
    branch: str | None
    affected_paths: tuple[str, ...]
    budget_tokens: int
    recheck_days: int
    generated_at: datetime
    task: str | None = None


@dataclass(frozen=True, slots=True)
class KindNotes:
    """The selected notes of one kind, and whether the search had more than it returned."""

    kind: str
    records: tuple[MemoryRecord, ...]
    more_upstream: bool


@dataclass(frozen=True, slots=True)
class PackData:
    """Everything gathered for one pack, before the budget is applied."""

    kinds: tuple[KindNotes, ...]
    session_titles: tuple[str, ...]
    drafts: int
    stale: int


@dataclass(frozen=True, slots=True)
class Block:
    """The lines one group contributed, and the call that fetches what was left out.

    `continuation` is None when nothing was left out.
    """

    name: str
    heading: str | None
    lines: tuple[str, ...]
    continuation: str | None

    def is_empty(self) -> bool:
        return not self.lines and self.continuation is None


@dataclass(frozen=True, slots=True)
class ContextPack:
    """A composed pack: its parts, ready to render as text or JSON."""

    request: PackRequest
    policy: Block
    recalled: tuple[Block, ...]
    episodic: Block
    drafts: int
    stale: int
    tokens_used: int

    def to_text(self) -> str:
        return "\n".join(
            _render(
                self.request,
                self.tokens_used,
                self.policy,
                self.recalled,
                self.episodic,
                self.drafts,
                self.stale,
            )
        )

    def to_json(self) -> dict[str, Any]:
        request = self.request
        return {
            "repo": request.repo,
            "branch": request.branch,
            "generated_at": _timestamp(request.generated_at),
            "tokens_used": self.tokens_used,
            "budget_tokens": request.budget_tokens,
            "policy_pointers": _block_json(self.policy),
            "recalled_knowledge": {block.name: _block_json(block) for block in self.recalled},
            "episodic": _block_json(self.episodic),
            "code_pointer": CODE_POINTER_TEXT if request.affected_paths else None,
            "hygiene": {
                "drafts": self.drafts,
                "stale": self.stale,
                "recheck_days": request.recheck_days,
                "stale_before": stale_cutoff(request.generated_at, request.recheck_days),
            },
        }


def _block_json(block: Block) -> dict[str, Any]:
    return {"lines": list(block.lines), "continuation": block.continuation}


def _timestamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


# --- Lines ---


def _arguments(**arguments: object) -> str:
    return ", ".join(
        f"{name}={json.dumps(value, ensure_ascii=False)}" for name, value in arguments.items()
    )


def _call(tool: str, **arguments: object) -> str:
    """A tool call as the model should write it, e.g. `bm_facts(kind="lesson", project="p")`."""
    return f"{tool}({_arguments(**arguments)})"


def _continue_with(call: str) -> str:
    return f"… continue with {call}"


def _policy_continuation(request: PackRequest) -> str:
    """How to get the policy pointers the budget cut.

    A larger budget returns the same pack with more room, so the call carries the
    arguments that shaped this one. At the maximum budget there is nothing larger to
    ask for; the pointers come from the notes, which `bm_facts` lists per kind.
    """
    scope = {"project": request.project, "repo": request.repo}
    if request.budget_tokens >= MAX_BUDGET_TOKENS:
        example = _call("bm_facts", kind="decision", status="active", **scope)
        return (
            "… budget_tokens is at its maximum; the pointers come from the notes, so list "
            f"them with one bm_facts call per kind, e.g. {example}"
        )
    carried: dict[str, object] = {}
    if request.branch is not None:
        carried["branch"] = request.branch
    if request.task is not None:
        carried["task"] = request.task
    if request.affected_paths:
        carried["affected_paths"] = list(request.affected_paths)
    widened = min(request.budget_tokens * 2, MAX_BUDGET_TOKENS)
    return _continue_with(_call("bm_project_context", **scope, **carried, budget_tokens=widened))


def _kind_continuation(kind: str, request: PackRequest) -> str:
    """The call that lists a kind's notes, scoped to the statuses the pack draws from.

    `bm_facts` starts at the first note, so the list repeats the notes shown above, and
    it has no branch filter, so a pack built for a branch says which notes to skip.
    """
    status_scope: dict[str, object] = (
        {"filters": {"status": {"$in": sorted(RENDERABLE_STATUSES)}}}
        if kind == "bug"
        else {"status": "active"}
    )
    call = _call("bm_facts", kind=kind, **status_scope, project=request.project, repo=request.repo)
    note = "full list; includes notes shown above"
    if request.branch is not None:
        note += f"; skip valid_for.branch other than {json.dumps(request.branch)}"
    return f"{_continue_with(call)} ({note})"


def item_line(record: MemoryRecord) -> str:
    """One note as one line: title, what it says, how fresh it is, where to read it.

    A lesson says what to do (`[recommended_action]`, then `[verification]` if the note
    has one) rather than what happened.
    """
    if record.kind == "lesson":
        detail = record.recommended_action or record.summary
    else:
        detail = record.summary
    text = f"{record.title}: {one_line(detail)}" if detail else record.title
    if record.kind == "lesson" and record.verification:
        text += f" (verify: {one_line(record.verification)})"
    tag = "[fixed] " if record.status == "fixed" else ""
    verified = f"last_verified {record.last_verified}" if record.last_verified else "unverified"
    confidence = record.confidence or "n/a"
    return f"- {tag}{text} [{verified}; confidence {confidence}] ({record.file_path})"


def policy_lines(kinds: Sequence[KindNotes]) -> tuple[str, ...]:
    """The distinct `authorities` of the shown notes, most cited first, with counts."""
    counts: dict[str, int] = {}
    for notes in kinds:
        for record in notes.records:
            for authority in dict.fromkeys(record.authorities):
                counts[authority] = counts.get(authority, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return tuple(f"- {authority} ({count} notes)" for authority, count in ordered)


# --- The budget ---


@dataclass(frozen=True, slots=True)
class _Group:
    name: str
    heading: str | None
    lines: tuple[str, ...]
    continuation: str
    more_upstream: bool


def _fill(groups: Sequence[_Group], available: int) -> list[Block]:
    """Fill groups in priority order from `available` tokens, then stop.

    A group the budget cut, or that the search had more of, ends with its continuation
    call. Once the budget cuts a group, every later group shows only its heading and
    continuation: the pack is a prefix of the priority order, so a low-priority note
    never appears while a higher one is missing. Each later group keeps room for that
    heading and continuation, and a group whose heading and continuation no longer fit
    is left out whole, so the pack never exceeds `available`.
    """
    live = [group for group in groups if group.lines or group.more_upstream]
    blocks: dict[str, Block] = {
        group.name: Block(group.name, group.heading, (), None) for group in groups
    }
    remaining = available
    exhausted = False
    for position, group in enumerate(live):
        continuation_cost = _line_cost(group.continuation)
        heading_cost = _line_cost(group.heading) if group.heading else 0
        reserved_later = sum(
            (_line_cost(later.heading) if later.heading else 0) + _line_cost(later.continuation)
            for later in live[position + 1 :]
        )
        room = remaining - reserved_later - heading_cost
        kept: list[str] = []
        for index, line in enumerate(group.lines):
            if exhausted:
                break
            # The last line needs no room for a continuation when nothing follows it.
            final = index == len(group.lines) - 1 and not group.more_upstream
            needed = _line_cost(line) + (0 if final else continuation_cost)
            if needed > room:
                break
            kept.append(line)
            room -= _line_cost(line)
        out_of_budget = len(kept) < len(group.lines)
        exhausted = exhausted or out_of_budget
        cut = out_of_budget or group.more_upstream
        spent = heading_cost + sum(_line_cost(line) for line in kept)
        if cut:
            spent += continuation_cost
        # Trigger: not even the heading and continuation fit in what is left.
        # Why: earlier groups' long lines (or long names repeated in every
        #      continuation) can use the budget up; emitting anyway overruns it.
        # Outcome: the group stays empty and is not rendered; later groups cannot fit
        #          either, so they are left out the same way.
        if spent > remaining:
            exhausted = True
            continue
        remaining -= spent
        blocks[group.name] = Block(
            group.name, group.heading, tuple(kept), group.continuation if cut else None
        )
    return [blocks[group.name] for group in groups]


# --- Rendering ---


def _section(label: str, blocks: Sequence[Block]) -> list[str]:
    body: list[str] = []
    for block in blocks:
        if block.is_empty():
            continue
        if block.heading is not None:
            body.append(block.heading)
        body.extend(block.lines)
        if block.continuation is not None:
            body.append(block.continuation)
    return [label, *(body or [EMPTY_SECTION]), ""]


def _render(
    request: PackRequest,
    tokens_used: int,
    policy: Block,
    recalled: Sequence[Block],
    episodic: Block,
    drafts: int,
    stale: int,
) -> list[str]:
    header = (
        f"Project context | repo: {request.repo} | branch: {request.branch or 'any'} | "
        f"generated: {_timestamp(request.generated_at)} | "
        f"tokens: {tokens_used}/{request.budget_tokens} (estimate, chars/{CHARS_PER_TOKEN})"
    )
    if tokens_used > request.budget_tokens:
        # The header, labels and hygiene lines are never cut; only long names can push
        # them past the smallest budget, and then no section has room.
        header += f" | over budget: {OVER_BUDGET_NOTE}"
    lines = [header, ""]
    lines += _section(POLICY_LABEL, [policy])
    lines += _section(RECALLED_LABEL, recalled)
    lines += _section(EPISODIC_LABEL, [episodic])
    if request.affected_paths:
        lines += [CODE_POINTER_LABEL, CODE_POINTER_TEXT, ""]
    triage = _arguments(status="draft", project=request.project, repo=request.repo)
    cutoff = stale_cutoff(request.generated_at, request.recheck_days)
    lines += [
        HYGIENE_LABEL,
        f"- {drafts} drafts awaiting review (one bm_facts call per kind, with {triage})",
        f"- {stale} active notes not verified in {request.recheck_days} days "
        f"(last_verified absent or before {cutoff})",
    ]
    return lines


def compose_pack(request: PackRequest, data: PackData) -> ContextPack:
    """Apply the budget to gathered notes and return the pack.

    The header, labels, code pointer and hygiene lines are always shown; the rest of the
    budget is filled with policy pointers, then recalled knowledge, then recent sessions.
    When names are so long that the always-shown lines alone exceed the budget, no
    section is shown and the header says so.
    """
    groups = [
        _Group(
            "policy",
            None,
            policy_lines(data.kinds),
            _policy_continuation(request),
            False,
        ),
        *(
            _Group(
                notes.kind,
                KIND_HEADINGS[notes.kind],
                tuple(item_line(record) for record in notes.records),
                _kind_continuation(notes.kind, request),
                notes.more_upstream,
            )
            for notes in data.kinds
        ),
        _Group(
            "episodic",
            None,
            tuple(f"- {title}" for title in data.session_titles),
            _continue_with(
                _call(
                    "bm_search",
                    note_types=["session"],
                    filters={"repo": request.repo},
                    project=request.project,
                )
            ),
            False,
        ),
    ]
    empty = Block("", None, (), None)
    # The frame is measured with the widest possible header (used = budget), so the
    # reported tokens_used never understates the text by more than the header's digits.
    frame = _render(request, request.budget_tokens, empty, (), empty, data.drafts, data.stale)
    available = request.budget_tokens - sum(_line_cost(line) for line in frame)
    policy, *rest = _fill(groups, available)
    *recalled, episodic = rest
    lines = _render(
        request, request.budget_tokens, policy, recalled, episodic, data.drafts, data.stale
    )
    tokens_used = sum(_line_cost(line) for line in lines)
    if tokens_used > request.budget_tokens:
        # Only the frame itself can overrun, and the header then says so; count that.
        lines = _render(request, tokens_used, policy, recalled, episodic, data.drafts, data.stale)
        tokens_used = sum(_line_cost(line) for line in lines)
    return ContextPack(
        request, policy, tuple(recalled), episodic, data.drafts, data.stale, tokens_used
    )
