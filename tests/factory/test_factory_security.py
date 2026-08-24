import importlib.util
import json
import unittest
from pathlib import Path

P = Path(__file__).parents[2] / "scripts" / "factory_security.py"
spec = importlib.util.spec_from_file_location("factory_security", P)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class SecurityGuardTests(unittest.TestCase):
    def backend(self, deps=None, dev=None, build=None):
        deps = deps or ["fastapi", "httpx"]
        dev = dev or ["pytest==8.3.4"]
        build = build or ["hatchling"]
        q = lambda xs: ", ".join(json.dumps(x) for x in xs)
        return (
            "[project]\nname='x'\nversion='0.1'\n"
            f"dependencies=[{q(deps)}]\n"
            "[project.optional-dependencies]\n"
            f"dev=[{q(dev)}]\n"
            "[build-system]\n"
            f"requires=[{q(build)}]\nbuild-backend='hatchling.build'\n"
        )

    def frontend(self, deps=None, dev=None):
        return json.dumps({
            "dependencies": deps or {"react": "^18.3.1"},
            "devDependencies": dev or {"vite": "^5.2.13"},
        })

    def evaluate(self, **overrides):
        values = {
            "changed_files": ["app/backend/routes/chat.py"],
            "base_backend": self.backend(),
            "head_backend": self.backend(),
            "base_frontend": self.frontend(),
            "head_frontend": self.frontend(),
            "diff": "diff --git a/app/backend/routes/chat.py b/app/backend/routes/chat.py\n"
                    "+++ b/app/backend/routes/chat.py\n+safe = True\n",
            "body": "Fixes #42\n",
        }
        values.update(overrides)
        return m.evaluate(**values)

    def test_clean_code_change_passes(self):
        self.assertEqual(self.evaluate()["verdict"], "pass")

    def test_factory_governance_path_is_blocked(self):
        result = self.evaluate(changed_files=[".archon/workflows/dark-factory-validate-pr.yaml"])
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["protected_paths"], [".archon/workflows/dark-factory-validate-pr.yaml"])

    def test_env_file_is_blocked(self):
        self.assertEqual(self.evaluate(changed_files=["app/backend/.env.production"])["verdict"], "fail")

    def test_backend_dependency_requires_lockfile(self):
        result = self.evaluate(
            changed_files=[m.BACKEND_MANIFEST],
            head_backend=self.backend(deps=["fastapi", "httpx", "resend"]),
            body="## Dependency justification\nresend is required for transactional email.\n",
        )
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any(x["kind"] == "lockfile" for x in result["findings"]))

    def test_frontend_dependency_requires_lockfile(self):
        result = self.evaluate(
            changed_files=[m.FRONTEND_MANIFEST],
            head_frontend=self.frontend(deps={"react": "^18.3.1", "zod": "^4.0.0"}),
            body="## Dependency justification\nzod validates API payloads.\n",
        )
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any(x["path"] == m.FRONTEND_LOCK for x in result["findings"]))

    def test_added_dependency_requires_named_justification(self):
        result = self.evaluate(
            changed_files=[m.BACKEND_MANIFEST, m.BACKEND_LOCK],
            head_backend=self.backend(deps=["fastapi", "httpx", "resend"]),
            body="## Dependency justification\nNeeded for email.\n",
        )
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any("resend" in x["detail"] for x in result["findings"]))

    def test_added_dependency_with_lock_and_named_justification_passes(self):
        result = self.evaluate(
            changed_files=[m.BACKEND_MANIFEST, m.BACKEND_LOCK],
            head_backend=self.backend(deps=["fastapi", "httpx", "resend"]),
            body="## Dependency justification\nresend provides the required email transport.\n",
        )
        self.assertEqual(result["verdict"], "pass")

    def test_version_change_requires_justification(self):
        result = self.evaluate(
            changed_files=[m.FRONTEND_MANIFEST, m.FRONTEND_LOCK],
            head_frontend=self.frontend(dev={"vite": "^6.0.0"}),
        )
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any("vite" in x["detail"] for x in result["findings"]))

    def test_removed_dependency_does_not_require_justification(self):
        result = self.evaluate(
            changed_files=[m.FRONTEND_MANIFEST, m.FRONTEND_LOCK],
            base_frontend=self.frontend(deps={"react": "^18.3.1", "zod": "^4.0.0"}),
            head_frontend=self.frontend(deps={"react": "^18.3.1"}),
        )
        self.assertEqual(result["verdict"], "pass")

    def test_lockfile_only_refresh_requires_justification(self):
        result = self.evaluate(changed_files=[m.BACKEND_LOCK])
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any(x["kind"] == "dependency_justification" for x in result["findings"]))

    def test_direct_python_url_dependency_is_blocked_and_source_is_redacted(self):
        result = self.evaluate(
            changed_files=[m.BACKEND_MANIFEST, m.BACKEND_LOCK],
            head_backend=self.backend(deps=["fastapi", "thing @ https://user:secret@example.com/thing.whl"]),
            body="## Dependency justification\nthing is required.\n",
        )
        self.assertTrue(any(x["kind"] == "dependency_source" for x in result["findings"]))
        serialized = json.dumps(result)
        self.assertNotIn("user:secret", serialized)
        self.assertNotIn("example.com/thing.whl", serialized)

    def test_javascript_git_dependency_is_blocked(self):
        result = self.evaluate(
            changed_files=[m.FRONTEND_MANIFEST, m.FRONTEND_LOCK],
            head_frontend=self.frontend(deps={"react": "^18.3.1", "thing": "git+https://github.com/x/y.git"}),
            body="## Dependency justification\nthing is required.\n",
        )
        self.assertTrue(any(x["kind"] == "dependency_source" for x in result["findings"]))
        self.assertNotIn("github.com/x/y.git", json.dumps(result))

    def test_private_key_added_line_is_blocked_without_echoing_secret(self):
        diff = "diff --git a/x.py b/x.py\n+++ b/x.py\n+-----BEGIN PRIVATE KEY-----\n"
        result = self.evaluate(diff=diff)
        self.assertEqual(result["verdict"], "fail")
        self.assertEqual(result["secret_findings"], [{"kind": "private_key", "path": "x.py"}])
        self.assertNotIn("BEGIN PRIVATE KEY", json.dumps(result))

    def test_database_url_with_inline_credentials_is_blocked(self):
        diff = "diff --git a/x.py b/x.py\n+++ b/x.py\n+DB='postgresql://alice:supersecret@db.internal/app'\n"
        result = self.evaluate(diff=diff)
        self.assertTrue(any(x["kind"] == "secret" for x in result["findings"]))
        self.assertNotIn("alice:supersecret", json.dumps(result))

    def test_placeholder_secret_is_not_flagged_by_generic_rule(self):
        diff = "diff --git a/example.py b/example.py\n+++ b/example.py\n+API_KEY='your_api_key_placeholder'\n"
        self.assertEqual(self.evaluate(diff=diff)["verdict"], "pass")


if __name__ == "__main__":
    unittest.main()
