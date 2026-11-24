#!/usr/bin/env python3
"""pr_review_threads.py — list unresolved (or all) PR review threads via GitHub GraphQL.

Usage:
    python3 pr_review_threads.py <PR_NUMBER> [--all] [--repo owner/repo]

Examples:
    python3 pr_review_threads.py 226
    python3 pr_review_threads.py 226 --all
    python3 pr_review_threads.py 226 --repo myorg/myrepo

Requires: gh CLI authenticated (gh auth status)
"""

import argparse
import json
import subprocess
import sys
import textwrap

QUERY = """
query($owner: String!, $repo: String!, $pr: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      title
      state
      reviewThreads(first: 100) {
        nodes {
          isResolved
          isOutdated
          path
          line
          diffSide
          comments(first: 10) {
            nodes {
              author { login }
              body
              createdAt
              url
            }
          }
        }
      }
    }
  }
}
"""

SEPARATOR = "─" * 72


def run_graphql(owner: str, repo: str, pr: int) -> dict:
    """Call GitHub GraphQL API via gh CLI and return the parsed JSON response."""
    result = subprocess.run(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={QUERY}",
            "-f",
            f"owner={owner}",
            "-f",
            f"repo={repo}",
            "-F",
            f"pr={pr}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Error calling gh API:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout)


def format_thread(thread: dict, index: int) -> str:
    """Render a single review thread as a formatted string block."""
    comments = thread["comments"]["nodes"]
    if not comments:
        return ""

    path = thread["path"] or "(no file)"
    line = thread["line"]
    loc = f"{path}:{line}" if line else path
    resolved = "✅ resolved" if thread["isResolved"] else "❌ unresolved"
    outdated = " (outdated)" if thread["isOutdated"] else ""

    lines = [
        f"[{index}] {resolved}{outdated}  {loc}",
        SEPARATOR,
    ]

    for i, comment in enumerate(comments):
        author = comment["author"]["login"]
        created = comment["createdAt"][:10]
        url = comment["url"]
        body = comment["body"].strip()
        wrapped = textwrap.fill(body, width=70, subsequent_indent="    ")
        prefix = "  ↳ " if i > 0 else "  "
        lines.append(f"{prefix}@{author}  [{created}]  {url}")
        for body_line in wrapped.splitlines():
            lines.append(f"    {body_line}")
        if i < len(comments) - 1:
            lines.append("")

    return "\n".join(lines)


def main() -> None:
    """Parse arguments, fetch PR review threads, and print them."""
    parser = argparse.ArgumentParser(
        description="List PR review threads from GitHub via gh CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("pr", type=int, help="Pull request number")
    parser.add_argument(
        "--all",
        dest="show_all",
        action="store_true",
        help="Show all threads, including resolved ones",
    )
    parser.add_argument(
        "--repo",
        metavar="OWNER/REPO",
        help="Repository in owner/repo format (default: detected from gh remote)",
    )
    args = parser.parse_args()

    if args.repo:
        owner, repo = args.repo.split("/", 1)
    else:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "owner,name"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("Could not detect repo. Use --repo owner/repo", file=sys.stderr)
            sys.exit(1)
        info = json.loads(result.stdout)
        owner = info["owner"]["login"]
        repo = info["name"]

    data = run_graphql(owner, repo, args.pr)
    pr_data = data["data"]["repository"]["pullRequest"]
    title = pr_data["title"]
    state = pr_data["state"]
    threads = pr_data["reviewThreads"]["nodes"]

    if args.show_all:  # noqa: SIM108
        shown = threads
    else:
        shown = [t for t in threads if not t["isResolved"]]

    unresolved_count = sum(1 for t in threads if not t["isResolved"])
    print(f"PR #{args.pr}: {title}  [{state}]")
    print(f"Threads: {unresolved_count} unresolved / {len(threads)} total")
    print()

    if not shown:
        label = "threads" if args.show_all else "unresolved threads"
        print(f"No {label} found.")
        return

    for i, thread in enumerate(shown, start=1):
        formatted = format_thread(thread, i)
        if formatted:
            print(formatted)
            print()


if __name__ == "__main__":
    main()
