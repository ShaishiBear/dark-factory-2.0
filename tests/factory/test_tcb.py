"""The TCB record (.factory/tcb.json) matches the kernel source: every module classified, the
import graph pinned, privileged roots imported only by permitted entrypoints, proposal/ui
modules reaching a privileged root only through a recorded violation that must disappear
when it is cut, and the authority closure inside its byte bounds. An import test supplements
the broker's OS boundary; it does not establish isolation, and the record says so."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from factory_kernel.canonical import sha256_value
from factory_kernel.tcb import (
    CLASSES,
    TCB_PATH,
    TcbRefused,
    first_path,
    import_graph,
    load_record,
    validate_record,
    verify,
    verify_repository,
)

ROOT = Path(__file__).resolve().parents[2]


def synthetic_kernel(tmp: Path, sources: dict[str, str]) -> Path:
    kernel = tmp / "factory_kernel"
    kernel.mkdir(parents=True)
    (kernel / "__init__.py").write_text("", encoding="utf-8")
    for name, body in sources.items():
        (kernel / f"{name}.py").write_text(body, encoding="utf-8")
    return tmp


def record_for(root: Path, *, classes: dict[str, str], roots: list[str], permitted: list[str], violations=(), bounds=None,
               include_init: bool = True) -> dict:
    graph = import_graph(root / "factory_kernel")
    modules = {name: {"class": cls, "reason": "test"} for name, cls in classes.items()}
    if include_init and "__init__" not in modules:
        modules["__init__"] = {"class": "orchestration", "reason": "package init"}
    return {
        "schema": "dark-factory/tcb", "schema_version": "1.0", "classes": list(CLASSES),
        "privileged_roots": roots, "permitted_privileged_entrypoints": permitted,
        "modules": modules,
        "known_violations": list(violations), "graph_sha256": sha256_value(graph),
        "closure_bounds": bounds or {"kernel": {"files": ["factory_kernel/__init__.py"], "per_file_max_bytes": 1000, "total_max_bytes": 1000}},
    }


# The record tests run everywhere: the mutation copy carries the whole kernel, the record and every
# closure file (COPY_FILES), so a missing record fails here instead of skipping.
class RepositoryRecordTests(unittest.TestCase):
    def test_the_committed_record_matches_the_kernel_source(self):
        result = verify_repository(ROOT)
        record = load_record(ROOT / TCB_PATH)
        self.assertEqual(result["modules"], len(record["modules"]))
        self.assertEqual(sorted(record["privileged_roots"]), ["credential_env", "git_authority", "github_cli"])
        # The authority closure is exactly what the host reads from protected main, measured under the reader's
        # bounds (exploration_repository.SELECTION_BOUND; 100 KB per file); the evidence-spine closure is what the
        # spine script hashes from the kernel checkout. Each is bounded separately.
        from factory_kernel.evidence_closure import CLOSURE_PROGRAMS, CONFIGURATION_FILES
        from factory_kernel.execution_authority import POLICY_FILES, PROGRAMS
        from factory_kernel.exploration_repository import SELECTION_BOUND
        authority = record["closure_bounds"]["authority"]
        self.assertEqual(sorted(authority["files"]), sorted(["factory_kernel/" + name for name in PROGRAMS] + list(POLICY_FILES)))
        self.assertEqual((authority["total_max_bytes"], authority["per_file_max_bytes"]), (SELECTION_BOUND, 100000))
        self.assertLessEqual(result["closures"]["authority"]["total"], SELECTION_BOUND - 50000, "keep 50 KB of headroom under the host reader's bound")
        spine = record["closure_bounds"]["evidence-spine"]
        self.assertEqual(sorted(spine["files"]), sorted(set(CLOSURE_PROGRAMS) | set(CONFIGURATION_FILES)))
        self.assertIn(TCB_PATH, POLICY_FILES, "the record is part of the closure the host verifies against protected main")
        # No TCB reduction is claimed while legacy orchestration still holds credentials.
        self.assertIn("runtime", result["trusted_until_boundary"])
        self.assertTrue(all(record["modules"][n]["class"] == "orchestration" for n in result["trusted_until_boundary"]))
        # Every recorded violation is a proposal/ui module and its path ends at a root.
        for item in record["known_violations"]:
            self.assertIn(record["modules"][item["module"]]["class"], ("proposal", "ui"))
            self.assertIn(item["path"][-1], record["privileged_roots"])

    def test_the_record_is_strict(self):
        record = load_record(ROOT / TCB_PATH)
        for name, mutate in (
            ("extra field", lambda r: r.update(extra=1)),
            ("other class vocabulary", lambda r: r.update(classes=["a"])),
            ("root not enforcement", lambda r: r["modules"]["github_cli"].update({"class": "ui"})),
            ("unknown root", lambda r: r.update(privileged_roots=["nowhere"])),
            ("legacy flag on proof policy", lambda r: r["modules"]["spine"].update(trusted_until_boundary=True)),
            ("violation not ending at a root", lambda r: r["known_violations"].append({"module": "claim_views", "path": ["claim_views", "canonical"], "reason": "x"})),
            ("bounds without total", lambda r: r["closure_bounds"]["authority"].pop("total_max_bytes")),
            ("no closures", lambda r: r.update(closure_bounds={})),
        ):
            value = json.loads(json.dumps(record)); mutate(value)
            with self.subTest(name), self.assertRaises(TcbRefused):
                validate_record(value)


class SyntheticKernelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="tcb-")))

    def test_drift_and_privilege_reach_are_refused_and_recorded_violations_must_stay_real(self):
        root = synthetic_kernel(self.tmp, {
            "creds": "TOKEN = 1\n", "broker": "from .creds import TOKEN\n", "view": "from . import canon\n",
            "canon": "X = 1\n", "orchestrate": "from .broker import TOKEN\n"})
        classes = {"creds": "privilege-enforcement", "broker": "privilege-enforcement", "view": "ui", "canon": "proof-policy",
                   "orchestrate": "orchestration"}
        good = record_for(root, classes=classes, roots=["creds"], permitted=["broker"])
        self.assertEqual(verify(good, root)["violations"], [])
        # An unclassified module (drift) refuses.
        (root / "factory_kernel" / "newcomer.py").write_text("Y = 2\n", encoding="utf-8")
        with self.assertRaisesRegex(TcbRefused, "unclassified"):
            verify(good, root)
        (root / "factory_kernel" / "newcomer.py").unlink()
        # A new import edge changes the graph digest and refuses until the record describes it.
        (root / "factory_kernel" / "canon.py").write_text("from . import orchestrate\nX = 1\n", encoding="utf-8")
        with self.assertRaisesRegex(TcbRefused, "import graph changed"):
            verify(good, root)
        (root / "factory_kernel" / "canon.py").write_text("X = 1\n", encoding="utf-8")
        # A module importing a root without permission refuses.
        (root / "factory_kernel" / "orchestrate.py").write_text("from .creds import TOKEN\n", encoding="utf-8")
        refreshed = record_for(root, classes=classes, roots=["creds"], permitted=["broker"])
        with self.assertRaisesRegex(TcbRefused, "without being a permitted entrypoint"):
            verify(refreshed, root)
        (root / "factory_kernel" / "orchestrate.py").write_text("from .broker import TOKEN\n", encoding="utf-8")
        # A ui module reaching a root through imports refuses unless the path is a recorded violation...
        (root / "factory_kernel" / "view.py").write_text("from . import canon\nfrom . import broker\n", encoding="utf-8")
        reaching = record_for(root, classes=classes, roots=["creds"], permitted=["broker"])
        with self.assertRaisesRegex(TcbRefused, "without a recorded violation"):
            verify(reaching, root)
        self.assertEqual(first_path(import_graph(root / "factory_kernel"), "view", {"creds"}), ["view", "broker", "creds"])
        allowed = record_for(root, classes=classes, roots=["creds"], permitted=["broker"],
                             violations=[{"module": "view", "path": ["view", "broker", "creds"], "reason": "known"}])
        self.assertEqual(verify(allowed, root)["violations"], [("view", ("view", "broker", "creds"))])
        # ...and a recorded violation that no longer exists must be removed (the allowance only shrinks).
        (root / "factory_kernel" / "view.py").write_text("from . import canon\n", encoding="utf-8")
        stale = record_for(root, classes=classes, roots=["creds"], permitted=["broker"],
                           violations=[{"module": "view", "path": ["view", "broker", "creds"], "reason": "known"}])
        with self.assertRaisesRegex(TcbRefused, "no longer exist"):
            verify(stale, root)

    def test_the_package_init_is_pinned_and_its_imports_belong_to_every_module(self):
        root = synthetic_kernel(self.tmp, {"creds": "TOKEN = 1\n", "view": "X = 1\n", "canon": "Y = 2\n"})
        classes = {"__init__": "orchestration", "creds": "privilege-enforcement", "view": "ui", "canon": "proof-policy"}
        record = record_for(root, classes=classes, roots=["creds"], permitted=[])
        self.assertEqual(verify(record, root)["violations"], [])
        self.assertIn("__init__", record["modules"])
        # A root imported by __init__ is imported by everything: the digest moves and the ui module reaches it.
        (root / "factory_kernel" / "__init__.py").write_text("from . import creds\n", encoding="utf-8")
        with self.assertRaisesRegex(TcbRefused, "import graph changed"):
            verify(record, root)
        graph = import_graph(root / "factory_kernel")
        self.assertEqual((graph["__init__"], graph["view"], graph["canon"]), (["creds"], ["creds"], ["creds"]))
        refreshed = record_for(root, classes=classes, roots=["creds"], permitted=[])
        with self.assertRaisesRegex(TcbRefused, "without being a permitted entrypoint"):
            verify(refreshed, root)
        # An unclassified __init__ is an unclassified module like any other.
        (root / "factory_kernel" / "__init__.py").write_text("", encoding="utf-8")
        partial = record_for(root, classes={k: v for k, v in classes.items() if k != "__init__"}, roots=["creds"], permitted=[],
                             include_init=False)
        with self.assertRaisesRegex(TcbRefused, "unclassified"):
            verify(partial, root)

    def test_closure_bounds_are_measured_on_normalised_bytes(self):
        root = synthetic_kernel(self.tmp, {"a": "X = 1\n"})
        (root / "big.py").write_bytes(b"#" * 900 + b"\r\n")
        record = record_for(root, classes={"a": "proof-policy"}, roots=["a"], permitted=[],
                            bounds={"host": {"files": ["big.py"], "per_file_max_bytes": 901, "total_max_bytes": 901}})
        record["modules"]["a"]["class"] = "privilege-enforcement"
        self.assertEqual(verify(record, root)["closures"]["host"], {"sizes": {"big.py": 901}, "total": 901})
        host = record["closure_bounds"]["host"]
        host["per_file_max_bytes"] = 900
        with self.assertRaisesRegex(TcbRefused, "above the per-file bound"):
            verify(record, root)
        host.update(per_file_max_bytes=901, total_max_bytes=900)
        with self.assertRaisesRegex(TcbRefused, "above the bound"):
            verify(record, root)
        host.update(files=["absent.py"], total_max_bytes=901)
        with self.assertRaisesRegex(TcbRefused, "missing"):
            verify(record, root)


if __name__ == "__main__":
    unittest.main()
