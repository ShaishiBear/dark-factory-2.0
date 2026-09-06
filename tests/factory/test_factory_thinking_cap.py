"""The route is probed for a thinking budget, and a worker can carry one per role (D-059).

Six builds of issue #103 died or overran in `test_author` on z-ai/glm-5.3-flash over
OpenRouter's Anthropic-compatible route, every one at `--effort medium` once D-055 landed;
the sixth (run 34013852733) ran 2025 s, 15 turns, 81,231 events and 121,065 thinking tokens,
~22,500 of them in its last turn, while the other stages of the same build ran at 15-46 s per
turn. The effort probe read honoured=false, true, true, false on the same route from run to
run, so the level is not a bound there. The CLI's thinking budget, `MAX_THINKING_TOKENS` in
its environment (above zero a `budget_tokens`, raised to 1024 if lower; exactly zero
`thinking: disabled`), is the lever not yet tried.

Pinned here: the preflight probe's pure parts (the environment each of its three runs gets,
the honoured rule, the line); the policy table (a row for every role, every row `None`
today, refused caps); the provider's environment (the variable is exported only when the
role's cap is set, the configured override wins, the runner's own value never leaks);
the kernel.json override validated at load; the deterministic-subprocess environment
passing the variable through; and the workflow step.
"""

from __future__ import annotations

import json
import os
import re
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

import factory_effort_probe as effort_probe  # noqa: E402
import factory_thinking_cap_probe as probe  # noqa: E402
from test_factory_effort_and_stream_logs import _stream_with_thinking, bounded  # noqa: E402
from test_factory_stream_timeouts import _Runs, _ok  # noqa: E402

from factory_kernel import providers as providers_module  # noqa: E402
from factory_kernel.config import ProviderConfig, load_config  # noqa: E402
from factory_kernel.credential_env import scoped_environment  # noqa: E402
from factory_kernel.providers import ClaudeCliProvider  # noqa: E402
from factory_kernel.worker_policy import (  # noqa: E402
    ROLE_EFFORT,
    ROLE_THINKING_CAP,
    THINKING_CAP_DISABLED,
    THINKING_CAP_ENV,
    THINKING_CAP_MIN_BUDGET,
    WORKER_EFFORT,
    check_thinking_cap,
    thinking_cap,
    validate_thinking_cap_overrides,
)

KERNEL_JSON = ROOT / ".factory" / "kernel.json"
WORKER_WORKFLOW = ROOT / ".github" / "workflows" / "dark-factory-worker.yml"
PROBE_LINE = re.compile(
    r"^FACTORY_PREFLIGHT_THINKING_CAP_PROBE model=(?P<model>\S+) uncapped=(?P<uncapped>\d+) "
    r"cap1024=(?P<cap1024>\d+) cap0=(?P<cap0>\d+) honoured=(?P<honoured>true|false) "
    r"cap1024_honoured=(?P<budget>true|false) cap0_honoured=(?P<disable>true|false) "
    r"effort=(?P<effort>\S+) uncapped_events=\d+ cap1024_events=\d+ cap0_events=\d+"
    r"(?: error=(?P<error>\S+))?$"
)


def _measure(cap: int | None, thinking: int, *, returned: bool = True) -> probe.CapMeasurement:
    return probe.CapMeasurement(cap, thinking + 3, thinking, returned, "" if returned else "rc=1")


def _provider(overrides: dict | None = None) -> ClaudeCliProvider:
    return ClaudeCliProvider(
        ProviderConfig(
            provider_id="claude-cli",
            binary="claude",
            model="m",
            timeout_seconds=2700,
            transient_retries=0,
            idle_timeout_seconds=420,
            thinking_cap_overrides=dict(overrides or {}),
        )
    )


# --- the CLI's variable --------------------------------------------------------------------------


class VariableTests(unittest.TestCase):
    def test_the_name_and_the_two_values_the_cli_documents(self):
        """2.1.259's bundle: `MAX_THINKING_TOKENS` > 0 is `budget_tokens` (raised to 1024
        when lower), == 0 is `thinking: disabled`; the error text for a level that needs
        thinking says `unset MAX_THINKING_TOKENS=0`."""
        self.assertEqual(THINKING_CAP_ENV, "MAX_THINKING_TOKENS")
        self.assertEqual(THINKING_CAP_DISABLED, 0)
        self.assertEqual(THINKING_CAP_MIN_BUDGET, 1024)

    def test_a_cap_the_cli_would_honour_as_given(self):
        self.assertEqual(check_thinking_cap(0, "x"), 0)
        self.assertEqual(check_thinking_cap(1024, "x"), 1024)
        self.assertEqual(check_thinking_cap(16000, "x"), 16000)
        for bad in (512, 1, 1023, -1, -1024):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "at least 1024"):
                check_thinking_cap(bad, "x")
        for bad in (True, False, "1024", 1024.0, None, [1024]):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "must be an integer"):
                check_thinking_cap(bad, "x")


