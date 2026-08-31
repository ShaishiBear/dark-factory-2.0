"""Adversarial coverage for the one-time genesis authority.

The PR that rewrites the factory trust root cannot be certified by that trust root: running the
candidate's own harness from the candidate's own tree proves self-consistency, not
trustworthiness. Nor can the candidate be allowed to author the standard it is judged against --
an artificially weak bar can be met honestly. These tests pin the boundary between the four
documents: an external policy says what must be proven, the candidate manifest describes only
what the tree is, the evidence says what a real run observed, and the external verifier proves
the relations.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).parents[2]
VERIFIER = ROOT / "harness" / "bootstrap_verify.py"
MANIFEST_REL = ".factory/bootstrap/genesis.json"
PREFIXES = ["factory_kernel/", "harness/", "tests/factory/"]
COMMIT_RE = "0" * 40


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120)
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout


DRIVER_SHA = "d" * 64
RECIPE_SHA = "e" * 64
AGGREGATOR_SHA = "f" * 64
WORKFLOW_SHA = "1" * 64
WORKFLOW_COMMIT = "2" * 40
STAGES = ("focused-factory-suite", "static-gate", "unit-gate", "quick-gate",
          "application-mutations", "factory-mutations")


def result_doc(commit: str, *, stages=None, driver=DRIVER_SHA, recipe=RECIPE_SHA,
               aggregator=AGGREGATOR_SHA, **over) -> dict:
    """A structured result of the shape the pinned external driver emits."""
    values = {
        "focused_tests": 328, "unit_tests": 766, "static_checks": 5,
        "factory_mutations_total": 102, "factory_mutations_caught": 102,
        "factory_mutations_not_injected": 0,
        "application_mutations_total": 9, "application_mutations_caught": 9,
        "application_mutations_not_injected": 0,
    }
    values.update(over)
    measured = {
        "focused-factory-suite": {"focused_tests": values["focused_tests"]},
        "static-gate": {"static_checks": values["static_checks"]},
        "unit-gate": {"unit_tests": values["unit_tests"]},
        "application-mutations": {
            k: values[k] for k in values if k.startswith("application_mutations_")
        },
        "factory-mutations": {k: values[k] for k in values if k.startswith("factory_mutations_")},
    }
    names = STAGES if stages is None else tuple(stages)
    return {
        "version": "1.0",
        "driver_sha256": driver,
        "recipe_sha256": recipe,
        "aggregator_sha256": aggregator,
        "stage_isolation": "one-disposable-runner-per-stage",
        "candidate_sha": commit,
        "verdict": "pass",
        "failed_stages": [],
        "stages": [
            {"name": n, "argv": ["python", f"{n}.py"], "exit": 0,
             "measurements": measured.get(n, {}), "output_sha256": "0" * 64}
            for n in names
        ],
    }


class Ceremony:
    """A miniature repository plus the human-held documents, none of it on the live tree."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.path = tmp / "repo"
        for rel in ("harness", "factory_kernel", "tests/factory", ".factory/bootstrap"):
            (self.path / rel).mkdir(parents=True)
        shutil.copy2(VERIFIER, self.path / "harness" / "bootstrap_verify.py")
        for rel, body in (
            ("harness/genesis_validate.py", "DRIVER = 1\n"),
            ("harness/genesis_aggregate.py", "AGG = 1\n"),
            ("harness/genesis-recipe.json", '{"version": "1.0"}\n'),
        ):
            (self.path / rel).write_text(body, encoding="utf-8")
        (self.path / "factory_kernel" / "spine.py").write_text("POLICY = 1\n", encoding="utf-8")
        (self.path / "factory_kernel" / "independence.py").write_text("REG = ()\n", encoding="utf-8")
        (self.path / "tests" / "factory" / "test_x.py").write_text("assert True\n", encoding="utf-8")
        (self.path / "MISSION.md").write_text("mission\n", encoding="utf-8")
        git(self.path, "init", "-q", "-b", "main")
        git(self.path, "config", "user.email", "genesis@example.invalid")
        git(self.path, "config", "user.name", "Genesis")
        git(self.path, "add", "-A")
        git(self.path, "commit", "-qm", "base")
        self.base = git(self.path, "rev-parse", "HEAD").strip()
        # The human's copies live outside the repository under test.
        self.verifier = tmp / "reviewed_bootstrap_verify.py"
        shutil.copy2(VERIFIER, self.verifier)

    def verifier_sha(self) -> str:
        return sha256(self.verifier.read_bytes())

    def pinned(self, rel: str) -> str:
        return sha256((self.path / rel).read_bytes())

    def commit_all(self, message: str = "change") -> None:
        git(self.path, "add", "-A")
        git(self.path, "commit", "-qm", message)

    def result(self, commit: str, **over) -> dict:
        """A result whose stated identities match the blobs the verifier will recompute."""
        return result_doc(
            commit,
            driver=self.pinned("harness/genesis_validate.py"),
            recipe=self.pinned("harness/genesis-recipe.json"),
            aggregator=self.pinned("harness/genesis_aggregate.py"),
            **over,
        )

    def policy(self, **over) -> dict:
        value = {
            "version": "1.0",
            "repository": "example/repo",
            "pull_request": 33,
            "base_sha": self.base,
            "required_trust_root_prefixes": ["factory_kernel/", "harness/", "tests/factory/"],
            "required_trust_root_paths": ["factory_kernel/spine.py", "MISSION.md"],
            "required_policy_files": ["factory_kernel/spine.py"],
            "required_stages": ["focused-factory-suite", "static-gate", "unit-gate",
                                "application-mutations", "factory-mutations"],
            "required_holdout_classes": {"quick": "quick-gate"},
            "required_external_evidence": {},
            "required_mutation_families": ["factory_mutations", "application_mutations"],
            "minimum": {"focused_tests": 300, "unit_tests": 700, "static_checks": 5},
            "validation_driver_sha256": self.pinned("harness/genesis_validate.py"),
            "validation_recipe_sha256": self.pinned("harness/genesis-recipe.json"),
            "validation_aggregator_sha256": self.pinned("harness/genesis_aggregate.py"),
            "validation_workflow_sha256": WORKFLOW_SHA,
            "validation_workflow_commit_sha": WORKFLOW_COMMIT,
        }
        value.update(over)
        return value

    def write_policy(self, policy: dict) -> Path:
        path = self.tmp / "genesis-policy.json"
        path.write_text(json.dumps(policy, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def inventory(self, prefixes: list[str]) -> dict[str, str]:
        head = git(self.path, "rev-parse", "HEAD").strip()
        listing = git(self.path, "ls-tree", "-r", "--name-only", head).splitlines()
        out = {}
        for p in listing:
            if any(p == x or p.startswith(x) for x in prefixes):
                out[p] = sha256(
                    subprocess.run(
                        ["git", "-C", str(self.path), "cat-file", "blob", f"{head}:{p}"],
                        capture_output=True, timeout=60,
                    ).stdout
                )
        return out

    def manifest(self, *, prefixes: list[str] | None = None, **over) -> dict:
        prefixes = prefixes or (PREFIXES + ["MISSION.md"])
        files = self.inventory(prefixes)
        value = {
            "version": "3.0",
            "base_sha": self.base,
            "verifier_sha256": self.verifier_sha(),
            "trust_root_prefixes": prefixes,
            "trust_root": files,
            "policy_sha256": {"factory_kernel/spine.py": files["factory_kernel/spine.py"]},
        }
        value.update(over)
        return value

    def write_manifest(self, manifest: dict) -> str:
        raw = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
        (self.path / MANIFEST_REL).write_bytes(raw)
        git(self.path, "add", "-A")
        git(self.path, "commit", "-qm", "genesis manifest")
        return sha256(raw)

    def evidence(self, commit: str, log: str, result: dict, **over) -> dict:
        value = {
            "repository": "example/repo",
            "workflow": "independence-validation",
            "run_id": "12345",
            "run_url": "https://example.invalid/run/12345",
            "conclusion": "success",
            "workflow_commit_sha": WORKFLOW_COMMIT,
            "workflow_sha256": WORKFLOW_SHA,
            "candidate_sha": commit,
            "log_sha256": sha256(log.encode()),
            "result_sha256": sha256(json.dumps(result, indent=2, sort_keys=True).encode() + b"\n"),
        }
        value.update(over)
        return value

    def run(self, *, policy=None, manifest_sha=None, verifier=None, expect_policy=None,
            commit=None, evidence=None, log=None, result=None, policy_path=None,
            run_url=None, extra=None):
        head = git(self.path, "rev-parse", "HEAD").strip()
        target = commit or head
        raw = subprocess.run(
            ["git", "-C", str(self.path), "cat-file", "blob", f"{head}:{MANIFEST_REL}"],
            capture_output=True, timeout=60,
        ).stdout
        policy_value = policy if policy is not None else self.policy()
        ppath = policy_path or self.write_policy(policy_value)
        log_value = log if log is not None else "raw ci log\n"
        log_file = self.tmp / "run.log"
        log_file.write_text(log_value, encoding="utf-8")
        if result is None:
            result_value = result_doc(
                target,
                driver=self.pinned("harness/genesis_validate.py"),
                recipe=self.pinned("harness/genesis-recipe.json"),
                aggregator=self.pinned("harness/genesis_aggregate.py"),
            )
        else:
            result_value = result
        result_file = self.tmp / "validation-result.json"
        result_file.write_text(
            json.dumps(result_value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        ev = evidence if evidence is not None else self.evidence(target, log_value, result_value)
        ev_file = self.tmp / "evidence.json"
        ev_file.write_text(json.dumps(ev), encoding="utf-8")
        argv = [
            sys.executable, str(self.verifier),
            "--repo", str(self.path), "--commit", target,
            "--policy", str(ppath),
            "--expect-policy", expect_policy or sha256(Path(ppath).read_bytes()),
            "--expect-verifier", verifier or self.verifier_sha(),
            "--expect-manifest", manifest_sha or sha256(raw),
            "--evidence", str(ev_file), "--evidence-log", str(log_file),
            "--result", str(result_file),
            "--evidence-run", run_url or "https://example.invalid/run/12345",
            "--approver", "repository owner",
            "--reason", "genesis: this PR replaces the machinery governing future PRs",
        ]
        return subprocess.run(argv + list(extra or []), cwd=self.tmp, capture_output=True,
                              text=True, timeout=180)


class GenesisCeremonyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.c = Ceremony(Path(self.tmpdir.name))

    # ---------- the happy path ----------

    def test_valid_genesis_is_authorized(self):
        self.c.write_manifest(self.c.manifest())
        proc = self.c.run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("BOOTSTRAP_OK", proc.stdout)

    def test_authorization_binds_every_document_and_the_human_act(self):
        self.c.write_manifest(self.c.manifest())
        out = self.c.tmp / "auth.json"
        proc = self.c.run(extra=["--output", str(out)])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        auth = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(auth["scope"], "one-time-genesis")
        self.assertEqual(auth["candidate_sha"], git(self.c.path, "rev-parse", "HEAD").strip())
        for field in (
            "genesis_policy_sha256", "verifier_sha256", "manifest_sha256", "evidence_sha256",
            "evidence_log_sha256", "evidence_run", "candidate_tree", "base_sha",
            "approver", "reason", "authorized_at", "repository",
            "validation_result_sha256", "validation_driver_sha256", "validation_recipe_sha256",
            "validation_aggregator_sha256", "validation_workflow_commit_sha",
            "validation_workflow_sha256",
        ):
            self.assertTrue(str(auth.get(field) or "").strip(), field)
        self.assertEqual(auth["observed"]["factory_mutations_caught"], 102)
        self.assertEqual(
            auth["validation_driver_sha256"], self.c.pinned("harness/genesis_validate.py")
        )
        self.assertEqual(
            auth["validation_recipe_sha256"], self.c.pinned("harness/genesis-recipe.json")
        )
        self.assertEqual(
            auth["validation_aggregator_sha256"], self.c.pinned("harness/genesis_aggregate.py")
        )

    # ---------- the external policy is not the candidate's to write ----------

    def test_external_policy_hash_bypass_is_refused(self):
        self.c.write_manifest(self.c.manifest())
        proc = self.c.run(expect_policy="7" * 64)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("genesis policy is not the one that was reviewed", proc.stderr)

    def test_manifest_that_was_not_reviewed_is_refused(self):
        self.c.write_manifest(self.c.manifest())
        proc = self.c.run(manifest_sha="6" * 64)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("genesis manifest is not the one that was reviewed", proc.stderr)

    def test_commit_must_be_named_exactly_not_by_a_moving_reference(self):
        """A symbolic ref could point somewhere else later; genesis names one immutable commit."""
        self.c.write_manifest(self.c.manifest())
        proc = self.c.run(commit="main")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("does not resolve to itself", proc.stderr)

    def test_policy_taken_from_inside_the_repository_is_refused(self):
        self.c.write_manifest(self.c.manifest())
        inside = self.c.path / "genesis-policy.json"
        inside.write_text(json.dumps(self.c.policy()), encoding="utf-8")
        proc = self.c.run(policy_path=inside)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("outside the repository under test", proc.stderr)

    def test_candidate_cannot_define_the_standard_it_is_judged_by(self):
        for key in ("evidence_requirements", "minimum", "authorization", "approved_by"):
            with self.subTest(key=key):
                c = Ceremony(Path(tempfile.mkdtemp(dir=self.tmpdir.name)))
                c.write_manifest(c.manifest(**{key: {"focused_tests": 1}}))
                proc = c.run()
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn("must not define the standard", proc.stderr)

    def test_candidate_cannot_narrow_trust_root_scope(self):
        self.c.write_manifest(self.c.manifest(prefixes=["factory_kernel/", "MISSION.md"]))
        proc = self.c.run()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("narrows the trust-root scope", proc.stderr)

    def test_candidate_cannot_omit_a_mandatory_trust_root_path(self):
        manifest = self.c.manifest()
        manifest["trust_root"].pop("MISSION.md")
        manifest["trust_root_prefixes"] = PREFIXES
        self.c.write_manifest(manifest)
        proc = self.c.run()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("omits mandatory trust-root paths", proc.stderr)

    def test_candidate_cannot_omit_a_mandatory_pinned_policy(self):
        manifest = self.c.manifest()
        manifest["policy_sha256"] = {"harness/bootstrap_verify.py": manifest["trust_root"]["harness/bootstrap_verify.py"]}
        self.c.write_manifest(manifest)
        proc = self.c.run()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("omits mandatory pinned policies", proc.stderr)

    # ---------- the self-reference trap ----------

    def test_manifest_inside_its_own_hashed_prefix_is_refused(self):
        """No fixed point exists if the inventory must contain the inventory's own hash."""
        policy = self.c.policy(
            required_trust_root_prefixes=["factory_kernel/", "harness/", "tests/factory/", ".factory/"]
        )
        self.c.write_manifest(self.c.manifest(prefixes=PREFIXES + ["MISSION.md", ".factory/"]))
        proc = self.c.run(policy=policy)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no fixed point exists", proc.stderr)

    def test_manifest_listing_itself_is_refused(self):
        manifest = self.c.manifest()
        manifest["trust_root"][MANIFEST_REL] = "8" * 64
        self.c.write_manifest(manifest)
        proc = self.c.run()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("lists itself", proc.stderr)

    def test_inventory_is_stable_across_the_manifest_commit(self):
        """The manifest is excluded on principle, so adding it does not disturb the inventory."""
        before = self.c.inventory(PREFIXES + ["MISSION.md"])
        self.c.write_manifest(self.c.manifest())
        after = self.c.inventory(PREFIXES + ["MISSION.md"])
        self.assertEqual(before, after)
        self.assertNotIn(MANIFEST_REL, after)

    # ---------- measurement lives in the pinned driver, not in candidate output ----------

    def test_measurements_come_from_the_structured_result(self):
        """A weak measurement in the driver's result is refused however the log reads."""
        self.c.write_manifest(self.c.manifest())
        head = git(self.c.path, "rev-parse", "HEAD").strip()
        proc = self.c.run(result=self.c.result(head, unit_tests=10))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("observed unit_tests=10 is below the required 700", proc.stderr)

    def test_marker_shaped_text_in_the_log_cannot_supply_a_measurement(self):
        """The historical spoof: candidate stdout printing authority-looking markers."""
        self.c.write_manifest(self.c.manifest())
        head = git(self.c.path, "rev-parse", "HEAD").strip()
        spoof = (
            "FOCUSED_OK tests=999999\nFACTORY_MUTATIONS_TOTAL=1\n"
            "FACTORY_MUTATIONS_CAUGHT=1\nFACTORY_MUTATIONS_NOT_INJECTED=0\n"
        )
        result = self.c.result(head, factory_mutations_caught=100)  # a real escape
        proc = self.c.run(log=spoof, result=result)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("let a mutation escape", proc.stderr)

    def test_result_not_assembled_by_the_pinned_aggregator_is_refused(self):
        self.c.write_manifest(self.c.manifest())
        head = git(self.c.path, "rev-parse", "HEAD").strip()
        proc = self.c.run(result=self.c.result(head) | {"aggregator_sha256": "9" * 64})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not assembled by the aggregator the policy pins", proc.stderr)

    def test_result_without_per_runner_stage_isolation_is_refused(self):
        """A result produced by sequencing stages in one environment is not the same evidence."""
        self.c.write_manifest(self.c.manifest())
        head = git(self.c.path, "rev-parse", "HEAD").strip()
        proc = self.c.run(result=self.c.result(head) | {"stage_isolation": "per-stage-worktree"})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not produced by one disposable runner per stage", proc.stderr)

    def test_run_of_another_workflow_commit_is_refused(self):
        self.c.write_manifest(self.c.manifest())
        head = git(self.c.path, "rev-parse", "HEAD").strip()
        log, result = "raw ci log\n", self.c.result(head)
        proc = self.c.run(
            log=log, result=result,
            evidence=self.c.evidence(head, log, result, workflow_commit_sha="9" * 40),
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not the workflow commit the policy pins", proc.stderr)

    def test_run_of_altered_workflow_content_is_refused(self):
        self.c.write_manifest(self.c.manifest())
        head = git(self.c.path, "rev-parse", "HEAD").strip()
        log, result = "raw ci log\n", self.c.result(head)
        proc = self.c.run(
            log=log, result=result,
            evidence=self.c.evidence(head, log, result, workflow_sha256="9" * 64),
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("did not use the workflow content the policy pins", proc.stderr)

    def test_candidate_driver_not_matching_the_policy_pin_is_refused(self):
        """Recomputed from the object store, not believed from the result document."""
        policy = self.c.policy()
        (self.c.path / "harness/genesis_validate.py").write_text("DRIVER = 2\n", encoding="utf-8")
        self.c.commit_all("tamper")
        self.c.write_manifest(self.c.manifest())
        proc = self.c.run(policy=policy)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("genesis_validate.py at the candidate does not match", proc.stderr)

    def test_candidate_aggregator_not_matching_the_policy_pin_is_refused(self):
        policy = self.c.policy()
        (self.c.path / "harness/genesis_aggregate.py").write_text("AGG = 2\n", encoding="utf-8")
        self.c.commit_all("tamper")
        self.c.write_manifest(self.c.manifest())
        proc = self.c.run(policy=policy)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("genesis_aggregate.py at the candidate does not match", proc.stderr)

    def test_candidate_recipe_not_matching_the_policy_pin_is_refused(self):
        policy = self.c.policy()
        (self.c.path / "harness/genesis-recipe.json").write_text('{"version": "2.0"}\n', encoding="utf-8")
        self.c.commit_all("tamper")
        self.c.write_manifest(self.c.manifest())
        proc = self.c.run(policy=policy)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("genesis-recipe.json at the candidate does not match", proc.stderr)

    def test_result_not_produced_by_the_pinned_driver_is_refused(self):
        self.c.write_manifest(self.c.manifest())
        head = git(self.c.path, "rev-parse", "HEAD").strip()
        proc = self.c.run(result=self.c.result(head, **{}) | {"driver_sha256": "a" * 64})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not produced by the driver the policy pins", proc.stderr)

    def test_result_from_another_recipe_is_refused(self):
        self.c.write_manifest(self.c.manifest())
        head = git(self.c.path, "rev-parse", "HEAD").strip()
        proc = self.c.run(result=self.c.result(head) | {"recipe_sha256": "b" * 64})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("did not execute the recipe the policy pins", proc.stderr)

    def test_omitted_mandatory_stage_is_refused(self):
        self.c.write_manifest(self.c.manifest())
        head = git(self.c.path, "rev-parse", "HEAD").strip()
        remaining = [s for s in STAGES if s != "static-gate"]
        proc = self.c.run(result=self.c.result(head, stages=remaining))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("omits mandatory stages: static-gate", proc.stderr)

    def test_omitted_holdout_stage_is_refused(self):
        self.c.write_manifest(self.c.manifest())
        head = git(self.c.path, "rev-parse", "HEAD").strip()
        remaining = [s for s in STAGES if s != "quick-gate"]
        proc = self.c.run(result=self.c.result(head, stages=remaining))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("omits mandatory stages: quick-gate", proc.stderr)

    def test_failed_stage_is_refused(self):
        self.c.write_manifest(self.c.manifest())
        head = git(self.c.path, "rev-parse", "HEAD").strip()
        result = self.c.result(head)
        result["stages"][1]["exit"] = 1
        proc = self.c.run(result=result)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("did not succeed", proc.stderr)

    def test_duplicate_stage_names_are_refused(self):
        self.c.write_manifest(self.c.manifest())
        head = git(self.c.path, "rev-parse", "HEAD").strip()
        result = self.c.result(head)
        result["stages"].append(dict(result["stages"][0]))
        proc = self.c.run(result=result)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("repeats a stage name", proc.stderr)

    def test_substituted_result_document_is_refused(self):
        """The evidence commits to the result's digest, so a swapped result fails closed."""
        self.c.write_manifest(self.c.manifest())
        head = git(self.c.path, "rev-parse", "HEAD").strip()
        log = "raw ci log\n"
        honest = self.c.result(head)
        proc = self.c.run(
            log=log, result=self.c.result(head, factory_mutations_total=1, factory_mutations_caught=1),
            evidence=self.c.evidence(head, log, honest),
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("does not match the digest the evidence commits to", proc.stderr)

    def test_substituted_log_is_refused(self):
        self.c.write_manifest(self.c.manifest())
        head = git(self.c.path, "rev-parse", "HEAD").strip()
        result = self.c.result(head)
        proc = self.c.run(
            log="different log\n",
            evidence=self.c.evidence(head, "raw ci log\n", result),
            result=result,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("validation log does not match the digest", proc.stderr)

    def test_result_for_another_commit_is_refused(self):
        self.c.write_manifest(self.c.manifest())
        head = git(self.c.path, "rev-parse", "HEAD").strip()
        proc = self.c.run(result=self.c.result("0" * 40) | {"candidate_sha": "0" * 40})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("different commit than the one being authorized", proc.stderr)

    def test_parent_commit_evidence_cannot_authorize_the_manifest_bearing_child(self):
        parent = git(self.c.path, "rev-parse", "HEAD").strip()
        self.c.write_manifest(self.c.manifest())
        child = git(self.c.path, "rev-parse", "HEAD").strip()
        self.assertNotEqual(parent, child)
        log, result = "raw ci log\n", self.c.result(child)
        proc = self.c.run(evidence=self.c.evidence(parent, log, result), log=log, result=result)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("different commit than the one being authorized", proc.stderr)

    def test_evidence_for_another_run_identity_is_refused(self):
        self.c.write_manifest(self.c.manifest())
        proc = self.c.run(run_url="https://example.invalid/run/99999")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("does not name the run identity supplied", proc.stderr)

    def test_mutation_invariant_is_enforced_from_the_result(self):
        self.c.write_manifest(self.c.manifest())
        head = git(self.c.path, "rev-parse", "HEAD").strip()
        for over, expected in (
            ({"factory_mutations_caught": 101}, "let a mutation escape"),
            ({"factory_mutations_not_injected": 2}, "failed to inject"),
            ({"factory_mutations_total": 0, "factory_mutations_caught": 0}, "ran nothing"),
        ):
            with self.subTest(over=over):
                proc = self.c.run(result=self.c.result(head, **over))
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn(expected, proc.stderr)

    def test_unsuccessful_run_is_refused(self):
        self.c.write_manifest(self.c.manifest())
        head = git(self.c.path, "rev-parse", "HEAD").strip()
        log, result = "raw ci log\n", self.c.result(head)
        proc = self.c.run(
            log=log, result=result,
            evidence=self.c.evidence(head, log, result, conclusion="failure"),
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("did not conclude successfully", proc.stderr)

    # ---------- the verifier itself ----------

    def test_verifier_imports_nothing_from_the_trust_root_it_certifies(self):
        source = VERIFIER.read_text(encoding="utf-8")
        for banned in ("factory_kernel", "factory_security", "harness.", "from scripts"):
            self.assertNotIn(f"import {banned}", source)
            self.assertNotIn(f"from {banned}", source)

    def test_modified_verifier_is_refused(self):
        self.c.write_manifest(self.c.manifest())
        self.c.verifier.write_text(
            self.c.verifier.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8"
        )
        proc = self.c.run(verifier="4" * 64)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not the one that was reviewed", proc.stderr)

    def test_trust_root_content_change_is_refused(self):
        manifest = self.c.manifest()
        manifest["trust_root"]["factory_kernel/spine.py"] = "1" * 64
        self.c.write_manifest(manifest)
        proc = self.c.run()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("does not match", proc.stderr)

    def test_unlisted_trust_root_file_is_refused(self):
        manifest = self.c.manifest()
        manifest_sha = self.c.write_manifest(manifest)
        (self.c.path / "factory_kernel" / "sneaky.py").write_text("BYPASS = True\n", encoding="utf-8")
        git(self.c.path, "add", "-A")
        git(self.c.path, "commit", "-qm", "smuggle")
        proc = self.c.run(manifest_sha=manifest_sha)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unlisted", proc.stderr)

    def test_working_tree_tampering_is_invisible(self):
        self.c.write_manifest(self.c.manifest())
        for rel in ("harness/bootstrap_verify.py", "factory_kernel/spine.py", MANIFEST_REL):
            (self.c.path / rel).write_text("TAMPERED = True\n", encoding="utf-8")
        proc = self.c.run()
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_every_ceremony_value_is_required(self):
        self.c.write_manifest(self.c.manifest())
        head = git(self.c.path, "rev-parse", "HEAD").strip()
        proc = subprocess.run(
            [sys.executable, str(self.c.verifier), "--repo", str(self.c.path), "--commit", head],
            cwd=self.c.tmp, capture_output=True, text=True, timeout=120,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("supplied by a human at ceremony time", proc.stderr)


class BootstrapIsNotMergeAuthorityTests(unittest.TestCase):
    def test_no_merge_path_consumes_the_genesis_exception(self):
        for rel in (
            "harness/merge_verify.py", "factory_kernel/evidence_closure.py",
            "factory_kernel/spine.py", "factory_kernel/runtime.py",
            "factory_kernel/worker_runtime.py", "scripts/factory_evidence.py",
            "scripts/factory_evidence_spine.py",
        ):
            with self.subTest(path=rel):
                self.assertNotIn("bootstrap", (ROOT / rel).read_text(encoding="utf-8").lower())

    def test_ordinary_trust_root_change_still_fails_closed(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("fs", ROOT / "scripts/factory_security.py")
        fs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fs)
        for path in ("factory_kernel/independence.py", "harness/merge_verify.py",
                     "harness/bootstrap_verify.py", "tests/factory/test_factory_bootstrap.py"):
            self.assertTrue(fs.protected_path(path), path)


if __name__ == "__main__":
    unittest.main()
