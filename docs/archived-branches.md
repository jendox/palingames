# Archived Git branches

These branches are kept for reference and possible cherry-picks. They are **not** merge targets for `main`.

| Branch | Tag snapshot | Notes |
|--------|----------------|-------|
| `archive/collections` | `archive/collections-2026-10-17` | Former `feature/collections`. Not planned for merge into `main`. |

## For contributors and automation

- Base all new work on `main`.
- Do not open PRs from archived branches unless the owner explicitly requests it.
- Do not rebase archived branches onto `main`.
- To reuse code, cherry-pick specific commits or re-implement against current `main`.

The remote branch `feature/collections` was removed in favor of `archive/collections`.