# --- the policy table ----------------------------------------------------------------------------


class PolicyTableTests(unittest.TestCase):
    def test_every_role_with_an_effort_level_has_a_row(self):
        self.assertEqual(set(ROLE_THINKING_CAP), set(ROLE_EFFORT))

    def test_every_row_is_none_today(self):
        """No behaviour change until the probe says the route honours the budget: nothing is
        exported for any role."""
        for role, cap in ROLE_THINKING_CAP.items():
            with self.subTest(role):
                self.assertIsNone(cap)
                self.assertIsNone(thinking_cap(role))

    def test_an_unknown_role_is_refused(self):
        with self.assertRaisesRegex(ValueError, "no thinking cap row for role"):
            thinking_cap("nope")

    def test_a_set_row_is_checked_when_read(self):
        with mock.patch.dict(ROLE_THINKING_CAP, {"test_author": 1024}):
            self.assertEqual(thinking_cap("test_author"), 1024)
        with mock.patch.dict(ROLE_THINKING_CAP, {"test_author": 0}):
            self.assertEqual(thinking_cap("test_author"), 0)
        with mock.patch.dict(ROLE_THINKING_CAP, {"test_author": 512}):
            with self.assertRaisesRegex(ValueError, "at least 1024"):
                thinking_cap("test_author")


# --- the provider's environment ------------------------------------------------------------------


class ProviderEnvironmentTests(unittest.TestCase):
    def test_no_cap_exports_nothing(self):
        env = _provider().environment_for(bounded("test_author"))
        self.assertNotIn(THINKING_CAP_ENV, env)
        self.assertIsNone(_provider().thinking_cap(bounded("test_author")))

    def test_the_runners_own_value_never_reaches_a_worker(self):
        """The variable is neither a provider credential nor on the exact list: the only way
        it reaches a worker is the policy table or the configured override."""
        with mock.patch.dict(os.environ, {THINKING_CAP_ENV: "9"}):
            self.assertNotIn(THINKING_CAP_ENV, ClaudeCliProvider._worker_env({}))
            self.assertNotIn(THINKING_CAP_ENV, _provider().environment_for(bounded()))
            env = _provider({"test_author": 1024}).environment_for(bounded())
        self.assertEqual(env[THINKING_CAP_ENV], "1024", "the override, not the runner's 9")

    def test_a_configured_override_is_exported_as_the_variable(self):
        for cap, expected in ((1024, "1024"), (0, "0"), (8192, "8192")):
            with self.subTest(cap=cap):
                provider = _provider({"test_author": cap})
                self.assertEqual(provider.thinking_cap(bounded()), cap)
                self.assertEqual(provider.environment_for(bounded())[THINKING_CAP_ENV], expected)

    def test_an_override_for_another_role_changes_nothing(self):
        env = _provider({"implement": 1024}).environment_for(bounded("test_author"))
        self.assertNotIn(THINKING_CAP_ENV, env)

    def test_a_set_policy_row_is_exported_and_the_override_wins_over_it(self):
        with mock.patch.dict(ROLE_THINKING_CAP, {"test_author": 4096}):
            self.assertEqual(_provider().environment_for(bounded())[THINKING_CAP_ENV], "4096")
            env = _provider({"test_author": 0}).environment_for(bounded())
            self.assertEqual(env[THINKING_CAP_ENV], "0")

    def test_the_request_environment_cannot_smuggle_it(self):
        """Request-local environment is whitelisted (`REQUEST_ENV`); the cap is policy."""
        env = _provider().environment_for(bounded(environment={THINKING_CAP_ENV: "1"}))
        self.assertNotIn(THINKING_CAP_ENV, env)

    def test_the_launched_process_gets_exactly_that_environment(self):
        """Through `run`: the reader is launched with `environment_for`'s result, so a set
        cap is on the CLI's environment and an unset one is absent from it."""
        for overrides, expected in (({}, None), ({"test_author": 1024}, "1024")):
            with self.subTest(overrides=overrides):
                runs = _Runs(_ok())
                with mock.patch.object(providers_module, "_stream_cli", runs):
                    _provider(overrides).run(bounded("test_author"))
                (call,) = runs.calls
                self.assertEqual(call["env"].get(THINKING_CAP_ENV), expected)

    def test_the_dataclass_default_is_no_override(self):
        config = ProviderConfig(provider_id="claude-cli", binary="c", model="m", timeout_seconds=60)
        self.assertEqual(dict(config.thinking_cap_overrides), {})


