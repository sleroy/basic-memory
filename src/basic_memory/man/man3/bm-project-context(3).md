---
title: bm-project-context(3)
type: manpage
section: 3
name: bm-project-context
summary: fetch a bounded session-start pack of what a project remembers about one repository
generated: registry
tool: bm_project_context
verified: 1.0.0 mcp
---

# bm-project-context(3)

## NAME

**bm-project-context** — fetch a bounded session-start pack of what a project remembers about one repository

## SYNOPSIS

```
bm_project_context(repo, project, task=None, affected_paths=None,
                   branch=None, budget_tokens=4000, recheck_days=90,
                   output_format="text")
```

## DESCRIPTION

Fetch at session start; complements repository AGENTS.md. Returns a bounded pack
of what the Basic Memory `project` remembers about one code repository, `repo`.
`project` is the Basic Memory project that holds the notes; `repo` is the code
repository they are about (their frontmatter `repo`).

The pack has six parts, always in this order:

1. A header line: repo, branch, generation time, and tokens used of the budget
   (estimated at 4 characters per token).
2. `POLICY POINTERS (binding; lives in the repo — read it there)`: the distinct
   `authorities` the notes cite, with how many notes cite each. Pointers only:
   the rule itself lives in the repository, so read it there.
3. `RECALLED KNOWLEDGE (non-binding evidence; verify before acting; the repository
   outranks memory)`: decisions, then bugs, lessons, best practices, facts and
   glossary terms. Each line gives the title, a one-line summary, `last_verified`
   (or `unverified`), `confidence` and the note's path. A lesson shows its
   `[recommended_action]` and `[verification]` instead of its summary.
4. `EPISODIC`: the titles of the five latest `session` notes for the repo.
5. `CODE POINTER`, only with `affected_paths`: structure of the code (symbols,
   callers, dependencies) comes from LSP or codegraph, not from memory.
6. `HYGIENE`: how many drafts await review and how many active notes were not
   verified within `recheck_days` (a missing `last_verified` counts as stale).

Only `status: active` notes appear, plus `fixed` bugs that match `task` or
`affected_paths`. Drafts, superseded, invalidated and promoted notes are never shown.
`task` ranks lessons and bugs by text match. `affected_paths` ranks first the notes
whose `applies_to` covers a path, by whole path segments. `branch` drops notes whose
`valid_for.branch` names another branch; notes without it hold on every branch.

The pack is filled in that order until `budget_tokens` runs out. A section that was
cut, and every section after it, ends with the exact call that fetches the rest, such
as `… continue with bm_facts(kind="lesson", project="memory", repo="monolith-breaker")`.
The pack reads the 50 most recently edited notes of each kind. The shell
counterpart is `bm tool project-context`, which prints the same plain text.

## PARAMETERS

- **repo** (string, required) — The code repository the notes are about (their `repo:` value).
- **project** (string, required) — Required project name, 'workspace/project', or project external id. Discover projects with bm_project_list(). Paths are relative to this project; a qualified path must agree with it.
- **task** (string | null, optional, default: None) — What the session will do; lessons and bugs matching it come first.
- **affected_paths** (array | null, optional, default: None) — Repository paths the work touches; notes whose `applies_to` covers them rank first.
- **branch** (string | null, optional, default: None) — Current branch; notes limited to another branch (`valid_for.branch`) are left out.
- **budget_tokens** (integer, optional, default: 4000) — Approximate size of the pack in tokens (500-20000), estimated at 4 characters each.
- **recheck_days** (integer, optional, default: 90) — A note not verified within this many days counts as stale.
- **output_format** (optional, default: "text")

## EXAMPLES

```
bm_project_context(repo="monolith-breaker", branch="dev", project="memory")
# → Project context | repo: monolith-breaker | branch: dev | generated: 2026-11-14T12:55:00Z | tokens: 282/4000 (estimate, chars/4)
#
#   POLICY POINTERS (binding; lives in the repo — read it there):
#   - AGENTS.md#14 (1 notes)
#
#   RECALLED KNOWLEDGE (non-binding evidence; verify before acting; the repository outranks memory):
#   Lessons:
#   - Run the slice before pushing: Run `make slice` first. (verify: make slice) [last_verified 2026-01-18; confidence medium] (memory/lessons/Run the slice before pushing.md)
#   ...
#   HYGIENE:
#   - 1 drafts awaiting review (bm_facts(kind="<kind>", status="draft", project="memory", repo="monolith-breaker"))
#   - 2 active notes not verified in 90 days (last_verified absent or before 2026-08-16)
```

## GOTCHAS

- The pack is evidence, not policy. Where a recalled note and the repository
  disagree, the repository is right; verify a note before acting on it.
- A project that has never been indexed returns the index guidance instead of an
  empty pack.
- Notes with no `repo` in their frontmatter are never part of a repo's pack.
- Drafts, stale notes and sessions are counted or listed from search results, so a
  note appears only once it is indexed.

## SEE ALSO

- see_also [[bm-facts(3)]]
- see_also [[bm-search(3)]]
- see_also [[bm-read(3)]] — read a long note in slices with `section` (a heading), `start_line`/`end_line` (a line range) or `max_tokens` (a token budget)
