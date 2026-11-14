"""Pack composition: selection, ranking and the token budget, over plain values."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from basic_memory.mcp.memory_records import MemoryRecord
from basic_memory.mcp.pack_composition import (
    CODE_POINTER_LABEL,
    EMPTY_SECTION,
    HYGIENE_LABEL,
    MAX_BUDGET_TOKENS,
    PACK_ORDER,
    POLICY_LABEL,
    RECALLED_LABEL,
    KindNotes,
    PackData,
    PackRequest,
    compose_pack,
    estimate_tokens,
    item_line,
    paths_overlap,
    policy_lines,
    select_records,
    stale_cutoff,
)

NOW = datetime(2026, 11, 14, 12, 30, 5, tzinfo=timezone.utc)


def record(**overrides: object) -> MemoryRecord:
    base = MemoryRecord(
        kind="fact",
        title="A note",
        file_path="facts/A note.md",
        permalink=None,
        external_id="id-a",
        status="active",
        repo="demo",
        authorities=(),
        applies_to=(),
        branch=None,
        last_verified=None,
        confidence=None,
        summary=None,
        recommended_action=None,
        verification=None,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def request(**overrides: object) -> PackRequest:
    base = PackRequest(
        project="proj",
        repo="demo",
        branch=None,
        affected_paths=(),
        budget_tokens=4000,
        recheck_days=90,
        generated_at=NOW,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def data(
    notes: dict[str, list[MemoryRecord]] | None = None,
    *,
    upstream: tuple[str, ...] = (),
    sessions: tuple[str, ...] = (),
    drafts: int = 0,
    stale: int = 0,
) -> PackData:
    notes = notes or {}
    return PackData(
        kinds=tuple(
            KindNotes(kind, tuple(notes.get(kind, ())), kind in upstream) for kind in PACK_ORDER
        ),
        session_titles=sessions,
        drafts=drafts,
        stale=stale,
    )


# --- Tokens and paths ---


def test_tokens_are_characters_over_four_rounded_up():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2


@pytest.mark.parametrize(
    ("left", "right", "overlap"),
    [
        ("src/loader", "src/loader/batch.py", True),
        ("src/loader/batch.py", "src/loader", True),
        ("src/loader/", "./src/loader/batch.py", True),
        ("src/loader", "src/loader", True),
        ("src/loader", "src/loader2/batch.py", False),
        ("src/billing", "src/loader", False),
    ],
)
def test_paths_overlap_by_whole_segments(left, right, overlap):
    assert paths_overlap(left, right) is overlap


def test_stale_cutoff_is_the_date_recheck_days_ago():
    assert stale_cutoff(NOW, 90) == "2026-08-16"
    assert stale_cutoff(NOW, 1) == "2026-11-13"


# --- Selection ---


def test_only_active_and_fixed_notes_are_selectable():
    notes = [
        record(external_id=status, status=status)
        for status in ("draft", "superseded", "invalidated", "promoted", "active")
    ]
    notes.append(record(external_id="none", status=None))

    kept = select_records(notes, text_order=[], affected_paths=[], branch=None)

    assert [note.external_id for note in kept] == ["active"]


def test_branch_keeps_matching_and_branch_agnostic_notes():
    notes = [
        record(external_id="dev", branch="dev"),
        record(external_id="main", branch="main"),
        record(external_id="any", branch=None),
    ]

    on_dev = select_records(notes, text_order=[], affected_paths=[], branch="dev")
    anywhere = select_records(notes, text_order=[], affected_paths=[], branch=None)

    assert [note.external_id for note in on_dev] == ["dev", "any"]
    assert [note.external_id for note in anywhere] == ["dev", "main", "any"]


def test_fixed_notes_need_a_task_or_path_match():
    fixed = record(external_id="fixed", status="fixed", applies_to=("src/loader",))
    other = record(external_id="other", status="fixed", applies_to=("src/billing",))
    bare = record(external_id="bare", status="fixed")
    notes = [fixed, other, bare]

    assert select_records(notes, text_order=[], affected_paths=[], branch=None) == ()
    assert select_records(
        notes, text_order=[], affected_paths=["src/loader/batch.py"], branch=None
    ) == (fixed,)
    assert select_records(notes, text_order=["bare"], affected_paths=[], branch=None) == (bare,)


def test_text_matches_rank_first_then_path_matches_and_ties_keep_search_order():
    plain_first = record(external_id="plain-1")
    plain_second = record(external_id="plain-2")
    on_path = record(external_id="path", applies_to=("src/loader",))
    text_second = record(external_id="text-2")
    text_first = record(external_id="text-1")
    notes = [plain_first, plain_second, on_path, text_second, text_first]

    kept = select_records(
        notes,
        text_order=["text-1", "text-2"],
        affected_paths=["src/loader"],
        branch=None,
    )

    assert [note.external_id for note in kept] == [
        "text-1",
        "text-2",
        "path",
        "plain-1",
        "plain-2",
    ]


# --- Lines ---


def test_item_line_shows_summary_freshness_confidence_and_path():
    line = item_line(
        record(
            title="Use SQLite",
            summary="The index is SQLite.",
            last_verified="2026-11-01",
            confidence="high",
            file_path="decisions/Use SQLite.md",
        )
    )

    assert line == (
        "- Use SQLite: The index is SQLite. [last_verified 2026-11-01; confidence high] "
        "(decisions/Use SQLite.md)"
    )


def test_item_line_without_summary_or_dates_reads_unverified():
    assert item_line(record(title="Bare")) == (
        "- Bare [unverified; confidence n/a] (facts/A note.md)"
    )


def test_lesson_line_gives_the_action_and_how_to_verify_it():
    lesson = record(
        kind="lesson",
        title="Retry",
        summary="Rate limits recur.",
        recommended_action="Back off.",
        verification="pytest tests/retry",
    )

    assert item_line(lesson).startswith("- Retry: Back off. (verify: pytest tests/retry) [")
    # A lesson without the structured observations falls back to its summary.
    assert item_line(replace(lesson, recommended_action=None, verification=None)).startswith(
        "- Retry: Rate limits recur. ["
    )
    assert item_line(
        replace(lesson, recommended_action=None, summary=None, verification=None)
    ).startswith("- Retry [")


def test_fixed_notes_are_tagged_and_long_summaries_are_cut_to_one_line():
    line = item_line(record(status="fixed", summary="word " * 100))

    assert line.startswith("- [fixed] A note: word word")
    assert "…" in line
    assert "\n" not in line


def test_policy_pointers_are_deduplicated_and_ordered_by_citations():
    kinds = data(
        {
            "decision": [record(authorities=("b.md", "a.md", "a.md"))],
            "lesson": [record(authorities=("b.md",)), record(authorities=("c.md",))],
        }
    ).kinds

    assert policy_lines(kinds) == ("- b.md (2 notes)", "- a.md (1 notes)", "- c.md (1 notes)")
    assert policy_lines(data().kinds) == ()


# --- Composition ---


def test_pack_sections_come_in_the_fixed_order_with_their_labels():
    pack = compose_pack(
        request(branch="dev", affected_paths=("src/loader",)),
        data(
            {"decision": [record(kind="decision", title="D", authorities=("AGENTS.md#1",))]},
            sessions=("Session one",),
            drafts=2,
            stale=3,
        ),
    )

    text = pack.to_text()

    labels = [POLICY_LABEL, RECALLED_LABEL, "EPISODIC:", CODE_POINTER_LABEL, HYGIENE_LABEL]
    assert [text.index(label) for label in labels] == sorted(text.index(label) for label in labels)
    assert text.splitlines()[0] == (
        "Project context | repo: demo | branch: dev | generated: 2026-11-14T12:30:05Z | "
        f"tokens: {pack.tokens_used}/4000 (estimate, chars/4)"
    )
    assert "- AGENTS.md#1 (1 notes)" in text
    assert "Decisions:\n- D [unverified; confidence n/a]" in text
    assert "EPISODIC:\n- Session one" in text
    assert "- 2 drafts awaiting review (bm_facts(" in text
    assert (
        "- 3 active notes not verified in 90 days (last_verified absent or before 2026-08-16)"
        in text
    )
    assert "memory does not store code structure." in text


def test_empty_pack_says_none_and_omits_the_code_pointer():
    text = compose_pack(request(), data()).to_text()

    assert text.count(EMPTY_SECTION) == 3
    assert CODE_POINTER_LABEL not in text
    assert "branch: any" in text


def test_budget_cuts_a_section_and_every_later_one_stops_at_its_continuation():
    lessons = [
        record(kind="lesson", external_id=f"l{n}", title=f"Lesson {n}", summary="x" * 150)
        for n in range(40)
    ]
    pack = compose_pack(
        request(budget_tokens=500),
        data({"lesson": lessons, "fact": [record(title="Late fact")]}),
    )

    text = pack.to_text()

    assert pack.tokens_used <= 500
    shown = [block for block in pack.recalled if block.name == "lesson"][0].lines
    assert 0 < len(shown) < 40
    assert shown == tuple(item_line(lesson) for lesson in lessons[: len(shown)])
    assert text.count('… continue with bm_facts(kind="lesson", project="proj", repo="demo")') == 1
    assert 'Facts:\n… continue with bm_facts(kind="fact", project="proj", repo="demo")' in text
    assert "Late fact" not in text
    # Sections with nothing to show stay out.
    assert "Decisions:" not in text


def test_policy_pointers_cut_by_the_budget_say_how_to_widen_the_pack():
    authorities = tuple(f"docs/rule-{n:03d}.md" for n in range(200))
    pack = compose_pack(
        request(budget_tokens=500),
        data({"decision": [record(kind="decision", authorities=authorities)]}),
    )

    text = pack.to_text()

    assert pack.tokens_used <= 500
    assert (
        '… continue with bm_project_context(project="proj", repo="demo", budget_tokens=1000)'
        in text
    )
    # Later sections are cut off from the budget but still point at their notes.
    assert 'Decisions:\n… continue with bm_facts(kind="decision"' in text


def test_the_widening_call_never_exceeds_the_maximum_budget():
    authorities = tuple(f"docs/rule-{n:04d}.md" for n in range(3000))
    pack = compose_pack(
        request(budget_tokens=MAX_BUDGET_TOKENS),
        data({"decision": [record(kind="decision", authorities=authorities)]}),
    )

    assert f"budget_tokens={MAX_BUDGET_TOKENS})" in pack.to_text()
    assert pack.tokens_used <= MAX_BUDGET_TOKENS


def test_a_kind_with_more_notes_upstream_ends_with_its_continuation_without_blocking_later_kinds():
    pack = compose_pack(
        request(),
        data(
            {"fact": [record(title="Fact one")], "glossary_term": [record(title="Term one")]},
            upstream=("fact",),
        ),
    )

    text = pack.to_text()

    assert (
        'Facts:\n- Fact one [unverified; confidence n/a] (facts/A note.md)\n… continue with bm_facts(kind="fact"'
        in text
    )
    assert "- Term one" in text


def test_a_kind_with_notes_only_upstream_shows_its_heading_and_continuation():
    text = compose_pack(request(), data(upstream=("lesson",))).to_text()

    assert 'Lessons:\n… continue with bm_facts(kind="lesson", project="proj", repo="demo")' in text


def test_the_last_line_of_a_section_needs_no_room_for_a_continuation():
    facts = [record(external_id=f"f{n}", title=f"Fact {n}") for n in range(60)]
    continuation = '… continue with bm_facts(kind="fact", project="proj", repo="demo")'

    def pack_at(budget: int):
        return compose_pack(request(budget_tokens=budget), data({"fact": facts}))

    smallest = next(
        budget for budget in range(100, 5000) if continuation not in pack_at(budget).to_text()
    )

    tight = pack_at(smallest)
    assert tight.to_text().count("- Fact ") == 60
    # Had the last line reserved room for a continuation, the pack would need that many
    # more tokens than it uses.
    assert tight.tokens_used > smallest - estimate_tokens(continuation + "\n")
    assert continuation in pack_at(smallest - 1).to_text()


def test_json_pack_has_the_same_parts_as_the_text():
    pack = compose_pack(
        request(branch="dev", affected_paths=("src",)),
        data(
            {"lesson": [record(kind="lesson", title="L", authorities=("a.md",))]},
            sessions=("S1",),
            drafts=1,
            stale=4,
        ),
    )

    payload = pack.to_json()

    assert payload["repo"] == "demo"
    assert payload["branch"] == "dev"
    assert payload["generated_at"] == "2026-11-14T12:30:05Z"
    assert payload["budget_tokens"] == 4000
    assert payload["tokens_used"] == pack.tokens_used
    assert payload["policy_pointers"] == {"lines": ["- a.md (1 notes)"], "continuation": None}
    assert list(payload["recalled_knowledge"]) == list(PACK_ORDER)
    assert payload["recalled_knowledge"]["lesson"]["lines"] == [
        "- L [unverified; confidence n/a] (facts/A note.md)"
    ]
    assert payload["episodic"]["lines"] == ["- S1"]
    assert payload["code_pointer"].startswith("For symbols, callers and dependencies")
    assert payload["hygiene"] == {
        "drafts": 1,
        "stale": 4,
        "recheck_days": 90,
        "stale_before": "2026-08-16",
    }
    assert compose_pack(request(), data()).to_json()["code_pointer"] is None
