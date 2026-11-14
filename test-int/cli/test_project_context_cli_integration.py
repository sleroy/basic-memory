"""`bm tool facts` and `bm tool project-context` print what the tools return."""

import json

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

REPO = "monolith-breaker"


def write_note(runner: CliRunner, kind: str, title: str, **metadata: object) -> None:
    result = runner.invoke(
        cli_app,
        [
            "tool",
            "write",
            "--title",
            title,
            "--directory",
            f"memory/{kind}s",
            "--note-type",
            kind,
            "--metadata",
            json.dumps({"repo": REPO, "status": "active", **metadata}),
        ],
        input=f"# {title}\n\n- [summary] About {title}.",
    )
    assert result.exit_code == 0, result.output


def test_project_context_prints_the_pack_as_plain_text(
    app, app_config, test_project, config_manager
):
    runner = CliRunner()
    write_note(runner, "lesson", "Slice first", authorities=["AGENTS.md#14"])
    write_note(runner, "decision", "Pending call", status="draft")

    text = runner.invoke(
        cli_app, ["tool", "project-context", "--repo", REPO, "--affected-paths", "src/a"]
    )
    as_json = runner.invoke(
        cli_app, ["tool", "project-context", "--repo", REPO, "--budget-tokens", "800", "--json"]
    )

    assert text.exit_code == 0, text.output
    assert text.stdout.startswith(f"Project context | repo: {REPO} | branch: any")
    assert "- Slice first: About Slice first." in text.stdout
    assert "- AGENTS.md#14 (1 notes)" in text.stdout
    assert "CODE POINTER:" in text.stdout
    assert "Pending call" not in text.stdout
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.stdout)["budget_tokens"] == 800


def test_facts_lists_notes_and_reports_bad_arguments(app, app_config, test_project, config_manager):
    runner = CliRunner()
    write_note(runner, "bug", "Loader drops rows")
    write_note(runner, "bug", "Unreviewed", status="draft")

    listing = runner.invoke(cli_app, ["tool", "facts", "bug", "--repo", REPO, "--status", "draft"])
    as_json = runner.invoke(cli_app, ["tool", "facts", "bug", "--json"])
    bad_kind = runner.invoke(cli_app, ["tool", "facts", "rumor"])
    conflict = runner.invoke(
        cli_app,
        ["tool", "facts", "bug", "--repo", REPO, "--filters", json.dumps({"repo": "x"})],
    )

    assert listing.exit_code == 0, listing.output
    assert "Unreviewed" in listing.stdout and "Loader drops rows" not in listing.stdout
    assert {row["title"] for row in json.loads(as_json.stdout)["results"]} == {
        "Loader drops rows",
        "Unreviewed",
    }
    assert bad_kind.exit_code == 1
    assert "--kind" in bad_kind.output
    assert conflict.exit_code == 1
    assert "`filters` has 'repo', which `repo` already sets." in conflict.output
