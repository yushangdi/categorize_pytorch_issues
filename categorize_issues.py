#!/usr/bin/env python3
"""
Script to fetch recent PyTorch GitHub issues and categorize them as user errors.

A user error is an issue where no PyTorch code change is needed - the user
needs to change their code or their usage of PyTorch APIs to resolve the issue.

Uses the `claude` CLI for categorization.

Skipping Logic:
  - DISABLED tests: Issues with titles starting with "DISABLED" are skipped
    (these are disabled test tracking issues, not user-reported problems)
  - Cached results: Issues already in results.json are skipped to avoid
    re-processing. Their cached categorization is reused.
  - Pull requests: PRs are filtered out (only issues are processed)

Incremental Processing:
  - Results are merged with existing results.json (old results preserved)
  - Comments are only fetched for issues not already in results.json
  - Re-running the script will only process new issues

Usage:
  # Step 1: Fetch issues manually (run this in your terminal, not through Claude Code)
  curl -s "https://api.github.com/repos/pytorch/pytorch/issues?state=all&per_page=50" > issues.json

  # Step 2: Run categorization
  python categorize_issues.py --input issues.json --output results.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass


@dataclass
class Issue:
    number: int
    title: str
    body: str
    state: str
    labels: list[str]
    comments: list[str]
    url: str


def github_api_request(url: str) -> dict | list:
    """Make a request to the GitHub API."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode())


def fetch_issues_from_api(limit: int = 50) -> list[dict]:
    """Fetch recent issues from pytorch/pytorch using GitHub API."""
    issues = []
    per_page = min(limit, 100)
    page = 1

    while len(issues) < limit:
        url = f"https://api.github.com/repos/pytorch/pytorch/issues?state=all&per_page={per_page}&page={page}"
        page_issues = github_api_request(url)
        if not page_issues:
            break
        page_issues = [i for i in page_issues if "pull_request" not in i]
        issues.extend(page_issues)
        page += 1

    return issues[:limit]


def fetch_issues_from_file(filepath: str) -> list[dict]:
    """Load issues from a JSON file."""
    with open(filepath) as f:
        issues = json.load(f)
    # Filter out pull requests (in case using the issues API instead of search API)
    return [i for i in issues if "pull_request" not in i]


def fetch_issue_comments_from_api(issue_number: int) -> list[str]:
    """Fetch comments for a specific issue from GitHub API."""
    url = f"https://api.github.com/repos/pytorch/pytorch/issues/{issue_number}/comments"
    comments = github_api_request(url)
    return [c["body"] for c in comments]


def fetch_comments_from_file(comments_dir: str, issue_number: int) -> list[str]:
    """Load comments from a pre-fetched JSON file."""
    filepath = os.path.join(comments_dir, f"{issue_number}.json")
    if os.path.exists(filepath):
        with open(filepath) as f:
            comments = json.load(f)
        return [c["body"] for c in comments]
    return []


def parse_issues(raw_issues: list[dict], comments_dir: str | None = None, fetch_comments: bool = True) -> list[Issue]:
    """Parse raw issue data into Issue objects with comments."""
    issues = []
    for raw in raw_issues:
        if comments_dir:
            comments = fetch_comments_from_file(comments_dir, raw["number"])
        elif fetch_comments:
            try:
                comments = fetch_issue_comments_from_api(raw["number"])
            except Exception as e:
                print(f"  Warning: Could not fetch comments for #{raw['number']}: {e}", file=sys.stderr)
                comments = []
        else:
            comments = []

        issue = Issue(
            number=raw["number"],
            title=raw["title"],
            body=raw.get("body") or "",
            state=raw["state"],
            labels=[label["name"] for label in raw.get("labels", [])],
            comments=comments,
            url=raw.get("html_url") or f"https://github.com/pytorch/pytorch/issues/{raw['number']}",
        )
        issues.append(issue)
        print(f"Loaded issue #{issue.number}: {issue.title[:50]}...", file=sys.stderr)
    return issues


