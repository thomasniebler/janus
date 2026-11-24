---
name: update-pr
description: Update an existing GitHub pull request by number: inspect comments, update the branch, implement review fixes, validate, push, and refresh the PR summary.
---

# Update PR

Update GitHub pull request #${input:prNumber:GitHub pull request number}.

Follow this workflow:

1. Read the pull request details, review threads, and review comments before making changes.
2. Check the current local branch state and determine whether the PR branch should be updated from `main` or have its base branch changed.
3. Bring the PR branch up to date with the correct base branch.
4. Work through the existing review comments carefully and implement the needed fixes.
5. Prefer multiple focused commits over one large commit when the review feedback spans separate concerns.
6. Run the relevant validation for the changed code paths.
7. Push the branch updates.
8. Add a self-review update summarizing which comments were addressed, which tradeoffs were made, and anything intentionally left out of scope.
9. Refresh the PR description so it matches the current branch content and relationship to its base branch.

Repository-specific requirements:

- Do not revert unrelated local changes.
- Use the repository's existing commands for linting and tests.
- For Janus, follow the spec-first (updating TECH_SPEC.md) and regression-test expectations in AGENTS.md.
