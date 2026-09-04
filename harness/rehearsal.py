#!/usr/bin/env python3
"""Rehearse the validation control plane end to end, without a model, a network or a merge.

Why this exists. Every gate the validator applies has its own tests, and the merge authority is
mutation-tested from several directions. The *sequence* had none. `validate_pr` had never been
executed end to end -- not in production, where the factory is dormant until its trust root
lands, and not in test, where the only fake GitHub served dispatch selection. So the ordering
properties that actually keep the factory safe were unexamined: that the deterministic security
guard runs before any model is invoked, that the emergency stop is re-read immediately before the
irreversible action, and above all that nothing reaches `merge_squash` unless every gate before
it passed.

What this proves, stated exactly. It proves the *orchestration* calls the right things in the
right order and refuses to merge when any of them fails. It does not prove the tools themselves
work: `_exec` is recorded rather than executed, so `factory_security.py`, `factory_evidence.py`
and `merge_verify.py` are represented by their contracts, not their behaviour. Those have their
own suites. Confusing the two would be the same error as trusting a green mutation score computed
on a red baseline.

What is deliberately real. The certificate envelopes are built and verified by the kernel's own
`build_certificate` / `verify_certificate`, and the builder provenance pack is checked by the
real `verify_pack`. A rehearsal whose fakes were free to return anything would prove nothing, so
the fakes are held to the same contracts the real components are: the fake provenance fetch has
to produce a pack that genuinely verifies, and the fake certifiers have to return judgements the
real independence registry accepts.

The happy path must actually reach a merge. Without it every "no merge occurred" assertion would
pass for the wrong reason -- the same trap as a mutation run reporting zero caught out of zero.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kernel.agents import AgentResult  # noqa: E402


def _evidence_rules():
    """Import the real evidence authority's pure rules rather than restating them here.

    The architecture holdout is the one authority `validate_pr` does not itself reject: the
    runtime requires only that it returns a mapping, and the decision to refuse a failing or
    regressing verdict lives inside scripts/factory_evidence.py. If the rehearsal restated that
    rule it would be asserting against its own copy, and the copy would drift. So the seam that
    stands in for factory_evidence.py calls the production function.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "factory_evidence_rules", ROOT / "scripts" / "factory_evidence.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
from factory_kernel.canonical import sha256_value  # noqa: E402
from factory_kernel.provenance import BUILDER_CLAIMS, NOTE_REF  # noqa: E402

CHANGED_FILES: tuple[str, ...] = ("app/backend/main.py",)
HEAD = "1" * 40
BASE = "2" * 40
PR_NUMBER = 77
ISSUE_NUMBER = 42


@dataclass(frozen=True)
class Step:
    """One externally observable action the control plane took."""

    kind: str  # control | github | agent | exec
    name: str
    argv: tuple[str, ...] = ()
    cwd: str = ""


@dataclass
class Trace:
    steps: list[Step] = field(default_factory=list)
    outcome: str = "incomplete"
    error: str = ""
    incident_body: str = ""  # what the humans were actually told, verbatim

    def record(self, kind: str, name: str, *, argv: tuple[str, ...] = (), cwd: str = "") -> None:
        self.steps.append(Step(kind, name, argv, cwd))

    def names(self, kind: str | None = None) -> list[str]:
        return [s.name for s in self.steps if kind is None or s.kind == kind]

    def index(self, name: str) -> int:
        for i, step in enumerate(self.steps):
            if step.name == name:
                return i
        raise AssertionError(f"step {name!r} never happened; trace={self.names()}")

    def happened(self, name: str) -> bool:
        return any(s.name == name for s in self.steps)

    def before(self, first: str, second: str) -> bool:
        return self.index(first) < self.index(second)


