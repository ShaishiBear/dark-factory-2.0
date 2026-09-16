"""Required real-container smoke for the opt-in HC-01 workflow; no model calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from factory_kernel.feedback_lab import Limits, run_arm  # noqa: E402
from factory_kernel.feedback_sandbox import DockerSandbox, SandboxError  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    sandbox = DockerSandbox(args.image)
    identity = sandbox.preflight()
    assert sandbox.evaluate("def solve(x): return x + 1", [1, -1], seconds=15) == [2, 0]
    # Observe actual container boundaries from candidate code, not just generated argv.
    boundary = '''import os, socket
def solve(x):
    result = {'uid': os.getuid(), 'secret': os.environ.get('FEEDBACK_HOST_SECRET'),
              'docker_socket': os.path.exists('/var/run/docker.sock')}
    try:
        open('/escape', 'w').write('bad')
        result['root_write'] = True
    except OSError:
        result['root_write'] = False
    try:
        socket.create_connection(('1.1.1.1', 443), timeout=1)
        result['network'] = True
    except OSError:
        result['network'] = False
    return result
'''
    assert sandbox.evaluate(boundary, [None], seconds=15) == [{
        "uid": 65534, "secret": None, "docker_socket": False, "root_write": False, "network": False}]
    # Public checks go red, real repair goes green, independent final inputs rerun.
    task = {"id": "smoke", "group": "smoke", "split": "development", "instruction": "Increment an integer.",
            "public": [{"input": 1, "expected": 2}], "acceptance": [{"input": -5, "expected": -4}]}
    def worker(prompt, **caps):
        return {"code": "def solve(x): return x + 1" if 'public_feedback' in prompt else "def solve(x): return x",
                "cost_usd": 0}
    result = run_arm(task, "feedback", worker, sandbox, Limits(seconds=90), emit=lambda event: None)
    assert result["accepted"] and len(result["rounds"]) == 2, result
    for code, timeout in (("while True: pass", 2),
                          ("import os\nwhile True: os.write(1, b'x' * 4096)", 10),
                          ("def solve(x): raise ValueError('broken')", 10)):
        try:
            sandbox.evaluate(code, [0], seconds=timeout)
        except SandboxError:
            pass
        else:
            raise AssertionError("bad candidate unexpectedly produced a valid observation")
    began = time.monotonic()
    try:
        sandbox.evaluate("while True: pass", [0], seconds=15,
                         cancelled=lambda: time.monotonic() - began > 2)
    except SandboxError as exc:
        assert "cancelled" in str(exc), exc
    else:
        raise AssertionError("cancellation failed")
    assert time.monotonic() - began < 12
    remaining = sandbox._control(["ps", "-aq", "--filter", "name=factory-feedback-"]).strip()
    assert not remaining, "experiment containers survived cleanup"
    print("FEEDBACK_CONTAINER_VERIFIED " + json.dumps({"checks": 8, "image": identity}))


if __name__ == "__main__":
    main()
