"""Only the kernel heartbeats the issue lease, and only it holds GitHub credentials on the build side.

`scripts/factory_protocol.py` and `scripts/factory_proof.py` used to start and touch the lease
themselves, which needed `gh` and therefore GH_TOKEN, while the kernel deliberately runs them with
no credentials because `factory_proof.py` executes model-authored checkpoint commands. The third
canary dispatch (run 33880438107) died at `LEASE_ERROR ... set the GH_TOKEN`. The heartbeat now
belongs to `KernelRuntime._lease_heartbeat`, which runs `scripts/factory_lease.py` with GitHub
scope; protocol and proof carry no lease code and run with scope `none`; and the checkpoint runner
scrubs both token names from its child environment as defence in depth.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUNTIME = ROOT / "factory_kernel" / "runtime.py"
PROTOCOL = ROOT / "scripts" / "factory_protocol.py"
PROOF = ROOT / "scripts" / "factory_proof.py"

EXPECTED_HEARTBEATS = [
    ("start", "contract"),
    ("touch", "design-context"),
    ("touch", "red"),
    ("touch", "green"),
    ("touch", "final-green"),
    ("finish", "pr-handoff"),
]


def load_proof():
    spec = importlib.util.spec_from_file_location("factory_proof_under_test", PROOF)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _function(tree: ast.Module, class_name: str, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return item
    raise AssertionError(f"{class_name}.{name} not found")


def _method_calls(func: ast.FunctionDef, method: str) -> list[ast.Call]:
    return [
        node for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == method
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
    ]


def _kw(call: ast.Call, name: str):
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _str(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


class KernelHeartbeatTests(unittest.TestCase):
    def runtime(self):
        from factory_kernel.config import load_config
        from factory_kernel.runtime import KernelRuntime

        raw = (ROOT / ".factory" / "kernel.json").read_text(encoding="utf-8")
        import json
        import tempfile

        cfg = json.loads(raw)
        tmp = tempfile.mkdtemp()
        cfg["runtime"]["work_root"] = tmp
        path = Path(tmp) / "kernel.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return KernelRuntime(repo_root=ROOT, config=load_config(path))

    def test_heartbeat_runs_factory_lease_with_github_scope(self):
        rt = self.runtime()
        paths = mock.Mock(artifacts=Path("/tmp/art"), transcripts=Path("/tmp/tr"))
        with mock.patch.object(rt, "_exec", return_value="") as run:
            rt._lease_heartbeat("start", 49, "contract", paths, cwd=Path("/wt"))
            rt._lease_heartbeat("finish", 49, "pr-handoff", paths, cwd=Path("/wt"), pr=77)
            rt._lease_heartbeat("start", 49, "contract", paths, cwd=Path("/wt"), pr=77)
        first, second, third = (call.args[0] for call in run.call_args_list)
        self.assertEqual(first[:3], ["python", "scripts/factory_lease.py", "start"])
        self.assertEqual(first[first.index("--lease-file") + 1], str(Path("/tmp/art") / "factory-lease.json"))
        self.assertNotIn("--pr", first)
        self.assertEqual(second[2], "finish")
        self.assertEqual(second[second.index("--pr") + 1], "77")
        self.assertNotIn("--pr", third, "start never carries a PR")
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["credential_scope"], "github")

    def test_exec_scopes_are_real(self):
        """scope=github forwards the token; scope=none strips it. The real subprocess runs."""
        rt = self.runtime()
        probe = ["python", "-c", "import os,sys; sys.stdout.write(str('GH_TOKEN' in os.environ))"]
        with mock.patch.dict(os.environ, {"GH_TOKEN": "t-1", "GITHUB_TOKEN": "t-2"}):
            self.assertEqual(rt._exec(probe, cwd=ROOT, credential_scope="github").strip(), "True")
            self.assertEqual(rt._exec(probe, cwd=ROOT, credential_scope="none").strip(), "False")


class BuilderCallSiteTests(unittest.TestCase):
    """The reviewer's structural test: the real build path, read from the AST."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tree = ast.parse(RUNTIME.read_text(encoding="utf-8"))
        cls.build = _function(cls.tree, "KernelRuntime", "build_issue")
        cls.repair = _function(cls.tree, "KernelRuntime", "_review_and_repair")

    def _protocol_and_proof_execs(self, func):
        found = []
        for call in _method_calls(func, "_exec"):
            argv = call.args[0] if call.args else _kw(call, "argv")
            if not isinstance(argv, ast.List) or len(argv.elts) < 3:
                continue
            program, command = _str(argv.elts[1]), _str(argv.elts[2])
            if program in {"scripts/factory_protocol.py", "scripts/factory_proof.py"}:
                found.append((program, command, _str(_kw(call, "credential_scope"))))
        return found

    def test_protocol_and_proof_run_without_github_credentials(self):
        calls = self._protocol_and_proof_execs(self.build) + self._protocol_and_proof_execs(self.repair)
        commands = sorted((p, c) for p, c, _ in calls)
        self.assertEqual(commands, sorted([
            ("scripts/factory_protocol.py", "contract"),
            ("scripts/factory_protocol.py", "context"),
            ("scripts/factory_protocol.py", "attach"),
            ("scripts/factory_proof.py", "red"),
            ("scripts/factory_proof.py", "green"),
            ("scripts/factory_proof.py", "green"),
            ("scripts/factory_proof.py", "green"),
            ("scripts/factory_proof.py", "attach"),
        ]))
        for program, command, scope in calls:
            with self.subTest(program=program, command=command):
                if command == "attach":
                    # attach edits the PR through gh and runs nothing model-authored.
                    self.assertEqual(scope, "github")
                else:
                    self.assertEqual(scope, "none", f"{program} {command} must not hold GitHub credentials")

    def test_kernel_heartbeats_every_stage_in_order(self):
        calls = _method_calls(self.build, "_lease_heartbeat")
        sequence = [(_str(c.args[0]), _str(c.args[2])) for c in calls]
        self.assertEqual(sequence, EXPECTED_HEARTBEATS)
        handoff = calls[-1]
        self.assertIsNotNone(_kw(handoff, "pr"), "pr-handoff must carry the PR number")
        for call in calls[:-1]:
            self.assertIsNone(_kw(call, "pr"))

    def test_heartbeat_method_uses_github_scope(self):
        method = _function(self.tree, "KernelRuntime", "_lease_heartbeat")
        execs = _method_calls(method, "_exec")
        self.assertEqual(len(execs), 1)
        self.assertEqual(_str(_kw(execs[0], "credential_scope")), "github")


