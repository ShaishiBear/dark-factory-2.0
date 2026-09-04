from pathlib import Path
import subprocess
import tempfile
import unittest

from factory_kernel.agents import (
    AgentRequest,
    AgentResult,
    ProviderCapabilities,
    ProviderRegistration,
    ProviderRegistry,
)
from factory_kernel.manifest import ArtifactRef, Certification, ClaimRecord, RunManifest
from factory_kernel.worktree import create_detached, remove


HEX_A = "a" * 64
HEX_B = "b" * 64
GIT_BASE = "1" * 40
GIT_HEAD = "2" * 40


class DummyProvider:
    provider_id = "dummy"
    capabilities = ProviderCapabilities(structured_output=True)

    def run(self, request: AgentRequest, **_kwargs: object) -> AgentResult:
        return AgentResult(provider_id="dummy", model=request.model or "dummy-1", content="ok")


class ProviderRegistryTests(unittest.TestCase):
    def test_registry_is_provider_neutral_and_rejects_duplicates(self):
        registry = ProviderRegistry()
        registration = ProviderRegistration(
            provider_id="dummy",
            display_name="Dummy",
            factory=DummyProvider,
            capabilities=DummyProvider.capabilities,
        )
        registry.register(registration)
        self.assertEqual(registry.provider_ids(), ("dummy",))
        self.assertEqual(registry.create("dummy").provider_id, "dummy")
        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register(registration)

    def test_invalid_provider_capability_combination_fails_closed(self):
        capabilities = ProviderCapabilities(session_fork=True, session_resume=False)
        with self.assertRaisesRegex(ValueError, "session_fork requires session_resume"):
            capabilities.validate()


class ManifestTests(unittest.TestCase):
    def artifact(self, name: str, sha: str) -> ArtifactRef:
        return ArtifactRef(name=name, path=f"artifacts/{name}.json", sha256=sha)

    def cert(self, kind: str, authority: str, name: str, sha: str) -> Certification:
        return Certification(kind=kind, authority_id=authority, artifact=self.artifact(name, sha))

    def test_material_claim_cannot_self_certify(self):
        with self.assertRaisesRegex(ValueError, "cannot deterministically certify"):
            ClaimRecord(
                claim_id="contract",
                stage="spec",
                producer="specifier-agent",
                artifact=self.artifact("contract", HEX_A),
                deterministic=self.cert(
                    "deterministic", "specifier-agent", "contract-validation", HEX_B
                ),
            )

    def test_independent_authority_must_be_distinct(self):
        with self.assertRaisesRegex(ValueError, "independent verifier must differ"):
            ClaimRecord(
                claim_id="design",
                stage="design",
                producer="architect-agent",
                artifact=self.artifact("design", HEX_A),
                deterministic=self.cert("deterministic", "design-validator", "design-det", HEX_B),
                independent=self.cert(
                    "independent", "design-validator", "design-independent", "c" * 64
                ),
            )

    def test_manifest_bindings_must_point_to_prior_artifacts(self):
        manifest = RunManifest.create(run_id="run-1", issue=42, base_sha=GIT_BASE)
        first = ClaimRecord(
            claim_id="contract",
            stage="spec",
            producer="specifier-agent",
            artifact=self.artifact("contract", HEX_A),
            deterministic=self.cert(
                "deterministic", "contract-validator", "contract-validation", HEX_B
            ),
            independent=self.cert(
                "independent", "contract-holdout", "contract-holdout", "c" * 64
            ),
        )
        manifest.add(first)
        manifest.add(
            ClaimRecord(
                claim_id="design",
                stage="design",
                producer="architect-agent",
                artifact=self.artifact("design", "d" * 64),
                deterministic=self.cert(
                    "deterministic", "design-validator", "design-validation", "e" * 64
                ),
                bindings={"contract_sha256": HEX_A},
            )
        )
        self.assertEqual(len(manifest.claims), 2)
        self.assertEqual(len(manifest.sha256()), 64)
        with self.assertRaisesRegex(ValueError, "earlier manifest artifacts"):
            manifest.add(
                ClaimRecord(
                    claim_id="bad",
                    stage="design",
                    producer="agent",
                    artifact=self.artifact("bad", "f" * 64),
                    bindings={"missing": "9" * 64},
                )
            )

    def test_code_bound_claim_requires_full_git_oid(self):
        with self.assertRaisesRegex(ValueError, "full git object id"):
            ClaimRecord(
                claim_id="green",
                stage="green",
                producer="green-runner",
                artifact=self.artifact("green", HEX_A),
                exact_head_sha="abc1234",
            )
        valid = ClaimRecord(
            claim_id="green",
            stage="green",
            producer="green-runner",
            artifact=self.artifact("green", HEX_A),
            exact_head_sha=GIT_HEAD,
        )
        self.assertEqual(valid.exact_head_sha, GIT_HEAD)

    def test_manifest_round_trips_with_two_authority_types(self):
        manifest = RunManifest.create(run_id="run-2", issue=7, base_sha=GIT_BASE)
        manifest.add(
            ClaimRecord(
                claim_id="contract",
                stage="spec",
                producer="specifier",
                artifact=self.artifact("contract", HEX_A),
                deterministic=self.cert("deterministic", "schema", "schema", HEX_B),
                independent=self.cert("independent", "spec-holdout", "holdout", "c" * 64),
            )
        )
        rebuilt = RunManifest.from_dict(manifest.to_dict())
        self.assertEqual(rebuilt.to_dict(), manifest.to_dict())
        self.assertEqual(rebuilt.version, "2.0")


class WorktreeTests(unittest.TestCase):
    def git(self, repo: Path, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return proc.stdout.strip()

    def test_detached_worktree_is_exact_sha_and_removable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            self.git(root, "init")
            self.git(root, "config", "user.email", "factory@example.invalid")
            self.git(root, "config", "user.name", "Dark Factory Test")
            (root / "x.txt").write_text("one\n", encoding="utf-8")
            self.git(root, "add", "x.txt")
            self.git(root, "commit", "-m", "one")
            expected = self.git(root, "rev-parse", "HEAD")
            worktree = create_detached(root, expected, base_dir=Path(tmp) / "worktrees")
            try:
                self.assertEqual(worktree.head_sha, expected)
                self.assertEqual(self.git(worktree.path, "rev-parse", "HEAD"), expected)
                self.assertEqual((worktree.path / "x.txt").read_text(encoding="utf-8"), "one\n")
            finally:
                remove(root, worktree)
            self.assertFalse(worktree.path.exists())


if __name__ == "__main__":
    unittest.main()
