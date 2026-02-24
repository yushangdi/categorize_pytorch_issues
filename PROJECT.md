# Categorize PyTorch Issues

Automated pipeline that fetches PyTorch GitHub issues, invokes Claude CLI to debug/categorize each one, and produces a categorized HTML report.

## Directory structure

```
categorize_pytorch_issues/
  process_export_issues.py    # Main orchestrator script
  .claude/skills/debug-export-issue/SKILL.md  # Claude skill for debugging a single issue
  results/                    # Default output directory (created at runtime)
    2026_02_18_issue_175293/
      issue_context.md        # Issue title + body + comments
      result.json             # Claude's structured output
      repro.py                # Extracted repro code (if any)
      fix.patch               # Proposed fix (if confirmed_bug)
    overview.html             # Aggregated HTML report
```

## How it works

1. **Fetch issues** — `gh issue list` with label filter (default: `oncall: export`), filtered to a time window (`--days`). Fetches comments per issue.
2. **Invoke Claude** — For each issue, writes `issue_context.md` then runs `claude -p "/debug-export-issue <context_path> <output_dir>" --add-dir /data/users/shangdiy/pytorch`. Claude runs with `cwd` set to this project directory (so the skill is discovered) and `--add-dir` gives it access to the pytorch repo.
3. **Classify** — Claude categorizes each issue as one of: `question`, `feature_request`, `no_repro`, `not_reproducible`, `confirmed_bug`. For bug reports with repro code, it runs the code via `conda run -n pytorch-3.12`.
4. **Collect results** — Reads `result.json` from each issue directory. Issues where Claude failed/timed out are marked `error`.
5. **Generate HTML** — Self-contained HTML report with summary counts, color-coded table (issue, title, author, category, summary), and collapsible detail sections.
6. **Upload to manifold** (optional) — Pushes report and per-issue artifacts to `tlparse_reports` bucket.

## Usage

```bash
# Default: last 7 days, oncall:export label
python process_export_issues.py

# Last 14 days, skip closed issues
python process_export_issues.py --days 14 --skip-closed

# Custom label and output directory
python process_export_issues.py --label "oncall: export" --output-dir ./my_results

# Upload to manifold after processing
python process_export_issues.py --days 7 --upload

# Re-run is safe — issues with existing result.json are skipped
python process_export_issues.py --days 7
```

### CLI arguments

| Argument | Default | Description |
|---|---|---|
| `--days` | 7 | Look back N days from now |
| `--label` | `oncall: export` | GitHub issue label to filter |
| `--output-dir` | `./results` | Where to write per-issue dirs and HTML report |
| `--upload` | false | Upload results to manifold after processing |
| `--timeout` | 600 | Claude CLI timeout per issue (seconds) |
| `--skip-closed` | false | Don't invoke Claude for closed issues (still recorded in report) |

## Result JSON schema

Each issue produces a `result.json`:

```json
{
  "category": "question|feature_request|no_repro|not_reproducible|confirmed_bug",
  "summary": "one-line summary",
  "closed": true,
  "answer": "answer if question, else null",
  "repro_code": "extracted repro code or null",
  "repro_output": "stdout/stderr from repro run or null",
  "commit_hash": "pytorch git version hash",
  "fix_description": "proposed fix if confirmed_bug, else null",
  "patch_file": "fix.patch if generated, else null"
}
```

## Category colors in HTML report

- **question**: green
- **feature_request**: purple
- **no_repro**: orange
- **not_reproducible**: blue
- **confirmed_bug**: red
- **error**: grey

## Known issues / TODO

- The skill uses positional args (`$0`, `$1`) — Claude Code doesn't support named skill arguments.
- To kill a stuck run: `pkill -f process_export_issues.py` or `pkill -f "claude -p"`.
- Manifold upload assumes `manifold` CLI is available on PATH.

## Dependencies

- `gh` (GitHub CLI, authenticated)
- `claude` (Claude Code CLI)
- `conda` environment `pytorch-3.12` with PyTorch installed from source
- `manifold` CLI (only needed with `--upload`)
