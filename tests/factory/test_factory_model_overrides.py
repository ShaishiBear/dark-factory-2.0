"""A role can run on its own model (D-061).

Six builds of issue #103 died in `test_author` on z-ai/glm-5.3-flash over OpenRouter's
Anthropic-compatible route, which honours neither `--effort` (D-055) nor `MAX_THINKING_TOKENS`
(D-059) for it, and that one role reasons 5-10x more per turn there than any other stage. The
remaining lever is the model per role, and the kernel had exactly one per-role model (the
architecture holdout's). Pinned here: the `provider.model_overrides` table, parsed, validated
and refused at load; the provider's resolution order (the request's own model, else the
override, else the architecture rule, else the worker model) and that the kernel's own
requests name no model, so that order is the whole story; the stage record, the timing row
and the FACTORY_STAGE line saying the resolved model, for a stage that returned and one that
died; `scripts/factory_models.py` listing every distinct model a run can use; and the worker
workflow's route probe reading that list.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
for entry in (str(ROOT), str(HERE), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import factory_models as models_script  # noqa: E402
from test_factory_effort_and_stream_logs import bounded  # noqa: E402
from test_factory_stream_timeouts import _ok, _Runs, _runtime, _timed_out  # noqa: E402

from factory_kernel import providers as providers_module  # noqa: E402
from factory_kernel.agents import AgentResult  # noqa: E402
from factory_kernel.config import ProviderConfig, load_config  # noqa: E402
from factory_kernel.providers import ClaudeCliProvider, ProviderStageError  # noqa: E402
from factory_kernel.runtime import STAGE_TIMINGS, KernelRuntime, RunPaths, stage_line  # noqa: E402
from factory_kernel.worker_policy import ROLE_MAX_TURNS, validate_model_overrides  # noqa: E402

KERNEL_JSON = ROOT / ".factory" / "kernel.json"
WORKER_WORKFLOW = ROOT / ".github" / "workflows" / "dark-factory-worker.yml"
MUTATION_RUNNER = ROOT / "harness" / "factory_mutations" / "run.py"
MODELS_SCRIPT = ROOT / "scripts" / "factory_models.py"
WORKER, ARCHITECTURE, OTHER = "worker/model", "architecture/model", "other/model"


def _provider(
    overrides: dict | None = None, *, model: str = WORKER, architecture: str = ARCHITECTURE
) -> ClaudeCliProvider:
    return ClaudeCliProvider(
        ProviderConfig(
            provider_id="claude-cli",
            binary="claude",
            model=model,
            architecture_model=architecture,
            timeout_seconds=2700,
            transient_retries=0,
            idle_timeout_seconds=420,
            model_overrides=dict(overrides or {}),
        )
    )


def _policy(directory: str | Path, mutate=None) -> Path:
    """The checked-in policy, optionally changed, written to `directory`."""
    raw = json.loads(KERNEL_JSON.read_text(encoding="utf-8"))
    if mutate is not None:
        mutate(raw["provider"])
    path = Path(directory) / "kernel.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _load(mutate=None) -> ProviderConfig:
    with tempfile.TemporaryDirectory() as tmp:
        path = _policy(tmp, mutate)
        with mock.patch.dict(os.environ, {"FACTORY_WORKDIR": tmp}):
            return load_config(path).provider


# --- the override table ---------------------------------------------------------------------------


class OverrideConfigTests(unittest.TestCase):
    def test_the_checked_in_policy_carries_an_empty_table(self):
        raw = json.loads(KERNEL_JSON.read_text(encoding="utf-8"))
        self.assertEqual(raw["provider"]["model_overrides"], {})
        self.assertIn("scripts/factory_models.py --list", raw["provider"]["_model_overrides"])
        self.assertEqual(dict(_load().model_overrides), {})

    def test_absent_means_no_override(self):
        self.assertEqual(dict(_load(lambda p: p.pop("model_overrides")).model_overrides), {})
        self.assertEqual(validate_model_overrides(None), {})

    def test_a_table_is_parsed_and_its_slugs_stripped(self):
        loaded = _load(
            lambda p: p.update(model_overrides={"test_author": " deepseek/x ", "holdout": "y"})
        )
        self.assertEqual(
            dict(loaded.model_overrides), {"test_author": "deepseek/x", "holdout": "y"}
        )

    def test_every_role_the_policy_knows_is_accepted(self):
        table = {role: f"m/{role}" for role in ROLE_MAX_TURNS}
        self.assertEqual(validate_model_overrides(table), table)
        self.assertEqual(
            dict(_load(lambda p: p.update(model_overrides=table)).model_overrides), table
        )

    def test_a_role_the_policy_does_not_know_is_refused(self):
        for bad in ("tester", "test-author", "Test_Author", ""):
            with self.subTest(role=bad), self.assertRaisesRegex(ValueError, "does not know"):
                _load(lambda p, bad=bad: p.update(model_overrides={bad: "m"}))
        with self.assertRaisesRegex(ValueError, "does not know"):
            validate_model_overrides({"tester": "m"})

    def test_an_empty_slug_is_refused(self):
        for bad in ("", "   ", "\n"):
            with self.subTest(slug=bad), self.assertRaisesRegex(ValueError, "non-empty model slug"):
                _load(lambda p, bad=bad: p.update(model_overrides={"test_author": bad}))

    def test_a_slug_that_is_not_a_string_is_refused(self):
        for bad in (1, None, True, ["m"], {"slug": "m"}, 1.5):
            with self.subTest(slug=bad), self.assertRaisesRegex(ValueError, "non-empty model slug"):
                _load(lambda p, bad=bad: p.update(model_overrides={"test_author": bad}))

    def test_a_table_that_is_not_an_object_is_refused(self):
        for bad in ("m", ["test_author", "m"], 1, True):
            with self.subTest(table=bad), self.assertRaisesRegex(ValueError, "must be an object"):
                _load(lambda p, bad=bad: p.update(model_overrides=bad))

    def test_the_message_names_the_key(self):
        with self.assertRaisesRegex(ValueError, r"provider\.model_overrides\.test_author"):
            validate_model_overrides({"test_author": ""})
        with self.assertRaisesRegex(ValueError, r"provider\.model_overrides names a role"):
            validate_model_overrides({"nope": "m"})

    def test_the_dataclass_default_is_no_override(self):
        config = ProviderConfig(provider_id="claude-cli", binary="c", model="m", timeout_seconds=60)
        self.assertEqual(dict(config.model_overrides), {})


# --- the resolution order ------------------------------------------------------------------------


class ResolutionTests(unittest.TestCase):
    def test_the_default_is_the_worker_model(self):
        provider = _provider()
        for role in ("test_author", "implement", "holdout", "triage"):
            with self.subTest(role):
                self.assertEqual(provider.model_for(bounded(role)), WORKER)

    def test_an_override_wins_over_the_default_for_its_role_only(self):
        provider = _provider({"test_author": OTHER})
        self.assertEqual(provider.model_for(bounded("test_author")), OTHER)
        self.assertEqual(provider.model_for(bounded("implement")), WORKER)
        self.assertEqual(provider.model_for(bounded("architecture-holdout")), ARCHITECTURE)

    def test_the_architecture_holdout_keeps_its_own_model_without_an_override(self):
        self.assertEqual(_provider().model_for(bounded("architecture-holdout")), ARCHITECTURE)
        self.assertEqual(
            _provider(architecture="").model_for(bounded("architecture-holdout")), WORKER
        )

    def test_an_override_wins_over_the_architecture_rule(self):
        provider = _provider({"architecture-holdout": OTHER})
        self.assertEqual(provider.model_for(bounded("architecture-holdout")), OTHER)

    def test_the_requests_own_model_wins_over_everything(self):
        provider = _provider({"test_author": OTHER, "architecture-holdout": OTHER})
        self.assertEqual(
            provider.model_for(bounded("test_author", model="explicit/m")), "explicit/m"
        )
        self.assertEqual(
            provider.model_for(bounded("architecture-holdout", model="explicit/m")), "explicit/m"
        )

    def test_the_launched_argv_and_the_result_name_the_resolved_model(self):
        for overrides, expected in (({}, WORKER), ({"test_author": OTHER}, OTHER)):
            with self.subTest(overrides=overrides):
                runs = _Runs(_ok())
                with mock.patch.object(providers_module, "_stream_cli", runs):
                    result = _provider(overrides).run(bounded("test_author"))
                (call,) = runs.calls
                argv = call["argv"]
                self.assertEqual(argv[argv.index("--model") + 1], expected)
                self.assertEqual(result.model, expected)

    def test_the_kernels_own_requests_name_no_model(self):
        """Every request the kernel builds used to carry `model=self.config.provider.model`,
        which under the order above would have beaten every override and made the table
        dead; the provider's resolution is the only place a stage's model is chosen."""
        for name in ("runtime.py", "triage.py", "worker_runtime.py"):
            with self.subTest(name):
                source = (ROOT / "factory_kernel" / name).read_text(encoding="utf-8")
                self.assertNotIn("model=self.config.provider.model", source)
        self.assertIn(
            "if request.model:",
            (ROOT / "factory_kernel" / "providers.py").read_text(encoding="utf-8"),
        )


# --- the record and the stage line ---------------------------------------------------------------


class StageRecordTests(unittest.TestCase):
    def _stage(self, provider, run, *, role: str = "test_author", expect_failure: bool = False):
        with tempfile.TemporaryDirectory() as tmp:
            paths = RunPaths.create(Path(tmp), "run")
            rt = _runtime(Path(tmp), provider)
            out = io.StringIO()
            with (
                mock.patch.object(providers_module, "_stream_cli", _Runs(run, run)),
                mock.patch.object(providers_module, "_sleep", lambda s: None),
                contextlib.redirect_stdout(out),
            ):
                if expect_failure:
                    with self.assertRaises(ProviderStageError):
                        rt._agent_stage(paths, bounded(role))
                else:
                    rt._agent_stage(paths, bounded(role))
            record = json.loads(
                (paths.transcripts / f"agent-{role}.json").read_text(encoding="utf-8")
            )
            rows = [
                json.loads(row)
                for row in (paths.transcripts / STAGE_TIMINGS)
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            line = next(t for t in out.getvalue().splitlines() if t.startswith("FACTORY_STAGE "))
            return record, rows[0], line

    def test_a_returned_stage_records_the_model_it_ran_on(self):
        record, row, line = self._stage(_provider({"test_author": OTHER}), _ok())
        self.assertEqual(record["model"], OTHER)
        self.assertEqual(row["model"], OTHER)
        self.assertTrue(line.endswith(f" effort=medium model={OTHER}"), line)
        record, row, line = self._stage(_provider({"test_author": OTHER}), _ok(), role="holdout")
        self.assertEqual((record["model"], row["model"]), (WORKER, WORKER))
        self.assertTrue(line.endswith(f" effort=high model={WORKER}"), line)

    def test_a_stage_that_died_records_the_model_it_ran_on(self):
        record, row, line = self._stage(
            _provider({"test_author": OTHER}), _timed_out(turns=4), expect_failure=True
        )
        self.assertEqual(record["outcome"], "failed")
        self.assertTrue(record["timed_out"])
        self.assertEqual(record["model"], OTHER)
        self.assertEqual(row["model"], OTHER)
        self.assertIn(" outcome=failed events=", line)
        self.assertTrue(line.endswith(f" model={OTHER}"), line)

    def test_a_provider_without_a_resolver_records_the_requests_model_or_the_workers(self):
        """The rehearsal provider has no `model_for`: a failed stage's record then says the
        request's model, else the configured worker model, exactly what every kernel
        request used to say."""

        class _Plain:
            def run(self, request, **_kwargs):
                return AgentResult(provider_id="p", model="m", content="t")

        rt = object.__new__(KernelRuntime)
        rt.provider = _Plain()
        rt.config = mock.Mock()
        rt.config.provider.model = WORKER
        self.assertEqual(rt._resolved_model(bounded(model="x/y")), "x/y")
        self.assertEqual(rt._resolved_model(bounded()), WORKER)
        rt.provider = _provider({"test_author": OTHER})
        self.assertEqual(rt._resolved_model(bounded()), OTHER)
        self.assertEqual(rt._resolved_model(bounded(model="x/y")), "x/y")

    def test_the_line_shape(self):
        row = {
            "kind": "agent",
            "name": "test_author",
            "seconds": 301.2,
            "num_turns": 19,
            "outcome": "ok",
            "events_seen": 77,
            "thinking_tokens": 5,
            "effort": "medium",
            "model": "z-ai/glm-5.3-flash",
        }
        self.assertEqual(
            stage_line(row),
            "FACTORY_STAGE kind=agent name=test_author seconds=301.2 turns=19 outcome=ok "
            "events=77 thinking=5 effort=medium model=z-ai/glm-5.3-flash",
        )
        self.assertNotIn("model=", stage_line({**row, "model": None}))
        self.assertNotIn("model=", stage_line({**row, "model": ""}))


# --- the script ----------------------------------------------------------------------------------


class ScriptTests(unittest.TestCase):
    def test_the_checked_in_policy_lists_the_worker_and_architecture_models(self):
        raw = json.loads(KERNEL_JSON.read_text(encoding="utf-8"))["provider"]
        expected = [raw["model"], raw["architecture_model"]]
        for slug in raw.get("model_overrides", {}).values():
            if slug not in expected:
                expected.append(slug)
        self.assertEqual(models_script.configured_models(KERNEL_JSON), expected)

    def test_every_override_value_is_listed_once_after_the_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _policy(
                tmp,
                lambda p: p.update(
                    model="w",
                    architecture_model="a",
                    model_overrides={
                        "test_author": "x",
                        "implement": "x",
                        "repair": "a",
                        "holdout": "y",
                    },
                ),
            )
            self.assertEqual(models_script.configured_models(path), ["w", "a", "x", "y"])

    def test_an_override_equal_to_the_worker_model_adds_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _policy(
                tmp,
                lambda p: p.update(
                    model="w", architecture_model="a", model_overrides={"test_author": "w"}
                ),
            )
            self.assertEqual(models_script.configured_models(path), ["w", "a"])

    def test_no_architecture_model_lists_the_worker_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _policy(
                tmp, lambda p: p.update(model="w", architecture_model="", model_overrides={})
            )
            self.assertEqual(models_script.configured_models(path), ["w"])
            path = _policy(
                tmp,
                lambda p: (p.update(model="w", model_overrides={}), p.pop("architecture_model")),
            )
            self.assertEqual(models_script.configured_models(path), ["w"])

    def test_a_table_the_kernel_would_refuse_refuses_the_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad, message in (
                ({"tester": "m"}, "does not know"),
                ({"test_author": ""}, "non-empty model slug"),
                ("m", "must be an object"),
            ):
                with self.subTest(bad=bad):
                    path = _policy(tmp, lambda p, bad=bad: p.update(model_overrides=bad))
                    with self.assertRaisesRegex(ValueError, message):
                        models_script.configured_models(path)
                    err = io.StringIO()
                    with contextlib.redirect_stderr(err):
                        rc = models_script.main(["--list", "--policy", str(path)])
                    self.assertEqual(rc, 1)
                    self.assertTrue(
                        err.getvalue().startswith("FACTORY_MODELS_REFUSED "), err.getvalue()
                    )
            path = _policy(tmp, lambda p: p.update(model=""))
            with self.assertRaisesRegex(ValueError, "provider.model must be a non-empty string"):
                models_script.configured_models(path)

    def test_list_prints_one_slug_per_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _policy(
                tmp,
                lambda p: p.update(
                    model="w", architecture_model="a", model_overrides={"test_author": "x"}
                ),
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = models_script.main(["--list", "--policy", str(path)])
            self.assertEqual(rc, 0)
            self.assertEqual(out.getvalue(), "w\na\nx\n")

    def test_role_prints_what_the_provider_would_resolve(self):
        """The script's restatement of the rule agrees with `ClaudeCliProvider.model_for` for
        every role the policy knows, with and without an override."""
        with tempfile.TemporaryDirectory() as tmp:
            for overrides in ({}, {"test_author": "x", "architecture-holdout": "y", "triage": "w"}):
                path = _policy(
                    tmp,
                    lambda p, o=overrides: p.update(
                        model="w", architecture_model="a", model_overrides=o
                    ),
                )
                provider = _provider(overrides, model="w", architecture="a")
                for role in ROLE_MAX_TURNS:
                    with self.subTest(role=role, overrides=overrides):
                        self.assertEqual(
                            models_script.model_for_role(role, path),
                            provider.model_for(bounded(role)),
                        )
                        out = io.StringIO()
                        with contextlib.redirect_stdout(out):
                            rc = models_script.main(["--role", role, "--policy", str(path)])
                        self.assertEqual(rc, 0)
                        self.assertEqual(out.getvalue().strip(), provider.model_for(bounded(role)))
            with self.assertRaisesRegex(ValueError, "does not know"):
                models_script.model_for_role("tester", path)

    def test_the_script_loads_the_kernel_from_beside_itself(self):
        text = MODELS_SCRIPT.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^ROOT = Path\.cwd\(\)\.resolve\(\)$")
        self.assertIn("sys.path.insert(0, str(HERE.parent))", text)
        self.assertLess(
            text.index("sys.path.insert(0, str(HERE.parent))"), text.index("from factory_kernel")
        )

    def test_the_script_runs_from_outside_the_repo(self):
        """As the workflow runs it: a fresh process, no PYTHONPATH, the policy named."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _policy(
                tmp,
                lambda p: p.update(
                    model="w", architecture_model="a", model_overrides={"repair": "x"}
                ),
            )
            env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
            proc = subprocess.run(
                [sys.executable, str(MODELS_SCRIPT), "--list", "--policy", str(path)],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.split(), ["w", "a", "x"])