class ScriptsCarryNoLeaseTests(unittest.TestCase):
    def test_protocol_and_proof_do_not_reference_the_lease_program(self):
        for path in (PROTOCOL, PROOF):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("factory_lease", text, path.name)
            self.assertNotIn("heartbeat(", text, path.name)
            self.assertNotIn("def lease(", text, path.name)


class CheckpointEnvironmentTests(unittest.TestCase):
    def test_checkpoint_runner_scrubs_both_tokens_from_the_real_child(self):
        proof = load_proof()
        probe = ["python", "-c",
                 "import os; print(sorted(k for k in os.environ if k in ('GH_TOKEN','GITHUB_TOKEN','KEEP_ME')))"]
        with mock.patch.dict(os.environ, {"GH_TOKEN": "a", "GITHUB_TOKEN": "b", "KEEP_ME": "c"}):
            rc, out = proof.run(probe, ".")
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.strip(), "['KEEP_ME']")

    def test_scrubbed_names_are_the_github_credential_names(self):
        from factory_kernel.credential_env import GITHUB_CREDENTIALS

        proof = load_proof()
        self.assertEqual(set(proof.CHECKPOINT_SCRUBBED_ENV), set(GITHUB_CREDENTIALS))


if __name__ == "__main__":
    unittest.main()
