# Method: code review, Spec axis

The Spec axis asks one question: does this change do exactly what the validated contract and compiled design say, no less and no more? It does not judge style, structure or taste; that is the Standards axis, judged separately so the two cannot blur into one impression.

Work from the artifacts, not from the implementer's narrative:

- **Every acceptance criterion, in order.** For each AC in the contract, find the code path that satisfies it and the seam the design mapped it to. Missing behaviour, behaviour at the wrong seam, and behaviour that satisfies the letter of a test while defeating its intent are findings.
- **Scope creep is a defect.** Any change not required by an AC, the design's `planned_files`, or a repository convention the change must follow is a finding, even if it is an improvement. The factory ships one issue per PR; improvements go to a new issue.
- **Contract mismatch.** Where the code disagrees with the contract's `given/when/then`, `invariants` or `out_of_scope`, the contract wins. Note the disagreement; do not decide the contract was wrong.
- **Design envelope.** Files changed outside `planned_files`, new files not in `allowed_new_files`, or an AC whose mapped seam was not the one implemented are findings.
- **Dependency declarations.** A package added without a matching entry in the contract's `dependencies` is a finding.

Severity: a missing or wrong AC is `critical` or `high`; scope creep and envelope drift are `high`; an AC satisfied at a different seam than designed is `medium` if behaviour is still correct through the public interface.

Output exactly one JSON object: `{ "version": "1.0", "axis": "spec", "verdict": "pass|fail", "findings": [...] }`. Each finding has `severity` (`critical|high|medium|low`), `file`, `line` (integer or null) and a concise `description` that names the AC or design element it relates to. Any `critical` or `high` finding means verdict `fail`. Do not include Standards observations here.
