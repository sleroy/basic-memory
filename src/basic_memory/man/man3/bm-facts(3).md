---
title: bm-facts(3)
type: manpage
section: 3
name: bm-facts
summary: list curated knowledge notes of one kind in a project, by repository and status
generated: registry
tool: bm_facts
verified: 1.0.0 mcp
---

# bm-facts(3)

## NAME

**bm-facts** — list curated knowledge notes of one kind in a project, by repository and status

## SYNOPSIS

```
bm_facts(kind, project, repo=None, status=None, filters=None, page=1,
         page_size=20, output_format="text")
```

## DESCRIPTION

Lists the project's knowledge notes of one `kind`: fact, bug, lesson,
best_practice, decision or glossary_term. The kind is the note's frontmatter
`type`. `repo` limits the list to notes about one code repository (frontmatter
`repo`). `status` limits it to one lifecycle status; omit it to list every status,
and pass `"draft"` to list notes awaiting review. `filters` adds frontmatter
conditions.

Each line shows the note's path, title and status, then `last_verified` and
`confidence` when the note has them. The most recently edited note comes first.
When more notes match than fit one page, the output ends with the exact call for
the next page.

`bm_project_context` composes these reads into a session-start pack. For
free-text search use bm_search.

## PARAMETERS

- **kind** (required) — Which kind of knowledge note to list.
- **project** (string, required) — Required project name, 'workspace/project', or project external id. Discover projects with bm_project_list(). Paths are relative to this project; a qualified path must agree with it.
- **repo** (string | null, optional, default: None) — Only notes about this code repository (`repo:`).
- **status** (string | null, optional, default: None) — Only notes with this status: draft, active, fixed, superseded, invalidated or promoted. Omit for all.
- **filters** (object | null, optional, default: None) — Frontmatter filters as {field: condition}. Nested fields use dot notation ("schema.confidence"). A condition is a value for equality ({"status": "draft"}), null for absent-or-null ({"owner": null}), a list for contains-all ({"tags": ["a", "b"]}), or an operator object with $gt, $gte, $lt, $lte, $in, $between, $contains or $exists, e.g. {"priority": {"$in": ["high"]}}. Several operators on one field AND together.
- **page** (integer, optional, default: 1) — Page number.
- **page_size** (integer, optional, default: 20) — Results per page (1-100).
- **output_format** (optional, default: "text")

## EXAMPLES

```
bm_facts(kind="lesson", repo="monolith-breaker", project="memory")
# → 2 lesson notes with repo 'monolith-breaker' on page 1:
#
#   - memory/lessons/Retry on 429.md | Retry on 429 | status active | last_verified 2026-11-14 | confidence high
#   - memory/lessons/Run the slice first.md | Run the slice first | status active

bm_facts(kind="decision", repo="monolith-breaker", status="draft", project="memory")
# → triage view: only decisions still awaiting review
```

## GOTCHAS

- `filters` may not name a field that an argument already sets: `type` is set by
  `kind`, `repo` by `repo` and `status` by `status`. The call is refused rather
  than letting one silently win. `filters={"repo": {"$in": [...]}}` is accepted
  when `repo` is not given.
- The search does not report an exact total, so the output counts the notes on the
  page, not in the project.

## SEE ALSO

- see_also [[bm-project-context(3)]]
- see_also [[bm-search(3)]]
- see_also [[bm-read(3)]] — read a long note in slices with `section` (a heading), `start_line`/`end_line` (a line range) or `max_tokens` (a token budget)
