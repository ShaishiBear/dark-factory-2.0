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

    def test_native_pr_ci_pins_toolchain_and_scrubs_checkout_token(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "dark-factory-ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
            workflow,
        )
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn(
            "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
            workflow,
        )
        self.assertIn("python-version: '3.12.14'", workflow)
        self.assertIn(
            "astral-sh/setup-uv@d0cc045d04ccac9d8b7881df0226f9e82c39688e",
            workflow,
        )
        self.assertIn("version: '0.12.5'", workflow)
        self.assertIn(
            "oven-sh/setup-bun@0c5077e51419868618aeaa5fe8019c62421857d6",
            workflow,
        )
        self.assertIn("bun-version: '1.4.0'", workflow)
        for floating in (
            "actions/checkout@v4",
            "actions/setup-python@v5",
            "astral-sh/setup-uv@v6",
            "oven-sh/setup-bun@v2",
        ):
            self.assertNotIn(floating, workflow)

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

    def test_worker_preflight_requires_every_label_the_kernel_can_apply(self) -> None:
        """The list is read from the kernel, not typed into YAML, and a missing label refuses."""
        workflow = (ROOT / ".github" / "workflows" / "dark-factory-worker.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "label_vocabulary(json.load(open(\".factory/kernel.json\"))[\"labels\"])", workflow,
            "preflight must derive the required labels from the kernel",
        )
        self.assertIn('for label in $required_labels', workflow)
        self.assertIn('echo "FACTORY_PREFLIGHT_REFUSED missing label $label"', workflow)
        self.assertIn('FACTORY_PREFLIGHT_REFUSED kernel label vocabulary is empty', workflow)
        self.assertNotIn("factory:accepted \\\n", workflow, "no second hand-typed label list")
        # Read the control labels straight from the policy file: load_config also validates the
        # work root, which is a POSIX path and fails on a Windows developer machine.
        import json
        from factory_kernel.triage import label_vocabulary
        policy = json.loads((ROOT / ".factory" / "kernel.json").read_text(encoding="utf-8"))
        vocabulary = label_vocabulary(policy["labels"])
        self.assertIn("priority:medium", vocabulary)
        self.assertIn("type:bug", vocabulary)
        self.assertIn("factory:stop", vocabulary)

    def test_worker_preflight_checks_actions_may_create_pull_requests(self) -> None:
        """The kernel opens the product PR with the Actions token. The first complete canary
        build (D-022) burned two hours before `gh pr create` said Actions may not create pull
        requests. The preflight now asks the setting: an explicit false refuses the run; a
        readable true passes; a token that cannot read the setting says so and continues,
        because a silent pass and a silent refusal are both worse than an honest unverified."""
        workflow = (ROOT / ".github" / "workflows" / "dark-factory-worker.yml").read_text(
            encoding="utf-8"
        )
        prerequisites = workflow.split("- name: Check operational prerequisites", 1)[1]
        prerequisites = prerequisites.split("- name: Setup Python", 1)[0]
        self.assertIn(
            'gh api "repos/$GITHUB_REPOSITORY/actions/permissions/workflow"', prerequisites
        )
        self.assertIn("--jq '.can_approve_pull_request_reviews'", prerequisites)
        self.assertRegex(
            prerequisites,
            r'false\)\n\s+echo "FACTORY_PREFLIGHT_REFUSED GitHub Actions may not create pull requests'
            r' \(Settings > Actions > General > Workflow permissions\)"\n\s+exit 1',
        )
        self.assertRegex(prerequisites, r'true\)\n\s+echo "FACTORY_PREFLIGHT_PR_PERMISSION_OK"')
        self.assertIn("FACTORY_PREFLIGHT_PR_PERMISSION_UNVERIFIED", prerequisites)
        # The unverified branch must not exit: it is the honest fallback for a token that
        # cannot read repository settings.
        unverified = prerequisites.split("FACTORY_PREFLIGHT_PR_PERMISSION_UNVERIFIED", 1)[1]
        unverified = unverified.split("esac", 1)[0]
        self.assertNotIn("exit 1", unverified)
        # Ordering: after the secret loop, before the route probes.
        self.assertLess(
            prerequisites.index('FACTORY_PREFLIGHT_REFUSED missing secret'),
            prerequisites.index("actions/permissions/workflow"),
        )
        self.assertLess(
            prerequisites.index("actions/permissions/workflow"),
            prerequisites.index("routing_model="),
        )

    def test_worker_model_route_is_the_request_the_sdk_makes(self) -> None:
        """The Anthropic SDK appends /v1/messages to ANTHROPIC_BASE_URL. With the versioned
        path as the base, the CLI hit /api/v1/v1/messages and got an HTML 404 for every model
        while a hard-coded curl probe passed (run 33876017910, D-010)."""
        workflow = (ROOT / ".github" / "workflows" / "dark-factory-worker.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(workflow.count("ANTHROPIC_BASE_URL: https://openrouter.ai/api\n"), 1,
                         "base URL must be defined exactly once, at job level")
        self.assertNotIn("openrouter.ai/api/v1", workflow, "a versioned base doubles /v1")
        self.assertIn('-X POST "${ANTHROPIC_BASE_URL}/v1/messages"', workflow,
                      "the curl probe must build its URL the way the SDK does")
        self.assertIn('ANTHROPIC_AUTH_TOKEN: ${{ secrets.OPENROUTER_API_KEY }}', workflow)

    def test_worker_preflight_proves_the_route_with_the_pinned_cli(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "dark-factory-worker.yml").read_text(
            encoding="utf-8"
        )
        probe = workflow.split("Prove the worker's model route with the pinned CLI", 1)
        self.assertEqual(len(probe), 2, "CLI route probe step missing")
        step = probe[1].split("- name:", 1)[0]
        for needle in (
            "claude --bare -p",
            '--permission-mode dontAsk --tools ""',
            "--strict-mcp-config --mcp-config '{\"mcpServers\":{}}' --disable-slash-commands",
            "--output-format json",
            'p["model"]',
            'p.get("architecture_model")',
            'd.get("is_error") is False',
            "FACTORY_PREFLIGHT_MODEL_ROUTE_OK model=$model",
        ):
            self.assertIn(needle, step, needle)
        self.assertRegex(
            step,
            r'echo "FACTORY_PREFLIGHT_REFUSED worker CLI cannot reach model \$model"\n(?:.*\n)?\s+exit 1',
            "a failed CLI probe must refuse the run, not warn",
        )
        install = workflow.index("Install pinned worker and browser CLIs")
        dispatch = workflow.index("Dispatch exactly one factory action")
        self.assertLess(install, workflow.index("Prove the worker's model route"))
        self.assertLess(workflow.index("Prove the worker's model route"), dispatch)

    def test_worker_provisions_disposable_validation_state(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "dark-factory-worker.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("postgres:16", workflow)
        self.assertIn("dark_factory_validation", workflow)
        self.assertIn("DARK_FACTORY_E2E_BOOTSTRAP=1", workflow)
        self.assertIn("secrets.OPENROUTER_API_KEY", workflow)
        self.assertIn("secrets.SUPADATA_API_KEY", workflow)
        for removed_secret in (
            "secrets.DATABASE_URL",
            "secrets.JWT_SECRET",
            "secrets.YOUTUBE_CHANNEL_ID",
            "secrets.DARK_FACTORY_E2E_EMAIL",
            "secrets.DARK_FACTORY_E2E_PASSWORD",
            # Model auth is no longer a distinct external credential: the CLI is pointed at
            # OpenRouter's Anthropic-compatible endpoint and authenticates with the OpenRouter
            # key it already needs, so a separate Anthropic secret would be an unused
            # requirement the preflight would still hard-fail on.
            "secrets.ANTHROPIC_API_KEY",
        ):
            self.assertNotIn(removed_secret, workflow)


if __name__ == "__main__":
    unittest.main()
