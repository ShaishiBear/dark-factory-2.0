# Method: diagnosing bugs

Build a tight red feedback loop before you form a theory. A root cause proposed before the failure has been seen going red is a guess with a confident tone, and the factory does not merge guesses.

Order of work:

1. **Reproduce first.** From the issue's report, propose the smallest command that should exhibit the reported symptom against the current code: only one of the repository's test-runner shapes, exactly as the role prompt lists them (`uv run pytest <path> -k <name>`, `bun run test <file>` and their siblings); a script or an ad-hoc check is refused. The kernel executes this command deterministically, with no credentials and no shell, and refuses to continue if it does not fail or does not show the symptom you named.
2. **Name the symptom exactly.** State the string, assertion or error the repro must produce. Vague symptoms ("it breaks") cannot be checked and are refused.
3. **Minimise.** Once a repro exists, cut it down: fewer inputs, fewer steps, one seam. The smaller the red loop, the faster every later step and the sharper the regression test.
4. **Separate facts from hypotheses.** Facts are what the issue reports, what the source visibly does, and what an executed repro printed. Hypotheses are explanations that would need further execution to confirm. Label each explicitly. Never present a hypothesis as an observation.
5. **Rank hypotheses.** List the plausible causes in order of likelihood with the evidence for each. Say which single observation would distinguish the top two.
6. **Identify the regression seam.** Name the public interface where a test will assert the fixed behaviour. That seam becomes an acceptance criterion; if you cannot name one, fail rather than guess.
7. **Only then reason about the fix.** Do not implement. Do not widen scope to nearby oddities; file them as separate issues in your notes.

Your output is `investigation.md` plus exactly one of `repro.json` (an existing command already fails) or `repro-deferred.json` (no existing command can fail yet; the red loop is closed by the independent acceptance tests). If the available evidence cannot support a credible repro, say so and fail the stage: a bug that cannot be made to go red cannot be contracted, and escalating early is cheaper than a confident wrong fix.