def categorize_issue_with_claude(issue: Issue) -> dict:
    """Use Claude CLI to categorize whether an issue is a user error."""
    comments_text = "\n\n---\n\n".join(issue.comments) if issue.comments else "(no comments)"

    prompt = f"""Analyze this PyTorch GitHub issue and determine if it's a "user error".

A USER ERROR is an issue where:
- No PyTorch code change is needed to resolve it
- The user needs to change their own code or usage of PyTorch APIs
- Examples: misunderstanding API behavior, incorrect usage, environment/setup issues on user's side, questions about how to use PyTorch

NOT a user error:
- Bugs in PyTorch that require code fixes
- Missing features that should be added
- Documentation bugs in PyTorch
- Performance issues caused by PyTorch internals

ISSUE #{issue.number}: {issue.title}
State: {issue.state}
Labels: {', '.join(issue.labels) if issue.labels else 'none'}

ISSUE BODY:
{issue.body[:4000] if issue.body else '(empty)'}

COMMENTS:
{comments_text[:4000]}

Respond with a JSON object ONLY, no other text:
{{"is_user_error": true/false, "confidence": "high"/"medium"/"low", "reasoning": "Brief explanation"}}"""

    cmd = [
        "claude",
        "--print",
        "--model", "sonnet",
        prompt
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=True
        )
        response_text = result.stdout.strip()

        # Handle potential markdown code blocks
        if "```json" in response_text:
            start = response_text.find("```json") + 7
            end = response_text.find("```", start)
            response_text = response_text[start:end].strip()
        elif "```" in response_text:
            start = response_text.find("```") + 3
            end = response_text.find("```", start)
            response_text = response_text[start:end].strip()

        try:
            result_json = json.loads(response_text)
        except json.JSONDecodeError:
            json_match = re.search(r'\{[^{}]*"is_user_error"[^{}]*\}', response_text)
            if json_match:
                result_json = json.loads(json_match.group())
            else:
                result_json = {
                    "is_user_error": None,
                    "confidence": "low",
                    "reasoning": f"Failed to parse response: {response_text[:200]}"
                }
    except subprocess.TimeoutExpired:
        result_json = {
            "is_user_error": None,
            "confidence": "low",
            "reasoning": "Claude CLI timeout"
        }
    except subprocess.CalledProcessError as e:
        result_json = {
            "is_user_error": None,
            "confidence": "low",
            "reasoning": f"Claude CLI error: {e.stderr[:200] if e.stderr else str(e)}"
        }

    return {
        "issue_number": issue.number,
        "title": issue.title,
        "url": issue.url,
        "state": issue.state,
        "labels": issue.labels,
        **result_json
    }


