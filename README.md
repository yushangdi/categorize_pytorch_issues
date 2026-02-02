# PyTorch Issue Categorizer

Automatically categorizes PyTorch GitHub issues as "user errors" vs issues requiring PyTorch code changes.

A **user error** is an issue where no PyTorch code change is needed - the user needs to change their own code or usage of PyTorch APIs to resolve the issue.

## Quick Start

```bash
# Run the full pipeline (fetch issues + categorize)
./run.sh

# Limit to N issues
./run.sh --limit 20

# Skip fetching (use existing issues.json)
./run.sh --skip-fetch

# Faster mode without fetching comments
./run.sh --no-comments
```

## How It Works

1. **Fetch issues** from PyTorch GitHub using the Search API
   - Filters for issues with `oncall: export` OR `oncall: pt2` labels
   - Excludes pull requests

2. **Fetch comments** for each issue (optional but improves accuracy)

3. **Categorize** each issue using Claude CLI
   - Analyzes issue title, body, labels, and comments
   - Returns: `is_user_error` (true/false), `confidence` (high/medium/low), `reasoning`

## Skipping Logic

The script is optimized for incremental runs:

- **DISABLED tests**: Issues with titles starting with "DISABLED" are skipped (these are disabled test tracking issues)
- **Cached results**: Issues already in `results.json` are skipped; their cached categorization is reused
- **Pull requests**: PRs are filtered out (only issues are processed)
- **Comments**: Only fetched for issues not already in `results.json`

## Viewing Results

```bash
# View summary
jq '.summary' results.json

# View user errors
jq '.issues[] | select(.is_user_error == true) | {number: .issue_number, title: .title, reasoning: .reasoning}' results.json

# View non-user errors
jq '.issues[] | select(.is_user_error == false) | {number: .issue_number, title: .title, reasoning: .reasoning}' results.json

# View uncertain issues
jq '.issues[] | select(.is_user_error == null) | {number: .issue_number, title: .title, reasoning: .reasoning}' results.json

# Count by category
jq '.issues | group_by(.is_user_error) | map({is_user_error: .[0].is_user_error, count: length})' results.json
```

## Files

- `run.sh` - Main script to fetch and categorize issues
- `categorize_issues.py` - Python script that calls Claude CLI for categorization
- `issues.json` - Fetched issues from GitHub API
- `comments/` - Directory with comment JSON files (one per issue)
- `results.json` - Categorization results

## Output Format

```json
{
  "summary": {
    "total": 50,
    "user_errors": 15,
    "non_user_errors": 30,
    "uncertain": 5
  },
  "issues": [
    {
      "issue_number": 12345,
      "title": "Error when using torch.compile",
      "url": "https://github.com/pytorch/pytorch/issues/12345",
      "state": "open",
      "labels": ["oncall: pt2"],
      "is_user_error": true,
      "confidence": "high",
      "reasoning": "User is passing unsupported argument type to torch.compile"
    }
  ]
}
```
