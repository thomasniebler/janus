---
name: pr-review-threads
description: List unresolved (or all) review threads on a GitHub PR using the GitHub GraphQL API via gh CLI. Use this before working through PR review comments.
---

# PR Review Threads

Use this skill to inspect review threads on a pull request — especially to find all unresolved comments before addressing them.

## Goal

Fetch and display PR review threads (grouped by comment chain) with author, file location, timestamp, and full body text. Filters to unresolved threads by default.

## CLI usage

```bash
# Unresolved threads only (default)
python3 .agents/skills/pr-review-threads/pr_review_threads.py <PR_NUMBER>

# All threads including resolved
python3 .agents/skills/pr-review-threads/pr_review_threads.py <PR_NUMBER> --all

# Explicit repo (if gh remote detection fails)
python3 .agents/skills/pr-review-threads/pr_review_threads.py <PR_NUMBER> --repo owner/repo
```

**Requires:** `gh` CLI authenticated (`gh auth status`).

## Output format

Each thread block shows:
- Resolution status (✅ resolved / ❌ unresolved), outdated flag, and file:line location
- Each comment: `@author [date] <url>` followed by the full body text (wrapped at 70 chars)
- Reply chains are indented with `↳`

## Workflow

1. Run the script to get unresolved threads.
2. For each thread: read the full comment body including suggestions.
3. Address each comment in code, then note which commit fixed it.
4. Re-run with `--all` to confirm nothing was missed.

## Example triggers

- "Show me the unresolved review comments on PR #226"
- "What do I need to fix before merging PR #42?"
- "List all review threads on this PR"
