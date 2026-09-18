"""Offline compatibility spike: can the installed Claude Code CLI work through a restricted channel?

SPECIFICATION 5 (provider gateway): before the gateway migration, run an offline spike with the
installed CLI and a local fake provider covering request routing, streaming and non-streaming
responses, cancellation, tool exchanges, retries, error envelopes and authentication redaction.
No upstream key and no paid call: the fake provider is a local HTTP server that speaks the
Anthropic Messages API shape the CLI expects, and the CLI is pointed at it with a throwaway key.
The gate FAILS EXPLICITLY (exit 1, `CLI_COMPATIBILITY_FAILED`) when the CLI cannot use the
channel; it establishes transport compatibility only, never provider accounting (SPEC 5).

Run:  python harness/cli_compatibility_spike.py --output <record.json> [--binary claude] [--timeout 90]

Every request the CLI makes reaches the fake provider and is recorded (method, path, headers
with credentials redacted, body digest, stream flag, tool_result presence). The record is the
evidence; the printed summary lines are for humans.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FAKE_KEY = "sk-ant-spike-0000000000000000000000000000000000000000"
REDACTED_HEADERS = {"authorization", "x-api-key", "cookie", "proxy-authorization"}
MODEL = "claude-sonnet-4-5"  # a name the CLI recognises; the fake provider answers for it


class Scenario:
    """One CLI invocation against the fake provider with a scripted response plan."""

    def __init__(self, name: str, plan: list[dict], *, prompt: str, expect: str, args: tuple[str, ...] = (), kill_after_first_chunk: bool = False):
        self.name, self.plan, self.prompt, self.expect, self.args = name, plan, prompt, expect, args
        self.kill_after_first_chunk = kill_after_first_chunk
        self.requests: list[dict] = []
        self.first_chunk_sent = threading.Event()


class ForeignOrigin(BaseHTTPRequestHandler):
    """A second listener standing in for another host. Anything arriving here is recorded: did the
    CLI follow a redirect off the channel, and did it forward the credential when it did?"""
    hits: list[dict] = []
    lock = threading.Lock()

    def log_message(self, *_):
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length) if length else b""
        with ForeignOrigin.lock:
            ForeignOrigin.hits.append({"path": self.path, "credential_forwarded": "x-api-key" in {k.lower() for k in self.headers.keys()}
                                       or "authorization" in {k.lower() for k in self.headers.keys()}})
        raw = json.dumps({"type": "error", "error": {"type": "not_found_error", "message": "foreign origin"}}).encode("utf-8")
        self.send_response(404); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)


class FakeProvider(BaseHTTPRequestHandler):
    scenario: Scenario | None = None
    lock = threading.Lock()

    def log_message(self, *_):  # keep the record, not stderr noise
        pass

    def _record(self, body: bytes) -> dict:
        headers = {}
        for key, value in self.headers.items():
            headers[key.lower()] = "<redacted>" if key.lower() in REDACTED_HEADERS else value
        parsed = None
        try:
            parsed = json.loads(body.decode("utf-8")) if body else None
        except ValueError:
            parsed = {"unparseable": True}
        entry = {
            "method": self.command, "path": self.path, "headers": headers, "body_sha256": hashlib.sha256(body).hexdigest(),
            "stream": bool(isinstance(parsed, dict) and parsed.get("stream")),
            "model": parsed.get("model") if isinstance(parsed, dict) else None,
            "tool_result": bool(isinstance(parsed, dict) and any(
                isinstance(m, dict) and isinstance(m.get("content"), list) and any(isinstance(c, dict) and c.get("type") == "tool_result" for c in m["content"])
                for m in parsed.get("messages", []))),
            "tools_offered": len(parsed.get("tools", [])) if isinstance(parsed, dict) else 0,
            "credential_in_body": FAKE_KEY in body.decode("utf-8", errors="replace"),
        }
        return entry

    def do_POST(self):  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        scenario = FakeProvider.scenario
        with FakeProvider.lock:
            entry = self._record(body)
            scenario.requests.append(entry)
            index = len(scenario.requests) - 1
        if self.path.split("?", 1)[0].rstrip("/") != "/v1/messages":
            self._json(404, {"type": "error", "error": {"type": "not_found_error", "message": "spike: only /v1/messages is routed"}})
            return
        step = scenario.plan[min(index, len(scenario.plan) - 1)]
        kind = step["kind"]
        if kind == "error":
            self._json(step["status"], {"type": "error", "error": {"type": step["error_type"], "message": step["message"]}},
                       extra=step.get("headers"))
        elif kind == "redirect":
            self.send_response(307)
            self.send_header("Location", step["location"])
            self.end_headers()
        elif kind == "text":
            if entry["stream"]:
                self._stream_text(step["text"], scenario)
            else:
                self._json(200, self._message([{"type": "text", "text": step["text"]}], "end_turn"))
        elif kind == "tool_use":
            block = {"type": "tool_use", "id": "toolu_spike_1", "name": step["tool"], "input": step["input"]}
            if entry["stream"]:
                self._stream_blocks([block], "tool_use", scenario)
            else:
                self._json(200, self._message([block], "tool_use"))
        else:
            self._json(500, {"type": "error", "error": {"type": "api_error", "message": "spike: unknown plan step"}})

    def _message(self, content, stop_reason):
        return {"id": "msg_spike", "type": "message", "role": "assistant", "model": MODEL, "content": content,
                "stop_reason": stop_reason, "stop_sequence": None,
                "usage": {"input_tokens": 12, "output_tokens": 7}}

    def _json(self, status, payload, extra=None):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def _sse(self, event, data):
        self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _stream_text(self, text, scenario):
        self._stream_blocks([{"type": "text", "text": text}], "end_turn", scenario)

    def _stream_blocks(self, blocks, stop_reason, scenario):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self._sse("message_start", {"type": "message_start", "message": self._message([], None)})
        try:
            for index, block in enumerate(blocks):
                if block["type"] == "text":
                    self._sse("content_block_start", {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}})
                    words = block["text"].split(" ")
                    for w, word in enumerate(words):
                        self._sse("content_block_delta", {"type": "content_block_delta", "index": index,
                                                          "delta": {"type": "text_delta", "text": word + (" " if w < len(words) - 1 else "")}})
                        scenario.first_chunk_sent.set()
                        if scenario.kill_after_first_chunk:
                            time.sleep(0.25)
                else:
                    self._sse("content_block_start", {"type": "content_block_start", "index": index,
                                                      "content_block": {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}})
                    self._sse("content_block_delta", {"type": "content_block_delta", "index": index,
                                                      "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}})
                    scenario.first_chunk_sent.set()
                self._sse("content_block_stop", {"type": "content_block_stop", "index": index})
            self._sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                                        "usage": {"output_tokens": 7}})
            self._sse("message_stop", {"type": "message_stop"})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            with FakeProvider.lock:
                scenario.requests[-1]["client_disconnected_mid_stream"] = True


def _kill_tree(proc: subprocess.Popen) -> None:
    """Cancellation as the CLI's caller would do it: the whole process tree (the npm shim spawns
    node on Windows, so killing the shell alone leaves the stream open)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.kill()


