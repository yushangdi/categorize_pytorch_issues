---
name: debug-export-issue
description: "Debug a PyTorch export issue. Usage: /debug-export-issue <issue_context_path> <output_dir>"
user_invocable: true
---

# Debug Export Issue

You are debugging a PyTorch export issue from GitHub. Follow these steps precisely.

**Arguments**: `$0` is the path to the issue context file, `$1` is the output directory.

## Step 1: Read the issue context

Read the file at `$0`. This contains the issue title, body, and comments.

## Step 2: Classify the issue

Classify into exactly one of these categories:

1. **question** — The issue is not a bug report; it's asking a question or requesting guidance.
2. **feature_request** — The issue is proposing a new feature or enhancement, not reporting a bug.
3. **no_repro** — It's a bug report but there is no reproducible code snippet in the issue or comments.
4. **not_reproducible** — There is repro code but it passes (no error) on the current checkout.
5. **confirmed_bug** — There is repro code and it fails (bug confirmed).

## Step 3: If repro code exists, run it

- Extract the repro code from the issue body or comments. Pick the most minimal/complete snippet.
- Write it to `$1/repro.py`.
- Run it:
  ```bash
  conda run -n pytorch-3.12 --no-capture-output python $1/repro.py
  ```
- Capture stdout and stderr. Use a timeout of 120 seconds.
- If it errors out, the bug is confirmed. If it passes, the bug is not reproducible.

## Step 4: If confirmed bug, analyze and propose a fix

- Analyze the traceback and root cause.
- Search the codebase for the relevant code paths.
- Write a description of the proposed fix.
- If you can write a concrete fix, generate a patch file at `$1/fix.patch` using `git diff`.

## Step 5: If category is "question", write an answer

Provide a helpful answer based on your knowledge of PyTorch export.

## Step 6: Write result.json

Get the PyTorch commit hash by running:
```bash
conda run -n pytorch-3.12 --no-capture-output python -c "import torch; print(torch.version.git_version)"
```

Write the following JSON to `$1/result.json`:

```json
{
  "category": "question|feature_request|no_repro|not_reproducible|confirmed_bug",
  "summary": "one-line summary of finding",
  "answer": "answer if category=question, else null",
  "repro_code": "extracted repro code if any, else null",
  "repro_output": "stdout/stderr from running repro (truncated to 5000 chars), else null",
  "commit_hash": "torch.version.git_version output",
  "fix_description": "proposed fix description if confirmed_bug, else null",
  "patch_file": "fix.patch if patch was generated, else null"
}
```

**Important**: The result.json must be valid JSON. Escape special characters in string values properly.
