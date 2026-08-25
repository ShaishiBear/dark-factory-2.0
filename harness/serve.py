#!/usr/bin/env python3
"""Start validation services, or fail with a diagnosable reason.

    python harness/serve.py --port 8123
    python harness/serve.py --port 8123 --with-frontend

The validation environment may come from an external env file or directly injected
process variables. In GitHub-hosted validation the disposable database/account bootstrap
is explicit via DARK_FACTORY_E2E_BOOTSTRAP=1; production/external validation remains
unchanged.

No validation process may silently degrade to "not testable". Missing env, a dead
frontend, a failed bootstrap, or a dead backend is a hard non-zero exit.
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
BACKEND = APP / "backend"
FRONTEND = APP / "frontend"

CORE_REQUIRED = ["DATABASE_URL", "OPENROUTER_API_KEY", "JWT_SECRET"]
BROWSER_REQUIRED = ["DARK_FACTORY_E2E_EMAIL", "DARK_FACTORY_E2E_PASSWORD"]
BOOTSTRAP_REQUIRED = ["SUPADATA_API_KEY"]


def load_env_file(path: Path) -> int:
    """Minimal KEY=VALUE loader, intentionally independent of app dependencies."""
    loaded = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)
        loaded += 1
    return loaded


def frontend_port_file(backend_port: int) -> Path:
    return Path(tempfile.gettempdir()) / f"dark-factory-frontend-{backend_port}.port"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _backend_python() -> Path:
    """Use the interpreter that `uv sync` actually populated for the backend."""
    rel = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    python = BACKEND / ".venv" / rel
    if not python.is_file():
        raise RuntimeError(
            f"backend virtualenv interpreter is missing: {python}; "
            "run `cd app/backend && uv sync --frozen --all-extras` first"
        )
    return python


def _wait_http(url: str, proc: subprocess.Popen[str], timeout: int = 60) -> None:
    deadline = time.time() + timeout
    last = "never answered"
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"process exited early rc={proc.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    return
                last = f"HTTP {response.status}"
        except (urllib.error.URLError, OSError) as exc:
            last = str(exc)
        time.sleep(0.2)
    raise RuntimeError(f"never became reachable: {last}")


def _terminate(proc: subprocess.Popen[str] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _bootstrap_validation(python: Path) -> None:
    proc = subprocess.run(
        [str(python), str(ROOT / "harness" / "bootstrap_e2e.py")],
        cwd=ROOT,
        env=dict(os.environ),
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=True,
        timeout=240,
    )
    if proc.returncode:
        raise RuntimeError(f"E2E validation bootstrap failed rc={proc.returncode}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--with-frontend", action="store_true")
    args = ap.parse_args()

    env_path = Path(os.environ.get("DARK_FACTORY_VALIDATION_ENV",
                                   "/opt/dark-factory/validation.env"))
    if env_path.is_file():
        print(f"loaded {load_env_file(env_path)} vars from {env_path}", flush=True)
    else:
        print(f"WARN: no validation env at {env_path}", file=sys.stderr, flush=True)

    bootstrap = os.environ.get("DARK_FACTORY_E2E_BOOTSTRAP") == "1"
    required = list(CORE_REQUIRED)
    if args.with_frontend:
        required.extend(BROWSER_REQUIRED)
    if bootstrap:
        required.extend(BOOTSTRAP_REQUIRED)
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print(f"APP_START_REFUSED missing={','.join(missing)}", file=sys.stderr, flush=True)
        print(
            f"Populate {env_path} (outside the repo) or inject the required environment. "
            "External validation must use a dedicated database/account; GitHub-hosted "
            "bootstrap is permitted only for its disposable loopback database.",
            file=sys.stderr,
            flush=True,
        )
        return 1

    backend: subprocess.Popen[str] | None = None
    frontend: subprocess.Popen[str] | None = None
    rendezvous = frontend_port_file(args.port)
    stopping = False

    def stop(_signum=None, _frame=None) -> None:
        nonlocal stopping
        stopping = True
        _terminate(backend)
        _terminate(frontend)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    try:
        backend_python = _backend_python()
        if bootstrap:
            _bootstrap_validation(backend_python)

        if args.with_frontend:
            frontend_port = _free_port()
            frontend_env = dict(os.environ)
            frontend_env["VITE_API_TARGET"] = f"http://127.0.0.1:{args.port}"
            frontend = subprocess.Popen(
                ["bun", "run", "dev", "--", "--host", "127.0.0.1",
                 "--port", str(frontend_port), "--strictPort"],
                cwd=FRONTEND, env=frontend_env, stdout=sys.stdout, stderr=sys.stderr,
                text=True,
            )
            _wait_http(f"http://127.0.0.1:{frontend_port}/", frontend, timeout=60)
            rendezvous.write_text(str(frontend_port), encoding="utf-8")
            print(f"FRONTEND_STARTED port={frontend_port}", flush=True)

        backend = subprocess.Popen(
            [str(backend_python), "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1",
             "--port", str(args.port)],
            cwd=APP, env=dict(os.environ), stdout=sys.stdout, stderr=sys.stderr, text=True,
        )

        while not stopping:
            backend_rc = backend.poll()
            frontend_rc = frontend.poll() if frontend is not None else None
            if backend_rc is not None:
                print(f"BACKEND_EXITED rc={backend_rc}", file=sys.stderr, flush=True)
                return backend_rc or 1
            if frontend is not None and frontend_rc is not None:
                print(f"FRONTEND_EXITED rc={frontend_rc}", file=sys.stderr, flush=True)
                return frontend_rc or 1
            time.sleep(0.25)
        return 0
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"APP_START_REFUSED {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        _terminate(backend)
        _terminate(frontend)
        try:
            rendezvous.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
