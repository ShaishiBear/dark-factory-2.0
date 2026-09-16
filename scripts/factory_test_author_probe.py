"""Measure the actual test-author route without reading product scope or running a build."""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path.cwd().resolve()
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from factory_models import model_for_role  # noqa: E402
from factory_thinking_cap_probe import CAPS, honoured, run_one, within_cap  # noqa: E402

WORKFLOW = "dark-factory-test-author-probe.yml"


def diagnostic(policy, *, source, runner=subprocess.run):
    model = model_for_role("test_author", policy)
    if not source.get("ANTHROPIC_AUTH_TOKEN"):
        raise ValueError("explicit API credential required")
    previous = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="factory-route-diagnostic-") as directory:
        # Fixed synthetic prompt, no tools or repository content, empty HOME/cwd, no GitHub
        # credentials, no inherited Max login/configuration and no automatic retries.
        environment = {"PATH": source["PATH"], "HOME": directory,
                       "ANTHROPIC_BASE_URL": "https://openrouter.ai/api",
                       "ANTHROPIC_AUTH_TOKEN": source["ANTHROPIC_AUTH_TOKEN"],
                       "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
        try:
            os.chdir(directory)
            results = [run_one("claude", model, cap, runner=runner, timeout=180, source=environment)
                       for cap in CAPS]
        finally:
            os.chdir(previous)
    return {"schema": "dark-factory/test-author-route-diagnostic", "schema_version": "1.0",
            "role": "test_author", "model": model,
            "measurements": [asdict(row) for row in results],
            "cap1024_exercised": results[0].returned and not within_cap(results[0].thinking, 1024),
            "cap1024_honoured": honoured(results[0], results[1]),
            "cap0_honoured": honoured(results[0], results[2]),
            "calls": 3, "max_budget_usd_per_call": 1, "actual_cost_usd": None,
            "qualifies_work": False, "changes_policy": False}


def verify_dispatch(source):
    owner = source.get("GITHUB_REPOSITORY_OWNER")
    repository = source.get("GITHUB_REPOSITORY")
    if (repository != "ShaishiBear/dark-factory-2.0" or owner != "ShaishiBear"
            or source.get("GITHUB_ACTOR") != owner or source.get("GITHUB_TRIGGERING_ACTOR") != owner
            or source.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or source.get("GITHUB_REF") != "refs/heads/main" or source.get("GITHUB_RUN_ATTEMPT") != "1"
            or source.get("GITHUB_WORKFLOW_REF") != f"{repository}/.github/workflows/{WORKFLOW}@refs/heads/main"):
        raise ValueError("first-attempt owner dispatch of protected diagnostic workflow required")


def main():
    verify_dispatch(os.environ)
    head = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    if head != os.environ["GITHUB_SHA"]:
        raise ValueError("diagnostic checkout differs from protected workflow revision")
    record = diagnostic(ROOT / ".factory/kernel.json", source=os.environ)
    record.update(source_sha=head, run_id=os.environ["GITHUB_RUN_ID"], run_attempt=1)
    path = Path(os.environ["RUNNER_TEMP"]) / "test-author-route-diagnostic.json"
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