def builder_pack(issue: int = ISSUE_NUMBER, head: str = HEAD, base: str = BASE) -> dict:
    """A provenance pack that satisfies the real verify_pack, bindings included.

    The cross-artifact bindings are computed rather than typed, so this stays correct if the
    canonical hashing changes -- and so the rehearsal cannot drift into asserting against a pack
    the real verifier would reject.
    """
    def rec(content: dict, source: str = "note") -> dict:
        return {"content": content, "sha256": sha256_value(content), "source": source}

    contract = rec({"version": "1.0", "issue": {"number": issue},
                    "acceptance_criteria": [{"id": "AC1", "text": "it works"}]})
    tickets = rec({"version": "1.0", "issue": issue, "contract_sha256": contract["sha256"]})
    frontier = rec({"version": "1.0", "issue": issue, "ticket_sha256": tickets["sha256"],
                    "ready": True})
    context = rec({"version": "1.0", "contract_sha256": contract["sha256"]})
    design = rec({"version": "1.0", "contract_sha256": contract["sha256"],
                  "context_sha256": context["sha256"]})
    artifacts = {
        "contract": contract, "tickets": tickets, "frontier": frontier,
        "context": context, "design": design,
    }
    for claim in BUILDER_CLAIMS:
        artifacts.setdefault(claim, rec({"version": "1.0", "claim": claim}))
    return {
        "version": "1.0", "note_ref": NOTE_REF, "issue": issue,
        "base_sha": base, "head_sha": head, "artifacts": artifacts,
    }


class FakeGitHub:
    """Only what validate_pr touches, and it records every call."""

    def __init__(self, trace: Trace, *, state: str = "OPEN", labels: tuple[str, ...] = (),
                 head: str = HEAD, base: str = BASE, body: str | None = None,
                 refuse_issue_creation: bool = False) -> None:
        self.trace = trace
        self.cwd = "."
        self._state = state
        self._labels = labels
        self._head = head
        self._base = base
        self._body = body if body is not None else _pr_body()
        self.refuse_issue_creation = refuse_issue_creation
        self.body = ""

    def pr(self, number: int, *, holdout_safe: bool = False) -> Mapping[str, Any]:
        self.trace.record("github", f"pr(holdout_safe={holdout_safe})")
        return {
            "number": number, "title": "rehearsal", "body": self._body,
            "url": f"https://example.invalid/pr/{number}",
            "headRefName": "rehearsal-head", "headRefOid": self._head,
            "baseRefName": "main", "baseRefOid": self._base,
            "state": self._state, "changedFiles": 1,
            "labels": [{"name": name} for name in self._labels],
        }

    def issue(self, number: int) -> Mapping[str, Any]:
        self.trace.record("github", "issue")
        return {"number": number, "title": "rehearsal issue", "body": "please do the thing"}

    @staticmethod
    def labels(value: Mapping[str, Any]) -> set[str]:
        return {str(item.get("name") or "") for item in (value.get("labels") or [])}

    def merge_squash(self, number: int, *, expected_head: str) -> None:
        self.trace.record("github", "merge_squash")

    def create_issue(self, *, title: str, body_file: Path, labels: tuple[str, ...] = ()) -> int:
        if not body_file.is_file():
            raise AssertionError("incident body file was not written before issue creation")
        self.body = body_file.read_text(encoding="utf-8")
        if self.refuse_issue_creation:
            raise RuntimeError("rehearsed GitHub failure while opening the incident")
        for label in labels:
            self.trace.record("github", f"create_issue:{label}")
        if not labels:
            self.trace.record("github", "create_issue")
        return 9001

    def add_pr_label(self, number: int, label: str) -> None:
        self.trace.record("github", f"add_pr_label:{label}")

    def remove_pr_label(self, number: int, label: str) -> None:
        self.trace.record("github", f"remove_pr_label:{label}")

    def add_issue_label(self, number: int, label: str) -> None:
        self.trace.record("github", f"add_issue_label:{label}")

    def remove_issue_label(self, number: int, label: str) -> None:
        self.trace.record("github", f"remove_issue_label:{label}")

    def comment_pr(self, number: int, body: str) -> None:
        self.trace.record("github", "comment_pr")

    def comment_issue(self, number: int, body: str) -> None:
        self.trace.record("github", "comment_issue")


