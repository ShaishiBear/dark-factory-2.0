"""Disposable, networkless execution of Python function experiments.

Only code and JSON arguments enter the container; the host compares results.
No host mounts, credentials, expected answers, repository or Docker socket enter it.
This is a narrow experiment executor, not the production qualification authority.
"""
from __future__ import annotations

import json
import queue
import re
import subprocess
import threading
import time
import uuid

from .benchmark import canonical

LIMIT = 64_000
BOOTSTRAP = """import json, sys
p = json.load(sys.stdin)
namespace = {}
exec(compile(p['code'], 'solution.py', 'exec'), namespace)
values = [namespace['solve'](x) for x in p['inputs']]
print(json.dumps({'values': values}, allow_nan=False, separators=(',', ':')))
"""


class SandboxError(RuntimeError):
    pass


class CandidateError(SandboxError):
    """The sandbox ran but the candidate did not return valid observations."""


class DockerSandbox:
    def __init__(self, image: str, binary: str = "docker"):
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
            raise ValueError("use a local immutable Docker image ID, not a tag")
        self.image, self.binary = image, binary

    def _control(self, args, timeout=15):
        try:
            result = subprocess.run([self.binary, *args], capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SandboxError(f"Docker control failed: {type(exc).__name__}") from exc
        if result.returncode:
            raise SandboxError("Docker control refused: " + result.stderr.decode(errors="replace")[-1500:])
        return result.stdout

    def preflight(self):
        facts = json.loads(self._control(["image", "inspect", self.image]))[0]
        # VOLUME creates implicit writable mounts and an entrypoint could change the runner.
        if (facts.get("Id") != self.image or facts.get("Os") != "linux"
                or facts.get("Config", {}).get("Volumes")):
            raise SandboxError("image identity/platform/volume policy refused")
        return {"image_id": self.image, "os": facts["Os"], "architecture": facts["Architecture"]}

    def create_args(self, name):
        return ["create", "--pull", "never", "--name", name, "--interactive",
                "--network", "none", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges=true", "--user", "65534:65534",
                "--memory", "128m", "--memory-swap", "128m", "--cpus", "1",
                "--pids-limit", "32", "--ulimit", "nofile=64:64",
                "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
                "--workdir", "/tmp", "--env", "HOME=/tmp", "--log-driver", "none",
                "--entrypoint", "python", self.image, "-I", "-S", "-c", BOOTSTRAP]

    def evaluate(self, code: str, inputs: list, *, seconds: float, cancelled=lambda: False):
        if not isinstance(code, str) or not code.strip() or len(code.encode()) > 24_000:
            raise ValueError("code must be nonempty and at most 24000 bytes")
        payload = canonical({"code": code, "inputs": inputs})
        if len(payload) > LIMIT or not 0 < seconds <= 120:
            raise ValueError("invalid sandbox request bounds")
        name = "factory-feedback-" + uuid.uuid4().hex
        deadline = time.monotonic() + seconds
        process = None
        try:
            if cancelled():
                raise SandboxError("cancelled")
            self._control(self.create_args(name), timeout=max(.1, deadline - time.monotonic()))
            process = subprocess.Popen([self.binary, "start", "--attach", "--interactive", name],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT)
            chunks = queue.Queue(maxsize=20)
            stop = threading.Event()

            def read():
                try:
                    while not stop.is_set():
                        chunk = process.stdout.read1(4096)
                        while not stop.is_set():
                            try:
                                chunks.put(chunk, timeout=.1)
                                break
                            except queue.Full:
                                pass
                        if not chunk:
                            break
                except (OSError, ValueError):
                    pass

            def write():
                try:
                    process.stdin.write(payload)
                    process.stdin.close()
                except (OSError, ValueError):
                    pass

            reader = threading.Thread(target=read, daemon=True)
            writer = threading.Thread(target=write, daemon=True)
            reader.start()
            writer.start()
            output = bytearray()
            try:
                while True:
                    if cancelled():
                        raise SandboxError("cancelled")
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise SandboxError("sandbox deadline exhausted")
                    try:
                        chunk = chunks.get(timeout=min(.1, left))
                    except queue.Empty:
                        continue
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > LIMIT:
                        raise SandboxError("sandbox output limit exceeded")
                process.wait(timeout=max(.1, deadline - time.monotonic()))
                state = json.loads(self._control(["inspect", "--format", "{{json .State}}", name]))
                if process.returncode or state["ExitCode"] or state["OOMKilled"] or state["Running"]:
                    raise CandidateError("candidate execution failed: " + output.decode(errors="replace")[-1500:])
                value = json.loads(output)
                if (not isinstance(value, dict) or set(value) != {"values"}
                        or not isinstance(value["values"], list) or len(value["values"]) != len(inputs)):
                    raise CandidateError("candidate returned malformed observations")
                # Reject NaN/Infinity even though Python's default JSON parser accepts them.
                json.dumps(value, allow_nan=False)
                return value["values"]
            finally:
                stop.set()
        except (json.JSONDecodeError, ValueError, subprocess.TimeoutExpired) as exc:
            raise CandidateError(f"invalid/incomplete candidate observation: {type(exc).__name__}") from exc
        finally:
            # Killing the attached CLI alone would leave candidate processes running.
            try:
                self._control(["rm", "--force", name])
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)
                    for stream in (process.stdin, process.stdout):
                        try:
                            stream.close()
                        except (OSError, ValueError):
                            pass