def run_scenario(server_port: int, scenario: Scenario, *, binary: str, timeout: int, workdir: Path) -> dict:
    FakeProvider.scenario = scenario
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_"))}
    env.update({
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server_port}",
        "ANTHROPIC_API_KEY": FAKE_KEY,
        "ANTHROPIC_MODEL": MODEL,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1", "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_MAX_RETRIES": "1",
        "HOME": env.get("HOME", str(workdir)),
    })
    launcher = [sys.executable, binary] if binary.endswith(".py") else [binary]
    argv = [*launcher, "--bare", "-p", scenario.prompt, "--model", MODEL, "--output-format", "json", *scenario.args]
    started = time.time()
    proc = subprocess.Popen(argv, cwd=workdir, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", shell=os.name == "nt" and not binary.endswith(".py"))
    killed = False
    if scenario.kill_after_first_chunk:
        scenario.first_chunk_sent.wait(timeout)
        time.sleep(0.1)
        _kill_tree(proc); killed = True
    try:
        out, err = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        proc.kill(); out, err = proc.communicate(); timed_out = True
    combined = (out or "") + (err or "")
    return {
        "name": scenario.name, "expect": scenario.expect, "argv": [a if a != scenario.prompt else "<prompt>" for a in argv],
        "exit_code": proc.returncode, "killed": killed, "timed_out": timed_out, "seconds": round(time.time() - started, 2),
        "requests": scenario.requests, "stdout_head": (out or "")[:4000], "stderr_head": (err or "")[:2000],
        "credential_in_output": FAKE_KEY in combined,
    }


def judge(results: list[dict]) -> list[str]:
    """Explicit gate: every scenario's observable outcome must match its expectation."""
    problems = []
    by = {r["name"]: r for r in results}

    def paths(r):
        return sorted({q["path"] for q in r["requests"]})

    for r in results:
        if r["credential_in_output"] or any(q["credential_in_body"] for q in r["requests"]):
            problems.append(f"{r['name']}: the credential leaked into output or a request body")
        if any(q["path"].split("?", 1)[0].rstrip("/") != "/v1/messages" for q in r["requests"]):
            problems.append(f"{r['name']}: the CLI called a path other than /v1/messages: {paths(r)}")
        if any("authorization" in q["headers"] and q["headers"]["authorization"] != "<redacted>" for q in r["requests"]):
            problems.append(f"{r['name']}: an unredacted authorization header was recorded")
    plain = by.get("plain")
    if not plain or not plain["requests"] or plain["exit_code"] != 0 or "ok-from-fake-provider" not in plain["stdout_head"]:
        problems.append("plain: the CLI did not complete a request through the restricted channel and echo the fake answer")
    stream = by.get("stream")
    if not stream or not any(q["stream"] for q in stream["requests"]):
        problems.append("stream: no streaming request was observed")
    tool = by.get("tool")
    if not tool or not any(q["tool_result"] for q in tool["requests"]):
        problems.append("tool: the CLI never posted a tool_result after the fake tool_use (tool exchange not observed)")
    retry = by.get("retry")
    if not retry or len(retry["requests"]) < 2 or retry["exit_code"] != 0:
        problems.append("retry: the CLI did not retry once after a 529 overloaded envelope and then succeed")
    error = by.get("error")
    if not error or error["exit_code"] == 0:
        problems.append("error: a 400 invalid_request_error envelope did not produce a nonzero exit")
    cancel = by.get("cancel")
    if not cancel or not cancel["killed"] or not any(q.get("client_disconnected_mid_stream") for q in cancel["requests"]):
        problems.append("cancel: the provider did not observe the client disconnect after a mid-stream cancellation")
    # Redirects are an OBSERVATION, not a gate: the gateway never emits one and refuses upstream
    # redirects itself (provider_gateway). What is recorded is whether the CLI would follow a 307
    # off the channel and whether it forwards the credential when it does; containment (WP06)
    # is what stops such egress, not this spike.
    return problems


def observations(results: list[dict], foreign_hits: list[dict]) -> dict:
    by = {r["name"]: r for r in results}
    redirect = by.get("redirect", {"requests": []})
    return {
        "cli_posts_to": sorted({q["path"] for r in results for q in r["requests"]}),
        "first_request_streams": bool(by.get("plain", {}).get("requests")) and by["plain"]["requests"][0]["stream"],
        "followed_cross_origin_redirect": bool(foreign_hits),
        "credential_forwarded_cross_origin": any(h["credential_forwarded"] for h in foreign_hits),
        "foreign_origin_hits": foreign_hits,
        "redirect_scenario_requests_on_channel": len(redirect["requests"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--binary", default="claude")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--workdir", type=Path, default=None)
    args = parser.parse_args()
    binary = args.binary if args.binary.endswith(".py") and Path(args.binary).is_file() else (shutil.which(args.binary) or args.binary)
    if not (binary.endswith(".py") or shutil.which(args.binary)):
        record = {"schema": "dark-factory/cli-compatibility-spike", "schema_version": "1.0", "status": "not_run",
                  "reason": f"CLI binary {args.binary!r} is not installed"}
        args.output.write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(f"CLI_COMPATIBILITY_FAILED reason=binary_missing binary={args.binary}")
        return 1
    version = subprocess.run(([sys.executable, binary] if binary.endswith(".py") else [binary]) + ["--version"], capture_output=True, text=True,
                             shell=os.name == "nt" and not binary.endswith(".py")).stdout.strip()
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeProvider)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    foreign = ThreadingHTTPServer(("127.0.0.1", 0), ForeignOrigin)
    foreign_port = foreign.server_address[1]
    threading.Thread(target=foreign.serve_forever, daemon=True).start()
    workdir = args.workdir or Path.cwd()
    scenarios = [
        Scenario("plain", [{"kind": "text", "text": "ok-from-fake-provider"}], prompt="Reply with exactly ok-from-fake-provider", expect="one request, exit 0, answer echoed"),
        Scenario("stream", [{"kind": "text", "text": "streamed ok from fake provider"}], prompt="Say hello", expect="a streaming request observed", args=("--output-format", "stream-json", "--verbose")),
        Scenario("tool", [{"kind": "tool_use", "tool": "Bash", "input": {"command": "echo spike-tool-ran"}}, {"kind": "text", "text": "tool done"}],
                 prompt="Run echo", expect="a second request carrying tool_result", args=("--allowedTools", "Bash")),
        Scenario("retry", [{"kind": "error", "status": 529, "error_type": "overloaded_error", "message": "spike overloaded"}, {"kind": "text", "text": "ok-after-retry"}],
                 prompt="Reply ok", expect="two requests, exit 0"),
        Scenario("error", [{"kind": "error", "status": 400, "error_type": "invalid_request_error", "message": "spike invalid request"}],
                 prompt="Reply ok", expect="nonzero exit with the error surfaced"),
        Scenario("redirect", [{"kind": "redirect", "location": f"http://127.0.0.1:{foreign_port}/v1/messages"}, {"kind": "text", "text": "after redirect"}],
                 prompt="Reply ok", expect="observation: does the CLI follow a 307 to another origin, and with the credential?"),
        Scenario("cancel", [{"kind": "text", "text": "this stream will be cut by the client before it ends " * 20}], prompt="Say a lot", expect="server sees the disconnect",
                 args=("--output-format", "stream-json", "--verbose"), kill_after_first_chunk=True),
    ]
    results = [run_scenario(port, s, binary=binary, timeout=args.timeout, workdir=workdir) for s in scenarios]
    server.shutdown(); foreign.shutdown()
    problems = judge(results)
    observed = observations(results, list(ForeignOrigin.hits))
    record = {"schema": "dark-factory/cli-compatibility-spike", "schema_version": "1.0", "binary": binary, "cli_version": version,
              "fake_provider": f"http://127.0.0.1:{port}", "model": MODEL, "status": "compatible" if not problems else "incompatible",
              "problems": problems, "observations": observed, "scenarios": results, "paid_calls": 0,
              "establishes": "transport compatibility through a restricted local channel only; not provider accounting, not containment"}
    args.output.write_text(json.dumps(record, indent=2), encoding="utf-8")
    for r in results:
        print(f"CLI_SPIKE scenario={r['name']} requests={len(r['requests'])} exit={r['exit_code']} killed={r['killed']} timed_out={r['timed_out']} seconds={r['seconds']}")
    print("CLI_SPIKE_OBSERVATIONS " + json.dumps({k: v for k, v in observed.items() if k != "foreign_origin_hits"}, sort_keys=True))
    if problems:
        for p in problems:
            print(f"CLI_SPIKE_PROBLEM {p}")
        print(f"CLI_COMPATIBILITY_FAILED problems={len(problems)} version={version!r}")
        return 1
    print(f"CLI_COMPATIBILITY_OK version={version!r} scenarios={len(results)} paid_calls=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
