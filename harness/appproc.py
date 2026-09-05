"""Reach the software under test. Three shapes cover almost everything.

    http     a server. Started on a dynamic port, polled until it answers.
    cli      a command. Invoked with args; stdout, stderr and exit code asserted.
    library  no process at all. The E2E imports it and calls it.

Chosen by `driver` in `harness.config.json`. Every one of them prints `APP_STARTED`,
because that marker means "the thing under test is reachable" - which is a different
claim for a server than for a library, and equally load-bearing for both.

WHY THIS IS SPLIT OUT. The first version of this file was HTTP-only: urllib, a health
path, a GET and a POST. That made the scaffold silently useless for a CLI, a library, a
batch job or a desktop app - the majority of software - while the surrounding
documentation claimed the plumbing was universal. The process management genuinely IS
universal. The way you talk to the thing is not.

The universal parts, kept in one place because getting them wrong is subtle:

  * a DYNAMIC port, so two laps cannot collide
  * WAIT for an answer rather than sleeping; a sleep is a race you chose to lose
  * FAIL HARD if it never comes up - never degrade to "not testable", which is how a
    crashed app becomes a green run
  * TEAR DOWN on every path including failure, or a leaked process holds the port and
    poisons the next lap
"""
from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_LOG_NAME = "app-process.log"


class AppDidNotStart(RuntimeError):
    pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def app_log_path(port: int) -> Path:
    """Where the child's combined stdout/stderr is written.

    Under the run's `ARTIFACTS_DIR` (the directory whose `e2e-evidence/` subdirectory the
    workflows upload) it is `app-process.log`; without one it is a temp file that survives
    only on the host. Never inside the repository: a log under the worktree would show up
    as an uncommitted change in the very tree being judged.
    """
    configured = os.environ.get("ARTIFACTS_DIR", "").strip()
    if configured:
        base = Path(configured).expanduser().resolve()
        if ROOT not in (base, *base.parents):
            base.mkdir(parents=True, exist_ok=True)
            return base / APP_LOG_NAME
    fd, name = tempfile.mkstemp(prefix=f"dark-factory-app-{port}-", suffix=".log")
    os.close(fd)
    return Path(name)


def _pump(stream, path: Path) -> None:
    """Copy a child's output to `path` line by line until EOF.

    Runs on a daemon thread so the pipe is always being read: a child whose pipe nobody
    drains blocks on `write` once the 64 KiB buffer fills, and everything it printed
    before that (the bootstrap marker, uvicorn's tracebacks, Vite's output) is lost when
    the harness only reads the pipe on the never-healthy path.
    """
    try:
        with path.open("a", encoding="utf-8", errors="replace") as handle:
            for line in stream:
                handle.write(line)
                handle.flush()
    finally:
        stream.close()


def read_log_tail(path: Path | None, lines: int = 60) -> str:
    """The last `lines` lines of a log file, or '' when there is no readable log."""
    if path is None:
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def _argv(cmd: str) -> list[str]:
    """Split, then resolve argv[0] through PATHEXT.

    Without the resolve, a Windows `.cmd` shim (npm, npx, yarn, pnpm) fails as
    "the system cannot find the file specified" - which reads as "not installed"
    for a tool that is on PATH and works in any terminal.
    """
    parts = shlex.split(cmd, posix=False)
    if parts:
        found = shutil.which(parts[0])
        if found:
            parts[0] = found
    return parts


