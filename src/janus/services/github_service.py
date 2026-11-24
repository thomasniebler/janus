"""GitHub integration service for Janus PR and issue workflows."""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx

_GITHUB_API_URL = "https://api.github.com"
_GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"


class GitHubService:
    """Thin wrapper around the GitHub REST API."""

    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None) -> None:
        """Initialize the service with a GitHub token (``transport`` is a test seam)."""
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15,
            transport=transport,
        )

    def close(self) -> None:
        """Closes the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> GitHubService:
        """Supports use as a context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Closes the HTTP client on context manager exit."""
        self.close()

    def _request(self, method: str, path: str, *, json_payload: dict[str, Any] | None = None) -> Any:
        """Execute a GitHub API request and normalize failures."""
        url = path if path.startswith("https://") else f"{_GITHUB_API_URL}{path}"
        try:
            response = self._client.request(method, url, json=json_payload)
        except httpx.RequestError as exc:
            raise ValueError(f"Connection error while calling GitHub API: {exc}") from exc

        payload: Any
        try:
            payload = response.json() if response.content else {}
        except ValueError:
            payload = {}

        if response.status_code >= 400:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise ValueError(message or f"GitHub API returned {response.status_code}.")

        return payload

    def test_connection(self) -> tuple[bool, str]:
        """Verifies the token by fetching the authenticated user."""
        try:
            viewer = self._request("GET", "/user")
        except ValueError:
            return False, "Unable to connect to GitHub API. Check your token and network connection."
        username = viewer.get("login") or "unknown"
        return True, f"Connected as {username}"

    def create_issue(
        self,
        *,
        repo: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a GitHub issue in the given repository."""
        payload = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        issue = self._request("POST", f"/repos/{repo}/issues", json_payload=payload)
        return {
            "number": issue.get("number"),
            "title": issue.get("title"),
            "url": issue.get("html_url"),
            "repo": repo,
        }

    def list_pull_requests(self, *, repo: str, state: str = "open", limit: int = 20) -> list[dict[str, Any]]:
        """List pull requests for a repository."""
        pulls = self._request("GET", f"/repos/{repo}/pulls?state={state}&sort=updated&direction=desc&per_page={limit}")
        return [
            {
                "number": pr.get("number"),
                "title": pr.get("title"),
                "url": pr.get("html_url"),
                "state": pr.get("state"),
                "draft": pr.get("draft"),
                "head_ref": (pr.get("head") or {}).get("ref"),
                "base_ref": (pr.get("base") or {}).get("ref"),
                "updated_at": pr.get("updated_at"),
            }
            for pr in pulls
        ]

    def merge_pull_request(
        self,
        *,
        repo: str,
        pull_number: int,
        merge_method: str = "squash",
        sha: str | None = None,
    ) -> dict[str, Any]:
        """Merge a pull request; with ``sha``, GitHub refuses if the head moved since it was checked."""
        payload: dict[str, Any] = {"merge_method": merge_method}
        if sha:
            payload["sha"] = sha
        result = self._request("PUT", f"/repos/{repo}/pulls/{pull_number}/merge", json_payload=payload)
        return {
            "merged": bool(result.get("merged")),
            "message": result.get("message"),
            "sha": result.get("sha"),
            "pull_number": pull_number,
            "repo": repo,
        }

    # --- Read helpers for the self-evolution merge gates (JAN-322) ---------------

    def _paginate(self, path: str, *, key: str | None = None, max_pages: int = 30) -> list[Any]:
        """Return every item of a paginated list endpoint (100 per page, at most ``max_pages`` pages)."""
        separator = "&" if "?" in path else "?"
        items: list[Any] = []
        for page in range(1, max_pages + 1):
            payload = self._request("GET", f"{path}{separator}per_page=100&page={page}")
            batch = payload.get(key) if key and isinstance(payload, dict) else payload
            if not isinstance(batch, list):
                raise ValueError(f"GitHub API returned an unexpected payload for {path}.")
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise ValueError(f"GitHub API listing {path} has more than {max_pages * 100} items.")

    def get_repository(self, repo: str) -> dict[str, Any]:
        """Return a repository's raw metadata (``default_branch`` ...)."""
        return self._request("GET", f"/repos/{repo}")

    def get_pull_request(self, repo: str, pull_number: int) -> dict[str, Any]:
        """Return a pull request's raw payload (state, mergeable, head/base, merge commit)."""
        return self._request("GET", f"/repos/{repo}/pulls/{pull_number}")

    def list_pull_request_files(self, repo: str, pull_number: int) -> list[dict[str, Any]]:
        """Return every changed file of a pull request (GitHub lists at most 3000)."""
        return self._paginate(f"/repos/{repo}/pulls/{pull_number}/files")

    def list_check_runs(self, repo: str, sha: str) -> list[dict[str, Any]]:
        """Return every check run reported for a commit."""
        return self._paginate(f"/repos/{repo}/commits/{sha}/check-runs", key="check_runs")

    def list_commit_statuses(self, repo: str, sha: str) -> list[dict[str, Any]]:
        """Return the latest commit status per context (the combined-status view)."""
        return self._paginate(f"/repos/{repo}/commits/{sha}/status", key="statuses")

    def get_commit(self, repo: str, sha: str) -> dict[str, Any]:
        """Return one commit (``author.login``, ``commit.message``)."""
        return self._request("GET", f"/repos/{repo}/commits/{sha}")

    def compare_commits(self, repo: str, base: str, head: str) -> dict[str, Any]:
        """Compare two commits; ``status`` is ``ahead``/``identical`` when ``head`` contains ``base``."""
        return self._request("GET", f"/repos/{repo}/compare/{base}...{head}")

    def revert_pull_request(self, pull_node_id: str, *, title: str, body: str) -> dict[str, Any]:
        """Open a pull request that reverts a merged one (GraphQL ``revertPullRequest``)."""
        query = (
            "mutation($input: RevertPullRequestInput!) { revertPullRequest(input: $input) "
            "{ revertPullRequest { number url } } }"
        )
        payload = self._request(
            "POST",
            _GITHUB_GRAPHQL_URL,
            json_payload={
                "query": query,
                "variables": {"input": {"pullRequestId": pull_node_id, "title": title, "body": body}},
            },
        )
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if errors:
            messages = "; ".join(str(error.get("message")) for error in errors if isinstance(error, dict))
            raise ValueError(f"GitHub could not open the revert pull request: {messages or 'unknown error'}")
        reverted = ((payload.get("data") or {}).get("revertPullRequest") or {}).get("revertPullRequest") or {}
        if not reverted.get("number"):
            raise ValueError("GitHub did not return the revert pull request.")
        return {"number": reverted["number"], "url": reverted.get("url")}
