# Bounded test-author route diagnostic

Issue #189 timed out in `test_author` on `minimax/minimax-m3`. The existing thinking-cap
probe measures `provider.model`, currently a different route. That observation cannot
establish whether the actual author route honors a cap.

The manual owner-only protected-main workflow runs the existing synthetic thinking probe
against `model_for_role("test_author")`: exactly three one-turn calls, uncapped,1024 and0.
Each call retains the existing one-dollar CLI budget and180-second timeout; the group is
bounded at600seconds. There are no retries. Actual billing is not retained by the existing
probe and is explicitly unknown, rather than inferred from the budget.

The fixed public prompt asks for a PostgreSQL migration plan. No issue, intent, repository
content or private conversation is sent. Each process has no tools, an empty temporary
HOME/cwd, the fixed OpenRouter API endpoint and a filtered environment without GitHub
credentials or retired Max login. The API credential remains in GitHub Secrets.

The workflow can only read repository content and upload a bounded observation. It cannot
start product work, relabel #189, change policy, write code, mint App tokens or merge. An
observed honored cap is evidence for a separate reviewed proposal, never automatic policy
activation. A failed/ambiguous measurement is retained as unproven. This diagnostic does not
release the issue's human hold.