class FakeProvider:
    """Returns the minimum each authority contract demands, and records the role."""

    def __init__(self, trace: Trace, *, reject: str | None = None) -> None:
        self.trace = trace
        self.reject = reject

    def run(self, request: Any) -> AgentResult:
        role = request.role
        self.trace.record("agent", role)
        # An authority that can see the repository is not blinded, whatever it concludes.
        if Path(request.cwd).resolve() == ROOT:
            raise AssertionError(f"authority {role!r} was run inside the repository checkout")
        if request.environment:
            raise AssertionError(f"authority {role!r} was given an environment: {request.environment}")
        verdict = "fail" if self.reject == role else "pass"
        if role == "holdout":
            value: dict[str, Any] = {"version": "1.0", "findings": [], "verdict": verdict}
        elif role == "architecture-holdout":
            # Computed from the real policy with the real applicability function, so a passing
            # rehearsal is not passing because the fixture happened to say the empty list.
            rules = _evidence_rules()
            policy = json.loads((ROOT / ".factory" / "architecture.json").read_text(encoding="utf-8"))
            value = {
                "version": "1.0", "verdict": verdict, "convergence": "neutral",
                "simplicity": "neutral", "findings": [],
                "principles": rules.applicable(policy.get("principles"), CHANGED_FILES, "scope"),
                "migrations": rules.applicable(
                    policy.get("migrations"), CHANGED_FILES, "paths", active_only=True),
                "debts": rules.applicable(policy.get("debt"), CHANGED_FILES, "paths"),
            }
        else:
            claim = {"contract-certifier": "contract", "design-certifier": "design",
                     "governor-certifier": "architecture-governor"}[role]
            value = {"version": "1.0", "certifies": claim, "verdict": verdict, "findings": []}
        return AgentResult(provider_id="rehearsal", model="rehearsal",
                           content=json.dumps(value), structured_output=value)


def _pr_body() -> str:
    """The attachment format the real _extract_attached parses, not an approximation of it."""
    contract = {"version": "1.0", "issue": {"number": ISSUE_NUMBER}}
    proof = {"version": "1.0", "test_commit": "3" * 40, "green_commit": HEAD,
             "green_results": {"passed": 1}}
    def block(kind: str, value: dict) -> str:
        return (f"<!-- factory-{kind}:start -->\n```factory-{kind}\n"
                + json.dumps(value) + "\n```\n<!-- factory-" + kind + ":end -->\n")
    return f"Fixes #{ISSUE_NUMBER}\n\n" + block("contract", contract) + block("proof", proof)


def exec_recorder(trace: Trace, *, fail: str | None = None) -> Callable[..., str]:
    """Stand in for the deterministic tools, materializing what each is contracted to write."""

    def _exec(argv: list[str], *, cwd: Path, env: Mapping[str, str] | None = None,
              credential_scope: str = "none", timeout: int = 300,
              transcript: Path | None = None) -> str:
        tool = Path(argv[1]).name if len(argv) > 1 else argv[0]
        phase = argv[2] if tool == "merge_verify.py" and len(argv) > 2 else ""
        name = f"{tool}:{phase}" if phase else tool
        trace.record("exec", name, argv=tuple(argv), cwd=str(cwd))
        if fail == name:
            raise RuntimeError(f"{name} failed rc=1: rehearsed failure")
        outputs = {argv[i + 1] for i, a in enumerate(argv) if a == "--output" and i + 1 < len(argv)}
        for out in outputs:
            target = Path(out)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"version": "1.0", "tool": name}), encoding="utf-8")
        if tool == "factory_evidence.py":
            # Stand in for the evidence authority by running its real architecture-holdout rule.
            # Everything else that tool does is out of scope here, but this gate must not be
            # silently absent: it is the only thing that refuses a rejecting architecture holdout.
            idx = argv.index("--architecture-verdict")
            value = json.loads(Path(argv[idx + 1]).read_text(encoding="utf-8"))
            rules = _evidence_rules()
            policy = json.loads(
                (Path(cwd) / ".factory" / "architecture.json").read_text(encoding="utf-8"))
            try:
                rules.verify_architecture_holdout(value, list(CHANGED_FILES), policy)
            except SystemExit as exc:
                raise RuntimeError(f"{name} failed rc=1: architecture holdout refused") from exc
        if tool == "factory_provenance.py":
            idx = argv.index("--output-dir")
            pack_dir = Path(argv[idx + 1])
            pack_dir.mkdir(parents=True, exist_ok=True)
            (pack_dir / "builder-provenance.json").write_text(
                json.dumps(builder_pack()), encoding="utf-8")
        return ""

    return _exec


@dataclass
class Scenario:
    """One rehearsed run. `reject` names an authority that fails; `fail` a tool that fails."""

    name: str
    reject: str | None = None
    fail: str | None = None
    merge: bool = True
    state: str = "OPEN"
    labels: tuple[str, ...] = ("factory:needs-review",)
    head: str = HEAD
    body: str | None = None
    refuse_issue_creation: bool = False