# --- the override table ---------------------------------------------------------------------------


class OverrideConfigTests(unittest.TestCase):
    def _load(self, mutate) -> ProviderConfig:
        raw = json.loads(KERNEL_JSON.read_text(encoding="utf-8"))
        mutate(raw["provider"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kernel.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with mock.patch.dict(os.environ, {"FACTORY_WORKDIR": tmp}):
                return load_config(path).provider

    def test_the_checked_in_policy_carries_an_empty_table(self):
        raw = json.loads(KERNEL_JSON.read_text(encoding="utf-8"))
        self.assertEqual(raw["provider"]["thinking_cap_overrides"], {})
        self.assertIn("MAX_THINKING_TOKENS", raw["provider"]["_thinking_cap_overrides"])
        self.assertEqual(dict(self._load(lambda p: None).thinking_cap_overrides), {})

    def test_absent_means_no_override(self):
        loaded = self._load(lambda p: p.pop("thinking_cap_overrides"))
        self.assertEqual(dict(loaded.thinking_cap_overrides), {})
        self.assertEqual(validate_thinking_cap_overrides(None), {})

    def test_a_table_is_parsed(self):
        loaded = self._load(
            lambda p: p.update(thinking_cap_overrides={"test_author": 1024, "holdout": 0})
        )
        self.assertEqual(dict(loaded.thinking_cap_overrides), {"test_author": 1024, "holdout": 0})

    def test_a_cap_the_cli_would_not_honour_as_given_is_refused(self):
        for bad in (512, 1, -1):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "at least 1024"):
                self._load(lambda p, bad=bad: p.update(thinking_cap_overrides={"test_author": bad}))
        for bad in ("1024", True, None, 1024.5):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "must be an integer"):
                self._load(lambda p, bad=bad: p.update(thinking_cap_overrides={"test_author": bad}))

    def test_a_role_the_policy_does_not_know_is_refused(self):
        with self.assertRaisesRegex(ValueError, "does not know"):
            self._load(lambda p: p.update(thinking_cap_overrides={"tester": 1024}))

    def test_a_table_that_is_not_an_object_is_refused(self):
        for bad in (1024, ["test_author", 1024], "test_author"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "must be an object"):
                self._load(lambda p, bad=bad: p.update(thinking_cap_overrides=bad))

    def test_the_message_names_the_key(self):
        with self.assertRaisesRegex(ValueError, r"provider\.thinking_cap_overrides\.test_author"):
            validate_thinking_cap_overrides({"test_author": 512})


# --- the deterministic-subprocess environment -----------------------------------------------------


class ScopedEnvironmentTests(unittest.TestCase):
    def test_the_variable_passes_through_the_credential_boundary(self):
        """It is not a credential: a kernel process that carries it hands it on, and a
        caller may add it as extra environment."""
        source = {"PATH": "/bin", THINKING_CAP_ENV: "1024", "GH_TOKEN": "secret"}
        child = scoped_environment(source=source)
        self.assertEqual(child[THINKING_CAP_ENV], "1024")
        self.assertNotIn("GH_TOKEN", child)
        added = scoped_environment({THINKING_CAP_ENV: "0"}, source={"PATH": "/bin"})
        self.assertEqual(added[THINKING_CAP_ENV], "0")


# --- the preflight probe --------------------------------------------------------------------------


class ProbeEnvironmentTests(unittest.TestCase):
    def test_the_three_runs_and_their_fields(self):
        self.assertEqual(probe.CAPS, (None, 1024, 0))
        self.assertEqual(
            [probe.cap_field(cap) for cap in probe.CAPS], ["uncapped", "cap1024", "cap0"]
        )
        self.assertEqual(probe.EFFORT, WORKER_EFFORT)

    def test_the_uncapped_run_inherits_nothing_and_a_capped_run_carries_its_cap(self):
        source = {"PATH": "/bin", "ANTHROPIC_AUTH_TOKEN": "t", THINKING_CAP_ENV: "9"}
        self.assertEqual(
            probe.probe_environment(None, source), {"PATH": "/bin", "ANTHROPIC_AUTH_TOKEN": "t"}
        )
        self.assertEqual(probe.probe_environment(1024, source)[THINKING_CAP_ENV], "1024")
        self.assertEqual(probe.probe_environment(0, source)[THINKING_CAP_ENV], "0")
        with mock.patch.dict(os.environ, {THINKING_CAP_ENV: "9"}):
            self.assertNotIn(THINKING_CAP_ENV, probe.probe_environment(None))
            self.assertEqual(probe.probe_environment(0)[THINKING_CAP_ENV], "0")

    def test_the_argv_is_the_effort_probes_at_the_builders_level(self):
        argv = effort_probe.probe_argv("claude", "z-ai/glm-5.3-flash", probe.EFFORT)
        self.assertEqual(argv[argv.index("--effort") + 1], "medium")
        self.assertEqual(argv[argv.index("--max-turns") + 1], "1")
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "1")
        self.assertEqual(argv[argv.index("-p") + 1], probe.PROMPT)