# --- the workflow and the mutation runner --------------------------------------------------------


class WorkflowTests(unittest.TestCase):
    @unittest.skipUnless(WORKER_WORKFLOW.is_file(), "repo-shaped copy without the workflow")
    def test_the_route_probe_runs_every_model_the_script_lists(self):
        text = WORKER_WORKFLOW.read_text(encoding="utf-8")
        step = text.split("Prove the worker's model route with the pinned CLI", 1)[1].split(
            "\n      - name:", 1
        )[0]
        self.assertIn('route_models="$(python3 scripts/factory_models.py --list)"', step)
        self.assertIn("for model in $route_models; do", step)
        self.assertLess(
            step.index("scripts/factory_models.py --list"), step.index("for model in $route_models")
        )
        self.assertLess(
            step.index("for model in $route_models"),
            step.index("FACTORY_PREFLIGHT_MODEL_ROUTE_OK model=$model"),
        )
        # An empty list is a refusal, not a loop that runs zero times and passes.
        self.assertIn('test -n "$route_models" || {', step)
        self.assertIn("FACTORY_PREFLIGHT_REFUSED scripts/factory_models.py listed no model", step)
        self.assertIn("FACTORY_PREFLIGHT_REFUSED worker CLI cannot reach model $model", step)
        self.assertIn("exit 1", step)
        # The inline two-model list is gone: the script is the one reader of the policy.
        self.assertNotIn('p.get("architecture_model")', step)

    @unittest.skipUnless(MUTATION_RUNNER.is_file(), "repo-shaped copy without the runner")
    def test_the_mutation_runner_copies_the_script_and_this_detector(self):
        text = MUTATION_RUNNER.read_text(encoding="utf-8")
        self.assertIn('"scripts/factory_models.py",', text)
        self.assertIn('"tests/factory/test_factory_model_overrides.py",', text)


if __name__ == "__main__":
    unittest.main()
