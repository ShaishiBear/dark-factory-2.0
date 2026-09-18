"""The offline CLI compatibility spike (SPEC 5) judged against a stub CLI: the fake provider
records every request with the credential redacted, the gate passes a CLI that uses the
restricted channel the way the real one was observed to, and it fails explicitly on a
credential leak, a request off the channel, or a tool exchange that never returns. The real
CLI's recorded run is evidence, not a test; this is the detector that runs everywhere."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "harness" / "cli_compatibility_spike.py"
STUB = Path(__file__).resolve().parent / "cli_stub.py"


def run_spike(tmp: Path, *, mode: str = "") -> tuple[int, dict, str]:
    output = tmp / "record.json"
    env = {**os.environ, "CLI_STUB_MODE": mode}
    proc = subprocess.run([sys.executable, str(HARNESS), "--output", str(output), "--binary", str(STUB), "--timeout", "30",
                           "--workdir", str(tmp)], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=240)
    record = json.loads(output.read_text(encoding="utf-8"))
    return proc.returncode, record, proc.stdout + proc.stderr


class SpikeHarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="cli-spike-")))

    def test_a_channel_conformant_cli_passes_and_every_request_is_recorded_with_the_credential_redacted(self):
        code, record, text = run_spike(self.tmp)
        self.assertEqual((code, record["status"], record["problems"], record["paid_calls"]), (0, "compatible", [], 0), text)
        by = {s["name"]: s for s in record["scenarios"]}
        self.assertEqual([q["path"] for q in by["plain"]["requests"]], ["/v1/messages?beta=true"])
        self.assertTrue(by["plain"]["requests"][0]["stream"], "the first request streams")
        self.assertEqual(by["plain"]["requests"][0]["headers"]["x-api-key"], "<redacted>")
        self.assertTrue(all(not q["credential_in_body"] for s in record["scenarios"] for q in s["requests"]))
        self.assertEqual([q["tool_result"] for q in by["tool"]["requests"]], [False, True], "the tool exchange posts a second turn")
        self.assertEqual(len(by["retry"]["requests"]), 2)
        self.assertEqual(by["error"]["exit_code"], 1)
        self.assertTrue(by["cancel"]["killed"] and any(q.get("client_disconnected_mid_stream") for q in by["cancel"]["requests"]))
        # Redirects are an observation, recorded with whether the credential travelled off the channel.
        self.assertEqual((record["observations"]["followed_cross_origin_redirect"], record["observations"]["credential_forwarded_cross_origin"]), (True, True))
        self.assertIn("CLI_COMPATIBILITY_OK", text)
        self.assertNotIn("sk-ant-spike", text)

    def test_the_gate_fails_explicitly_on_a_leak_a_wrong_path_or_a_missing_tool_result(self):
        for mode, needle in (("leak", "credential leaked"), ("wrong_path", "a path other than /v1/messages"), ("no_tool_result", "never posted a tool_result")):
            with self.subTest(mode=mode):
                code, record, text = run_spike(self.tmp, mode=mode)
                self.assertEqual((code, record["status"]), (1, "incompatible"), text)
                self.assertTrue(any(needle in p for p in record["problems"]), record["problems"])
                self.assertIn("CLI_COMPATIBILITY_FAILED", text)

    def test_a_missing_binary_is_an_explicit_failure_not_a_pass(self):
        output = self.tmp / "record.json"
        proc = subprocess.run([sys.executable, str(HARNESS), "--output", str(output), "--binary", "no-such-cli-binary-xyz"],
                              capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "not_run")
        self.assertIn("CLI_COMPATIBILITY_FAILED reason=binary_missing", proc.stdout)


if __name__ == "__main__":
    unittest.main()