def rehearse(scenario: Scenario) -> Trace:
    """Run the real validate_pr against fakes, and return what it actually did."""
    import dataclasses
    import tempfile
    from unittest import mock

    from factory_kernel.config import load_config
    from factory_kernel.runtime import KernelRuntime

    trace = Trace()
    with tempfile.TemporaryDirectory(prefix="dark-factory-rehearsal-") as tmp:
        home = Path(tmp)
        work_root = home / "work"
        work_root.mkdir()
        worktree_dir = home / "worktree"
        (worktree_dir / ".factory").mkdir(parents=True)
        (worktree_dir / ".factory" / "architecture.json").write_text(
            (ROOT / ".factory" / "architecture.json").read_text(encoding="utf-8"), encoding="utf-8")

        config = load_config(ROOT / ".factory" / "kernel.json")
        config = dataclasses.replace(
            config, runtime=dataclasses.replace(config.runtime, work_root=work_root))

        runtime = KernelRuntime(repo_root=ROOT, config=config)
        runtime.github = FakeGitHub(
            trace, state=scenario.state, labels=scenario.labels,
            head=scenario.head, body=scenario.body,
            refuse_issue_creation=scenario.refuse_issue_creation)
        runtime.provider = FakeProvider(trace, reject=scenario.reject)
        runtime._exec = exec_recorder(trace, fail=scenario.fail)  # type: ignore[method-assign]
        runtime._prepare_worktree = lambda cwd, paths: trace.record("control", "prepare_worktree")  # type: ignore[method-assign]
        runtime.check_stop = lambda: trace.record("control", "check_stop")  # type: ignore[method-assign]

        def fake_git(*args: str, cwd: Path | None = None) -> str:
            if args[:1] == ("diff",) and "--name-only" in args:
                return "".join(f"{path}\n" for path in CHANGED_FILES)
            if args[:1] == ("diff",):
                return "diff --git a/app/backend/main.py b/app/backend/main.py\n"
            return ""

        runtime._git = fake_git  # type: ignore[method-assign]

        worktree = mock.Mock(path=worktree_dir)
        with mock.patch("factory_kernel.runtime.create_detached", return_value=worktree), \
             mock.patch("factory_kernel.runtime.remove") as removed:
            try:
                runtime.validate_pr(PR_NUMBER, merge=scenario.merge)
                trace.outcome = "returned"
            except Exception as exc:  # the control plane's own refusal is the result
                trace.outcome = type(exc).__name__
                trace.error = str(exc)
            if removed.called:
                trace.record("control", "worktree_removed")
        trace.incident_body = runtime.github.body
    return trace


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("happy"),
    Scenario("merge-disabled", merge=False),
    Scenario("pr-not-open", state="CLOSED"),
    Scenario("pr-unlabelled", labels=()),
    Scenario("pr-without-exact-oids", head="not-an-oid"),
    Scenario("holdout-rejects", reject="holdout"),
    Scenario("architecture-holdout-rejects", reject="architecture-holdout"),
    Scenario("contract-certifier-rejects", reject="contract-certifier"),
    Scenario("design-certifier-rejects", reject="design-certifier"),
    Scenario("governor-certifier-rejects", reject="governor-certifier"),
    Scenario("security-guard-fails", fail="factory_security.py"),
    Scenario("provenance-fetch-fails", fail="factory_provenance.py"),
    Scenario("evidence-bundle-fails", fail="factory_evidence.py"),
    Scenario("merge-authorization-fails", fail="merge_verify.py:pre"),
    Scenario("merge-verification-fails", fail="merge_verify.py:post"),
    Scenario("merge-verification-fails-and-github-is-down",
             fail="merge_verify.py:post", refuse_issue_creation=True),
)


def main() -> int:
    merged = 0
    for scenario in SCENARIOS:
        trace = rehearse(scenario)
        did_merge = trace.happened("merge_squash")
        merged += did_merge
        print(f"  {scenario.name:28s} outcome={trace.outcome:14s} "
              f"merged={'yes' if did_merge else 'no ':3s} steps={len(trace.steps)}")
    print(f"REHEARSAL_OK scenarios={len(SCENARIOS)} merged={merged}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