# --------------------------------------------------------------------------- http
class HttpApp:
    """A server on a dynamic port, polled until healthy."""

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg.get("http", {})
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen | None = None
        # The child's combined stdout/stderr, drained from the moment it starts. `run_e2e`
        # reads it for the bootstrap marker and copies it into the evidence dump.
        self.app_log: Path | None = None
        self._drain: threading.Thread | None = None

    def __enter__(self) -> "HttpApp":
        cmd = self.cfg.get("start", "").replace("{port}", str(self.port))
        if not cmd:
            raise AppDidNotStart("driver=http but http.start is empty in harness.config.json")
        self.app_log = app_log_path(self.port)
        self.proc = subprocess.Popen(_argv(cmd), cwd=ROOT, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="replace")
        # Drain BEFORE waiting on health. The first version read the pipe only when health
        # never came; after APP_STARTED nothing read it, so the bootstrap marker, uvicorn's
        # streaming tracebacks and Vite's output were lost, and a full pipe would have
        # blocked the child (run 33960088633).
        self._drain = threading.Thread(
            target=_pump, args=(self.proc.stdout, self.app_log),
            name=f"app-log-{self.port}", daemon=True,
        )
        self._drain.start()
        self._await_health()
        print(f"APP_STARTED port={self.port} app_log={self.app_log}", flush=True)
        return self

    def __exit__(self, *exc) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self._drain is not None:
            self._drain.join(timeout=5)

    def log_tail(self, lines: int = 60) -> str:
        """The last `lines` lines the child printed so far."""
        if self._drain is not None and self.proc is not None and self.proc.poll() is not None:
            self._drain.join(timeout=2)
        return read_log_tail(self.app_log, lines)

    def _await_health(self) -> None:
        path = self.cfg.get("health_path", "/health")
        want = self.cfg.get("health_contains", "")
        deadline = time.time() + int(self.cfg.get("boot_timeout_s", 30))
        last = "never answered"
        while time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                raise AppDidNotStart(
                    f"the app exited with {self.proc.returncode} before answering "
                    f"(app_log={self.app_log}):\n{self.log_tail(40)[-1500:]}")
            try:
                status, body, _ = self.get(path)
                if status == 200 and (not want or want in body):
                    return
                last = f"status={status} body={body[:120]!r}"
            except (urllib.error.URLError, OSError) as e:
                last = f"not accepting connections yet ({e})"
            time.sleep(0.2)
        raise AppDidNotStart(
            f"never became healthy in time. Last: {last}. This is a FAILURE, not "
            f"'not testable'. app_log={self.app_log}:\n{self.log_tail(40)[-1500:]}")

    def get(self, path: str, follow: bool = False, headers: dict | None = None):
        """(status, body, headers). `follow=False` so a redirect stays VISIBLE -
        following them by default is how a test of a redirect stops testing it."""
        req = urllib.request.Request(self.base + path, headers=headers or {})

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **kw):
                return None

        opener = (urllib.request.build_opener()
                  if follow else urllib.request.build_opener(_NoRedirect))
        try:
            with opener.open(req, timeout=15) as r:
                return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)

    def post(self, path: str, body: str, headers: dict | None = None):
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=body.encode("utf-8"),
                                     headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)


# --------------------------------------------------------------------------- cli
class CliApp:
    """A command-line program. `app.run("--flag value")` returns (rc, stdout, stderr).

    The equivalent of a health check is a smoke invocation: if the binary cannot even
    print its own version, nothing below is worth running.
    """

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg.get("cli", {})

    def __enter__(self) -> "CliApp":
        invoke = self.cfg.get("invoke", "")
        if not invoke:
            raise AppDidNotStart("driver=cli but cli.invoke is empty in harness.config.json")
        rc, out, err = self.run(self.cfg.get("smoke_args", "--help"))
        want = self.cfg.get("smoke_contains", "")
        if rc not in (0, 1) or (want and want not in (out + err)):
            raise AppDidNotStart(
                f"the smoke invocation failed: rc={rc}\n{(out + err)[-1000:]}")
        print("APP_STARTED driver=cli", flush=True)
        return self

    def __exit__(self, *exc) -> None:
        return None

    def run(self, args: str = "", stdin: str = "", timeout: int = 60):
        cmd = self.cfg.get("invoke", "").replace("{args}", args)
        p = subprocess.run(_argv(cmd), cwd=ROOT, input=stdin or None,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return p.returncode, p.stdout or "", p.stderr or ""


# --------------------------------------------------------------------------- library
class LibraryApp:
    """No process. The E2E imports the thing and calls it.

    `APP_STARTED` here means it imports at all - which is the same claim as a server
    answering: whatever follows is being asserted against something that loaded.
    """

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg.get("library", {})

    def __enter__(self) -> "LibraryApp":
        check = self.cfg.get("import_check", "")
        if check:
            p = subprocess.run(_argv(check), cwd=ROOT, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
            if p.returncode != 0:
                raise AppDidNotStart(
                    f"the library does not import:\n{(p.stdout + p.stderr)[-1000:]}")
        sys.path.insert(0, str(ROOT))
        print("APP_STARTED driver=library", flush=True)
        return self

    def __exit__(self, *exc) -> None:
        return None


DRIVERS = {"http": HttpApp, "cli": CliApp, "library": LibraryApp}


def make_driver(cfg: dict):
    name = (cfg.get("driver") or "http").strip().lower()
    if name not in DRIVERS:
        raise AppDidNotStart(
            f"unknown driver {name!r} in harness.config.json - expected one of "
            f"{', '.join(sorted(DRIVERS))}")
    return DRIVERS[name](cfg)
