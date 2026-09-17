"""GitHub refuses a whole workflow file, silently for the schedule, when a job-level field uses
a context only steps have. The hourly worker did exactly that after WP00 merged (17 Sept 2026,
21:01 UTC): `FACTORY_DIAGNOSTICS_DIR: ${{ runner.temp }}/...` in `jobs.dispatch.env` made GitHub
report "Unrecognized named-value: 'runner'" on every push and start no scheduled run at all.
YAML parsers accept it; only GitHub's expression validator refuses it, and it refuses at load
time, so no hosted check on the PR ran the file it was changing. This detector reads every
workflow the way GitHub does for that one rule, without a YAML library."""
from __future__ import annotations

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}")
# Contexts GitHub does not provide to job-level fields (docs: "Context availability").
STEP_ONLY = re.compile(r"(?<![A-Za-z0-9_.-])(runner|steps|job|env)\.")
# Job-level fields that may not see step-only contexts. `outputs` may reference `steps`, and
# `steps` itself is where those contexts live, so both are excluded on purpose.
JOB_FIELDS = {"env", "if", "runs-on", "timeout-minutes", "services", "container", "concurrency",
              "defaults", "permissions", "continue-on-error", "needs", "strategy"}


def job_level_expressions(text: str) -> list[tuple[int, str, str]]:
    """(line number, job field, expression) for every expression in a job-level field."""
    found = []
    in_jobs = False
    job, field = None, None
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            in_jobs = stripped == "jobs:"
            job, field = None, None
            continue
        if not in_jobs:
            continue
        if indent == 2 and stripped.endswith(":"):
            job, field = stripped[:-1], None
            continue
        if indent == 4 and job is not None:
            key = stripped.split(":", 1)[0]
            field = key if stripped.startswith(key + ":") else field
        if job is None or field not in JOB_FIELDS:
            continue
        for match in EXPRESSION.finditer(line):
            found.append((number, f"{job}.{field}", match.group(1).strip()))
    return found


class WorkflowContextTests(unittest.TestCase):
    def test_no_job_level_field_uses_a_step_only_context(self):
        offenders = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            for number, field, expression in job_level_expressions(path.read_text(encoding="utf-8")):
                if STEP_ONLY.search(expression):
                    offenders.append(f"{path.name}:{number} {field}: ${{{{ {expression} }}}}")
        self.assertEqual(offenders, [], "GitHub refuses the whole file for these; see the module docstring")

    def test_the_detector_sees_the_exact_shape_github_refused(self):
        text = "name: x\non:\n  schedule:\n    - cron: '1 * * * *'\njobs:\n  dispatch:\n    runs-on: ubuntu-24.04\n" \
               "    env:\n      A: plain\n      FACTORY_DIAGNOSTICS_DIR: ${{ runner.temp }}/dark-factory/diagnostics\n" \
               "    steps:\n      - run: echo ${{ runner.temp }}\n        env:\n          B: ${{ steps.x.outputs.y }}\n"
        rows = job_level_expressions(text)
        self.assertEqual(rows, [(10, "dispatch.env", "runner.temp")])
        self.assertTrue(STEP_ONLY.search(rows[0][2]))
        allowed = "jobs:\n  a:\n    env:\n      X: ${{ github.run_id }}-${{ inputs.pr }}-${{ needs.plan.outputs.ready }}\n" \
                  "    outputs:\n      o: ${{ steps.s.outputs.v }}\n"
        self.assertEqual([row for row in job_level_expressions(allowed) if STEP_ONLY.search(row[2])], [])

    def test_the_worker_names_its_diagnostics_directory_from_a_step(self):
        text = (WORKFLOWS / "dark-factory-worker.yml").read_text(encoding="utf-8")
        self.assertIn('echo "FACTORY_DIAGNOSTICS_DIR=$RUNNER_TEMP/dark-factory/diagnostics" >> "$GITHUB_ENV"', text)
        first_step = text.index("- name: Name the early diagnostics directory")
        mint = text.index("- name: Mint the autonomous identity's installation token")
        self.assertLess(first_step, mint, "the directory exists before the first probe can refuse")


if __name__ == "__main__":
    unittest.main()