class HonouredRuleTests(unittest.TestCase):
    def test_within_the_cap_with_slack(self):
        self.assertTrue(probe.within_cap(1024, 1024))
        self.assertTrue(probe.within_cap(1536, 1024))
        self.assertFalse(probe.within_cap(1537, 1024))
        self.assertTrue(probe.within_cap(0, 0))
        self.assertFalse(probe.within_cap(1, 0), "disabled means no thinking at all")

    def test_clearly_below_the_uncapped_run(self):
        self.assertTrue(probe.clearly_below(1000, 4000))
        self.assertTrue(probe.clearly_below(0, 100))
        self.assertFalse(probe.clearly_below(1000, 1400), "ratio not met")
        self.assertFalse(probe.clearly_below(0, 99), "gap below MARGIN_TOKENS")
        self.assertFalse(probe.clearly_below(4000, 1000))

    def test_a_cap_is_honoured_only_when_both_hold(self):
        self.assertTrue(probe.cap_honoured(900, 1024, 4000))
        self.assertFalse(probe.cap_honoured(2000, 1024, 4000), "inside the margin, outside the cap")
        self.assertFalse(probe.cap_honoured(900, 1024, 1000), "inside the cap, no margin")
        self.assertFalse(probe.cap_honoured(4000, 1024, 4000), "the route ignored the variable")
        self.assertTrue(probe.cap_honoured(0, 0, 4000))
        self.assertFalse(probe.cap_honoured(50, 0, 4000))

    def test_an_errored_call_proves_nothing(self):
        self.assertTrue(probe.honoured(_measure(None, 4000), _measure(1024, 900)))
        self.assertFalse(probe.honoured(_measure(None, 4000, returned=False), _measure(1024, 900)))
        self.assertFalse(probe.honoured(_measure(None, 4000), _measure(1024, 900, returned=False)))
        with self.assertRaisesRegex(ValueError, "no cap"):
            probe.honoured(_measure(None, 4000), _measure(None, 900))


class _FakeRunner:
    """Answers each run with the stream scripted for its `MAX_THINKING_TOKENS`."""

    def __init__(self, streams: dict[str | None, tuple[str, int]]) -> None:
        self.streams = streams
        self.calls: list[dict] = []

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": list(argv), **kwargs})
        cap = kwargs["env"].get(THINKING_CAP_ENV)
        stdout, rc = self.streams[cap]
        return subprocess.CompletedProcess(argv, rc, stdout=stdout, stderr="")


