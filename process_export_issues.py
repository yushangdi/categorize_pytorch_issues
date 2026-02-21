#!/usr/bin/env python3
"""
Fetch PyTorch GitHub issues with a given label, invoke Claude CLI to debug each,
and generate a categorized HTML report (optionally uploaded to manifold).

Usage:
    # Process export issues from the last 7 days
    python process_export_issues.py

    # Process issues from the last 14 days, skip closed ones
    python process_export_issues.py --days 14 --skip-closed

    # Custom label, output directory, and 10-minute timeout per issue
    python process_export_issues.py --label "oncall: export" --output-dir ./my_results --timeout 600

    # Process and upload results to manifold
    python process_export_issues.py --days 7 --upload

    # Re-run safely (already-processed issues are skipped)
    python process_export_issues.py --days 7
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

PYTORCH_DIR = "/data/users/shangdiy/pytorch"
SCRIPT_DIR = Path(__file__).resolve().parent


def issue_dir_name(issue: dict) -> str:
    """e.g. '2026_02_18_issue_175293'"""
    created = issue["createdAt"].replace("Z", "+00:00")
    date_str = datetime.fromisoformat(created).strftime("%Y_%m_%d")
    return f"{date_str}_issue_{issue['number']}"

# ---------------------------------------------------------------------------
# Step 1: Fetch issues
# ---------------------------------------------------------------------------

def fetch_issues(label: str, days: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cmd = [
        "gh", "issue", "list",
        "-R", "pytorch/pytorch",
        "--label", label,
        "--state", "all",
        "--json", "number,title,body,labels,createdAt,state,author",
        "--limit", "100",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    issues = json.loads(result.stdout)

    filtered = []
    for issue in issues:
        created = datetime.fromisoformat(issue["createdAt"].replace("Z", "+00:00"))
        if created >= cutoff:
            filtered.append(issue)

    # Fetch comments for each issue
    for issue in filtered:
        num = issue["number"]
        cmd = [
            "gh", "issue", "view", str(num),
            "-R", "pytorch/pytorch",
            "--json", "comments",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        issue["comments"] = json.loads(result.stdout).get("comments", [])

    return filtered


# ---------------------------------------------------------------------------
# Step 2: Write issue context and invoke Claude
# ---------------------------------------------------------------------------

def write_issue_context(issue: dict, issue_dir: Path) -> Path:
    ctx_path = issue_dir / "issue_context.md"
    lines = [
        f"# Issue #{issue['number']}: {issue['title']}",
        "",
        f"**Created**: {issue['createdAt']}",
        f"**Labels**: {', '.join(l['name'] for l in issue.get('labels', []))}",
        "",
        "## Body",
        "",
        issue.get("body") or "(empty)",
        "",
    ]
    comments = issue.get("comments", [])
    if comments:
        lines.append("## Comments")
        lines.append("")
        for i, c in enumerate(comments, 1):
            author = c.get("author", {}).get("login", "unknown")
            lines.append(f"### Comment {i} by @{author}")
            lines.append("")
            lines.append(c.get("body", ""))
            lines.append("")

    ctx_path.write_text("\n".join(lines))
    return ctx_path


def invoke_claude(issue_dir: Path, ctx_path: Path, timeout: int = 600) -> bool:
    """Invoke Claude CLI with the debug-export-issue skill. Returns True on success."""
    cmd = [
        "claude", "-p",
        f"/debug-export-issue {ctx_path} {issue_dir}/",
        "--add-dir", PYTORCH_DIR,
    ]
    try:
        print(f"  Invoking Claude for {issue_dir.name}...")
        prompt = cmd[2]
        print(f'  claude -p "{prompt}"')
        subprocess.run(cmd, cwd=SCRIPT_DIR, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        print(f"  Claude timed out for {issue_dir.name}")
        return False
    return (issue_dir / "result.json").exists()

# pkill -f process_export_issues.py to kill
# ---------------------------------------------------------------------------
# Step 3: Collect results
# ---------------------------------------------------------------------------

def collect_results(output_dir: Path, issues: list[dict]) -> list[dict]:
    results = []
    for issue in issues:
        num = issue["number"]
        dirname = issue_dir_name(issue)
        issue_dir = output_dir / dirname
        result_path = issue_dir / "result.json"
        author = issue.get("author", {}).get("login", "unknown")
        entry = {"number": num, "title": issue["title"], "dir": dirname, "author": author}
        if result_path.exists():
            try:
                entry["result"] = json.loads(result_path.read_text())
            except json.JSONDecodeError:
                entry["result"] = {"category": "error", "summary": "Invalid result.json"}
        else:
            entry["result"] = {"category": "error", "summary": "No result produced by Claude"}
        results.append(entry)
    return results


# ---------------------------------------------------------------------------
# Step 4: Generate HTML overview
# ---------------------------------------------------------------------------

CATEGORY_COLORS = {
    "question": "#4caf50",
    "feature_request": "#9c27b0",
    "no_repro": "#ff9800",
    "not_reproducible": "#2196f3",
    "confirmed_bug": "#f44336",
    "error": "#9e9e9e",
}


def generate_html(results: list[dict], output_dir: Path) -> Path:
    counts: dict[str, int] = {}
    for r in results:
        cat = r["result"].get("category", "error")
        counts[cat] = counts.get(cat, 0) + 1

    rows = []
    detail_sections = []
    for r in results:
        num = r["number"]
        title = escape(r["title"])
        res = r["result"]
        cat = res.get("category", "error")
        summary = escape(res.get("summary") or "")
        color = CATEGORY_COLORS.get(cat, "#9e9e9e")
        link = f"https://github.com/pytorch/pytorch/issues/{num}"
        closed = res.get("closed", False)
        closed_badge = ' <span style="background:#666;color:#fff;padding:2px 6px;border-radius:3px;font-size:0.8em">closed</span>' if closed else ""
        author = escape(r.get("author", "unknown"))
        author_link = f"https://github.com/{author}"

        rows.append(
            f'<tr>'
            f'<td><a href="{link}">#{num}</a></td>'
            f'<td>{title}{closed_badge}</td>'
            f'<td><a href="{author_link}">@{author}</a></td>'
            f'<td style="background:{color};color:#fff;text-align:center">{cat}</td>'
            f'<td>{summary}</td>'
            f'<td><a href="#detail-{num}">details</a></td>'
            f'</tr>'
        )

        # Build collapsible detail
        detail_parts = [f'<h3 id="detail-{num}">#{num}: {title}{closed_badge}</h3>']
        if res.get("answer"):
            detail_parts.append(f"<h4>Answer</h4><pre>{escape(res['answer'])}</pre>")
        if res.get("repro_code"):
            detail_parts.append(f"<h4>Repro Code</h4><pre>{escape(res['repro_code'])}</pre>")
        if res.get("repro_output"):
            detail_parts.append(f"<h4>Repro Output</h4><pre>{escape(res['repro_output'][:5000])}</pre>")
        if res.get("fix_description"):
            detail_parts.append(f"<h4>Fix Description</h4><pre>{escape(res['fix_description'])}</pre>")
        patch_file = res.get("patch_file")
        if patch_file:
            patch_path = output_dir / r["dir"] / patch_file
            if patch_path.exists():
                detail_parts.append(f"<h4>Patch</h4><pre>{escape(patch_path.read_text()[:10000])}</pre>")
        detail_sections.append("\n".join(detail_parts))

    summary_items = " | ".join(
        f'<span style="color:{CATEGORY_COLORS.get(k, "#999")}">{k}: {v}</span>'
        for k, v in sorted(counts.items())
    )

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>PyTorch Export Issues Report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 2em; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
  th {{ background: #333; color: #fff; }}
  tr:nth-child(even) {{ background: #f9f9f9; }}
  pre {{ background: #f4f4f4; padding: 1em; overflow-x: auto; white-space: pre-wrap; }}
  a {{ color: #1976d2; }}
  .summary {{ font-size: 1.2em; margin: 1em 0; }}
  details {{ margin: 1em 0; }}
</style>
</head>
<body>
<h1>PyTorch Export Issues Report</h1>
<p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
<p class="summary">{summary_items}</p>
<table>
<tr><th>Issue</th><th>Title</th><th>Author</th><th>Category</th><th>Summary</th><th>Details</th></tr>
{"".join(rows)}
</table>
<h2>Details</h2>
{"<hr>".join(detail_sections)}
</body>
</html>"""

    html_path = output_dir / "overview.html"
    html_path.write_text(html)
    return html_path


