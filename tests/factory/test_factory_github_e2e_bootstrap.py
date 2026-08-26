from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "harness"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = load_module("factory_bootstrap_e2e", HARNESS / "bootstrap_e2e.py")
serve = load_module("factory_serve", HARNESS / "serve.py")


class GitHubE2EBootstrapTests(unittest.TestCase):
    def test_bootstrap_accepts_only_dedicated_loopback_database(self) -> None:
        self.assertTrue(
            bootstrap.safe_local_validation_database(
                "postgresql://postgres:postgres@127.0.0.1:5432/dark_factory_validation"
            )
        )
        self.assertTrue(
            bootstrap.safe_local_validation_database(
                "postgres://postgres:postgres@localhost:5432/dark_factory_validation"
            )
        )
        for unsafe in (
            "postgresql://postgres:postgres@db.example.com:5432/dark_factory_validation",
            "postgresql://postgres:postgres@127.0.0.1:5432/production",
            "sqlite:///dark_factory_validation",
            "",
        ):
            self.assertFalse(bootstrap.safe_local_validation_database(unsafe), unsafe)

    def test_bootstrap_keeps_real_locked_fixture_and_real_ingest_path(self) -> None:
        cfg = (HARNESS / "harness.config.json").read_text(encoding="utf-8")
        source = (HARNESS / "bootstrap_e2e.py").read_text(encoding="utf-8")
        self.assertIn('"fixture_video_id": "pjF-0dliYhg"', cfg)
        self.assertIn("fetch_video_for_ingest", source)
        self.assertIn("ingest_video", source)
        self.assertNotIn("SEED_ENABLE", source)

    def test_full_server_uses_backend_virtualenv_and_package_import(self) -> None:
        source = (HARNESS / "serve.py").read_text(encoding="utf-8")
        self.assertIn('BACKEND / ".venv"', source)
        self.assertIn('"backend.main:app"', source)
        self.assertIn("cwd=APP", source)
        self.assertNotIn('"main:app"', source)
        self.assertEqual(
            serve.CORE_REQUIRED,
            ["DATABASE_URL", "OPENROUTER_API_KEY", "JWT_SECRET"],
        )
        self.assertEqual(serve.BOOTSTRAP_REQUIRED, ["SUPADATA_API_KEY"])

    def test_backend_migrations_use_running_locked_interpreter(self) -> None:
        source = (ROOT / "app" / "backend" / "main.py").read_text(encoding="utf-8")
        self.assertIn("import sys", source)
        self.assertIn(
            '[\n            sys.executable,\n            "-m",\n            "alembic",',
            source,
        )
        self.assertNotIn(
            '[\n            "uv",\n            "run",\n            "alembic",',
            source,
        )

    def test_worker_refuses_unprotected_main(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "dark-factory-worker.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "gh api \"repos/$GITHUB_REPOSITORY/branches/main\" --jq '.protected'",
            workflow,
        )
        self.assertIn('test "$main_protected" = "true" || {', workflow)
        self.assertIn("FACTORY_PREFLIGHT_REFUSED main branch is not protected", workflow)

    def test_worker_provisions_disposable_validation_state(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "dark-factory-worker.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("postgres:16", workflow)
        self.assertIn("dark_factory_validation", workflow)
        self.assertIn("DARK_FACTORY_E2E_BOOTSTRAP=1", workflow)
        self.assertIn("secrets.ANTHROPIC_API_KEY", workflow)
        self.assertIn("secrets.OPENROUTER_API_KEY", workflow)
        self.assertIn("secrets.SUPADATA_API_KEY", workflow)
        for removed_secret in (
            "secrets.DATABASE_URL",
            "secrets.JWT_SECRET",
            "secrets.YOUTUBE_CHANNEL_ID",
            "secrets.DARK_FACTORY_E2E_EMAIL",
            "secrets.DARK_FACTORY_E2E_PASSWORD",
        ):
            self.assertNotIn(removed_secret, workflow)


if __name__ == "__main__":
    unittest.main()