class ProbeLineTests(unittest.TestCase):
    SOURCE = {"PATH": "/bin", "ANTHROPIC_AUTH_TOKEN": "t"}

    def test_the_line_when_the_route_honours_both_caps(self):
        runner = _FakeRunner(
            {
                None: (_stream_with_thinking(4000), 0),
                "1024": (_stream_with_thinking(900), 0),
                "0": (_stream_with_thinking(0), 0),
            }
        )
        line = probe.run_probe("z-ai/glm-5.3-flash", runner=runner, source=self.SOURCE)
        match = PROBE_LINE.match(line)
        self.assertIsNotNone(match, line)
        self.assertEqual(match.group("model"), "z-ai/glm-5.3-flash")
        self.assertEqual(
            (match.group("uncapped"), match.group("cap1024"), match.group("cap0")),
            ("4000", "900", "0"),
        )
        self.assertEqual(match.group("honoured"), "true")
        self.assertEqual((match.group("budget"), match.group("disable")), ("true", "true"))
        self.assertEqual(match.group("effort"), "medium")
        self.assertIsNone(match.group("error"))
        self.assertEqual(len(runner.calls), 3, "three one-turn calls, no more")
        self.assertEqual(
            [c["env"].get(THINKING_CAP_ENV) for c in runner.calls], [None, "1024", "0"]
        )
        for call in runner.calls:
            self.assertEqual(call["argv"][call["argv"].index("--effort") + 1], "medium")
            self.assertEqual(call["argv"][call["argv"].index("--max-turns") + 1], "1")
            self.assertEqual(call["env"]["ANTHROPIC_AUTH_TOKEN"], "t")

    def test_the_line_when_the_route_ignores_the_variable(self):
        runner = _FakeRunner(
            {
                None: (_stream_with_thinking(4000), 0),
                "1024": (_stream_with_thinking(3900), 0),
                "0": (_stream_with_thinking(4100), 0),
            }
        )
        match = PROBE_LINE.match(probe.run_probe("m", runner=runner, source=self.SOURCE))
        self.assertIsNotNone(match)
        self.assertEqual(match.group("honoured"), "false")
        self.assertEqual((match.group("budget"), match.group("disable")), ("false", "false"))

    def test_the_budget_may_be_honoured_while_disabling_is_not(self):
        """The two verdicts are separate data: a budget the route respects is usable even if
        a disabled-thinking request is refused or ignored."""
        runner = _FakeRunner(
            {
                None: (_stream_with_thinking(4000), 0),
                "1024": (_stream_with_thinking(700), 0),
                "0": (_stream_with_thinking(0, is_error=True), 1),
            }
        )
        match = PROBE_LINE.match(probe.run_probe("m", runner=runner, source=self.SOURCE))
        self.assertIsNotNone(match)
        self.assertEqual(match.group("honoured"), "false")
        self.assertEqual((match.group("budget"), match.group("disable")), ("true", "false"))
        self.assertTrue(match.group("error").startswith("cap0:"), match.group("error"))

    def test_a_process_that_did_not_start_is_false_with_the_error_named(self):
        def runner(argv, **kwargs):
            raise FileNotFoundError(argv[0])

        line = probe.run_probe("m", binary="nope", runner=runner, source=self.SOURCE)
        match = PROBE_LINE.match(line)
        self.assertIsNotNone(match, line)
        self.assertEqual(match.group("honoured"), "false")
        self.assertEqual(
            match.group("error"),
            "uncapped:FileNotFoundError,cap1024:FileNotFoundError,cap0:FileNotFoundError",
        )

    def test_a_timed_out_call_counts_what_it_showed_and_is_not_honoured(self):
        def runner(argv, **kwargs):
            if kwargs["env"].get(THINKING_CAP_ENV) == "1024":
                raise subprocess.TimeoutExpired(argv, 180, output=_stream_with_thinking(500))
            return subprocess.CompletedProcess(
                argv, 0, stdout=_stream_with_thinking(3000), stderr=""
            )

        match = PROBE_LINE.match(probe.run_probe("m", runner=runner, source=self.SOURCE))
        self.assertIsNotNone(match)
        self.assertEqual(match.group("cap1024"), "500")
        self.assertEqual(match.group("budget"), "false")
        self.assertIn("cap1024:timeout_after_180s", match.group("error"))

    def test_the_line_needs_one_run_per_cap(self):
        with self.assertRaisesRegex(ValueError, "one run per cap"):
            probe.probe_line("m", [_measure(None, 1), _measure(1024, 1)])

    def test_the_script_loads_the_kernel_from_beside_itself(self):
        text = (ROOT / "scripts" / "factory_thinking_cap_probe.py").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^ROOT = Path\.cwd\(\)\.resolve\(\)$")
        self.assertIn("sys.path.insert(0, str(HERE.parent))", text)


class WorkflowTests(unittest.TestCase):
    @unittest.skipUnless(WORKER_WORKFLOW.is_file(), "repo-shaped copy without the workflow")
    def test_the_workflow_probes_the_cap_after_the_effort_probe_and_never_refuses(self):
        text = WORKER_WORKFLOW.read_text(encoding="utf-8")
        route = text.index("Prove the worker's model route with the pinned CLI")
        cap = text.index("Probe whether the route honours a thinking budget")
        scope = text.index("Prove the worker's read scope with the pinned CLI")
        self.assertLess(route, cap)
        self.assertLess(cap, scope)
        route_step = text[route:cap]
        self.assertIn("scripts/factory_effort_probe.py", route_step)
        step = text[cap:scope]
        self.assertIn("scripts/factory_thinking_cap_probe.py --model", step)
        self.assertIn("ANTHROPIC_AUTH_TOKEN: ${{ secrets.OPENROUTER_API_KEY }}", step)
        self.assertIn(
            "FACTORY_PREFLIGHT_THINKING_CAP_PROBE model=$worker_model uncapped=0 cap1024=0 cap0=0 "
            "honoured=false error=probe-did-not-run",
            step,
        )
        self.assertNotIn("exit 1", step, "data, never a gate")
        self.assertNotIn("FACTORY_PREFLIGHT_REFUSED", step)


if __name__ == "__main__":
    unittest.main()