def main():
    parser = argparse.ArgumentParser(
        description="Categorize PyTorch issues as user errors",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fetch issues and comments manually first:
  curl -s "https://api.github.com/repos/pytorch/pytorch/issues?state=all&per_page=20" > issues.json

  # Optionally fetch comments (creates a directory with comment files):
  mkdir -p comments
  for num in $(jq -r '.[].number' issues.json); do
    curl -s "https://api.github.com/repos/pytorch/pytorch/issues/$num/comments" > comments/$num.json
  done

  # Run categorization:
  python categorize_issues.py --input issues.json --comments-dir comments --output results.json
"""
    )
    parser.add_argument("--input", type=str, help="Input JSON file with issues (from GitHub API)")
    parser.add_argument("--comments-dir", type=str, help="Directory with comment JSON files (named {issue_number}.json)")
    parser.add_argument("--limit", type=int, default=20, help="Number of issues to process (default: 20)")
    parser.add_argument("--output", type=str, default=None, help="Output JSON file (default: stdout)")
    parser.add_argument("--fetch-online", action="store_true", help="Fetch issues from GitHub API (requires network access)")
    args = parser.parse_args()

    if not args.input and not args.fetch_online:
        print("Error: Either --input or --fetch-online is required.", file=sys.stderr)
        print("\nTo fetch issues manually, run:", file=sys.stderr)
        print('  curl -s "https://api.github.com/repos/pytorch/pytorch/issues?state=all&per_page=50" > issues.json', file=sys.stderr)
        print("\nThen run:", file=sys.stderr)
        print("  python categorize_issues.py --input issues.json --output results.json", file=sys.stderr)
        sys.exit(1)

    # Load existing results to avoid re-processing
    cached_results: dict[int, dict] = {}
    if args.output and os.path.exists(args.output):
        try:
            with open(args.output) as f:
                existing = json.load(f)
            for issue_result in existing.get("issues", []):
                cached_results[issue_result["issue_number"]] = issue_result
            print(f"Loaded {len(cached_results)} cached results from {args.output}", file=sys.stderr)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Warning: Could not load cache from {args.output}: {e}", file=sys.stderr)

    if args.input:
        print(f"Loading issues from {args.input}...", file=sys.stderr)
        raw_issues = fetch_issues_from_file(args.input)
    else:
        print(f"Fetching {args.limit} recent issues from pytorch/pytorch...", file=sys.stderr)
        raw_issues = fetch_issues_from_api(args.limit)

    raw_issues = raw_issues[:args.limit]
    print(f"Processing {len(raw_issues)} issues...", file=sys.stderr)

    fetch_comments = args.fetch_online and not args.comments_dir
    issues = parse_issues(raw_issues, args.comments_dir, fetch_comments=fetch_comments)

    print("Categorizing issues with Claude...", file=sys.stderr)

    results = []
    skipped = 0
    skipped_disabled = 0
    for i, issue in enumerate(issues):
        # Skip disabled test issues
        if issue.title.startswith("DISABLED"):
            skipped_disabled += 1
            print(f"[{i+1}/{len(issues)}] Issue #{issue.number} is a disabled test, skipping", file=sys.stderr)
            continue

        # Check cache first
        if issue.number in cached_results:
            results.append(cached_results[issue.number])
            skipped += 1
            print(f"[{i+1}/{len(issues)}] Issue #{issue.number} already cached, skipping", file=sys.stderr)
            continue

        print(f"[{i+1}/{len(issues)}] Analyzing issue #{issue.number}...", file=sys.stderr)
        result = categorize_issue_with_claude(issue)
        results.append(result)
        is_user_error = result.get("is_user_error")
        status = "USER ERROR" if is_user_error else ("NOT user error" if is_user_error is False else "UNCERTAIN")
        print(f"  -> {status} ({result.get('confidence', 'unknown')} confidence)", file=sys.stderr)

    if skipped > 0:
        print(f"\nSkipped {skipped} already-cached issues", file=sys.stderr)
    if skipped_disabled > 0:
        print(f"Skipped {skipped_disabled} disabled test issues", file=sys.stderr)

    # Merge new results into cached results (preserves old issues not in current input)
    for result in results:
        cached_results[result["issue_number"]] = result

    # Convert back to list for output
    all_results = list(cached_results.values())

    user_errors = [r for r in all_results if r.get("is_user_error")]
    non_user_errors = [r for r in all_results if r.get("is_user_error") is False]
    uncertain = [r for r in all_results if r.get("is_user_error") is None]

    output = {
        "summary": {
            "total": len(all_results),
            "user_errors": len(user_errors),
            "non_user_errors": len(non_user_errors),
            "uncertain": len(uncertain),
        },
        "issues": all_results
    }

    if args.output:
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\nResults written to {args.output}", file=sys.stderr)
    else:
        print(json.dumps(output, indent=2))

    print("\n" + "=" * 50, file=sys.stderr)
    print("SUMMARY", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"Issues in this run: {len(issues)} ({len(issues) - skipped - skipped_disabled} new, {skipped} cached, {skipped_disabled} disabled)", file=sys.stderr)
    print(f"Total issues in results: {len(all_results)}", file=sys.stderr)
    print(f"  User errors: {len(user_errors)}", file=sys.stderr)
    print(f"  Non-user errors: {len(non_user_errors)}", file=sys.stderr)
    print(f"  Uncertain: {len(uncertain)}", file=sys.stderr)

    if user_errors:
        print("\n" + "-" * 50, file=sys.stderr)
        print("USER ERRORS:", file=sys.stderr)
        print("-" * 50, file=sys.stderr)
        for r in user_errors:
            print(f"\n#{r['issue_number']}: {r['title'][:70]}", file=sys.stderr)
            print(f"  URL: {r['url']}", file=sys.stderr)
            print(f"  Confidence: {r.get('confidence', 'unknown')}", file=sys.stderr)
            print(f"  Reason: {r.get('reasoning', 'N/A')}", file=sys.stderr)


if __name__ == "__main__":
    main()
