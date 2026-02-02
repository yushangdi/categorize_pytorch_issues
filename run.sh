#!/bin/bash
#
# Script to fetch PyTorch GitHub issues and categorize them as user errors.
#
# Usage:
#   ./run.sh                    # Fetch 50 issues with comments, then categorize
#   ./run.sh --limit 20         # Fetch 20 issues
#   ./run.sh --skip-fetch       # Skip fetching, use existing issues.json
#   ./run.sh --no-comments      # Fetch issues without comments (faster)
#

set -e

cd "$(dirname "$0")"

# Default values
LIMIT=50
SKIP_FETCH=false
FETCH_COMMENTS=true

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --limit)
            LIMIT="$2"
            shift 2
            ;;
        --skip-fetch)
            SKIP_FETCH=true
            shift
            ;;
        --no-comments)
            FETCH_COMMENTS=false
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --limit N        Number of issues to fetch (default: 50)"
            echo "  --skip-fetch     Skip fetching, use existing issues.json"
            echo "  --no-comments    Don't fetch comments (faster but less accurate)"
            echo "  -h, --help       Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Step 1: Fetch issues from GitHub API (using Search API to exclude PRs)
if [ "$SKIP_FETCH" = false ]; then
    echo "=== Fetching $LIMIT issues from pytorch/pytorch ==="
    # Use Search API with is:issue to exclude PRs and filter by oncall labels
    # Query: issues with (oncall: export OR oncall: pt2) labels
    QUERY='repo:pytorch/pytorch+is%3Aissue%20(label%3A%22oncall%3A%20export%22%20OR%20label%3A%22oncall%3A%20pt2%22)'
    curl -s "https://api.github.com/search/issues?q=${QUERY}&per_page=$LIMIT&sort=created&order=desc" | jq '.items // []' > issues.json
    echo "Saved issues to issues.json"

    ISSUE_COUNT=$(jq 'length' issues.json)
    echo "Found $ISSUE_COUNT issues with oncall:export OR oncall:pt2 labels"

    # Step 2: Fetch comments for each issue (skip if already in results.json)
    if [ "$FETCH_COMMENTS" = true ]; then
        echo ""
        echo "=== Fetching comments for each issue ==="
        mkdir -p comments

        # Get issue numbers already in results.json
        CACHED_ISSUES=""
        if [ -f results.json ]; then
            CACHED_ISSUES=$(jq -r '.issues[].issue_number' results.json 2>/dev/null | tr '\n' ' ')
        fi

        for num in $(jq -r '.[].number' issues.json); do
            # Skip if already cached
            if echo "$CACHED_ISSUES" | grep -qw "$num"; then
                echo "Issue #$num already in results, skipping comments fetch"
                continue
            fi
            echo "Fetching comments for issue #$num..."
            curl -s "https://api.github.com/repos/pytorch/pytorch/issues/$num/comments" > "comments/$num.json"
        done
        echo "Comments saved to comments/ directory"
    fi
else
    echo "=== Skipping fetch, using existing issues.json ==="
    if [ ! -f issues.json ]; then
        echo "Error: issues.json not found. Run without --skip-fetch first."
        exit 1
    fi
fi

# Step 3: Run categorization with Claude
echo ""
echo "=== Categorizing issues with Claude ==="

COMMENTS_ARG=""
if [ "$FETCH_COMMENTS" = true ] && [ -d comments ]; then
    COMMENTS_ARG="--comments-dir comments"
fi

conda run -n pytorch-3.12 --no-capture-output python categorize_issues.py \
    --input issues.json \
    $COMMENTS_ARG \
    --limit "$LIMIT" \
    --output results.json

echo ""
echo "=== Done! ==="
echo "Results saved to results.json"
echo ""
echo "To view summary:"
echo "  jq '.summary' results.json"
echo ""
echo "To view user errors:"
echo "  jq '.issues[] | select(.is_user_error == true) | {number: .issue_number, title: .title, reasoning: .reasoning}' results.json"