# ---------------------------------------------------------------------------
# Step 5: Upload to manifold
# ---------------------------------------------------------------------------

def upload_to_manifold(output_dir: Path, results: list[dict]):
    date_str = datetime.now().strftime("%Y_%m_%d")
    base_path = f"tree/export_issues/{date_str}"
    bucket = "tlparse_reports"

    def put(local: Path, remote: str):
        cmd = ["manifold", "put", "--bucket", bucket, "--path", remote, str(local)]
        print(f"  Uploading {local} -> manifold://{bucket}/{remote}")
        subprocess.run(cmd, check=True)

    # Upload overview
    put(output_dir / "overview.html", f"{base_path}/overview.html")

    # Upload per-issue artifacts
    for r in results:
        dirname = r["dir"]
        issue_dir = output_dir / dirname
        result_path = issue_dir / "result.json"
        if result_path.exists():
            put(result_path, f"{base_path}/{dirname}/result.json")
        patch_file = r["result"].get("patch_file")
        if patch_file:
            patch_path = issue_dir / patch_file
            if patch_path.exists():
                put(patch_path, f"{base_path}/{dirname}/{patch_file}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Process PyTorch export issues with Claude")
    parser.add_argument("--days", type=int, default=7, help="Look back N days (default: 7)")
    parser.add_argument("--label", default="oncall: export", help="GitHub label to filter")
    parser.add_argument("--output-dir", default="./results", help="Output directory")
    parser.add_argument("--upload", action="store_true", help="Upload results to manifold")
    parser.add_argument("--timeout", type=int, default=600, help="Claude timeout per issue in seconds")
    parser.add_argument(
        "--skip-closed", action="store_true",
        help="Skip processing closed issues (still marked as closed in results)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching issues with label '{args.label}' from the last {args.days} days...")
    issues = fetch_issues(args.label, args.days)
    print(f"Found {len(issues)} issues.")

    if not issues:
        print("No issues to process.")
        return

    for issue in issues:
        num = issue["number"]
        issue_dir = output_dir / issue_dir_name(issue)
        result_path = issue_dir / "result.json"
        is_closed = issue.get("state", "").upper() == "CLOSED"

        if result_path.exists():
            print(f"\nSkipping issue #{num}: {issue['title']} (result already exists)")
            continue

        if is_closed and args.skip_closed:
            print(f"\nSkipping closed issue #{num}: {issue['title']}")
            issue_dir.mkdir(parents=True, exist_ok=True)
            result = {
                "category": "error",
                "summary": "Skipped (issue is closed)",
                "closed": True,
                "answer": None,
                "repro_code": None,
                "repro_output": None,
                "commit_hash": None,
                "fix_description": None,
                "patch_file": None,
            }
            (issue_dir / "result.json").write_text(json.dumps(result, indent=2))
            continue

        print(f"\nProcessing issue #{num}: {issue['title']}{' (closed)' if is_closed else ''}")
        issue_dir.mkdir(parents=True, exist_ok=True)

        ctx_path = write_issue_context(issue, issue_dir)
        ok = invoke_claude(issue_dir, ctx_path, timeout=args.timeout)
        if ok:
            # If issue is closed, add the closed flag to the result
            if is_closed:
                result = json.loads(result_path.read_text())
                result["closed"] = True
                result_path.write_text(json.dumps(result, indent=2))
            print(f"  Result written for #{num}")
        else:
            print(f"  No result for #{num}")

    print("\nCollecting results...")
    results = collect_results(output_dir, issues)

    print("Generating HTML report...")
    html_path = generate_html(results, output_dir)
    print(f"Report: {html_path}")

    if args.upload:
        print("Uploading to manifold...")
        upload_to_manifold(output_dir, results)
        print("Upload complete.")


if __name__ == "__main__":
    main()
