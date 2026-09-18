"""A stand-in for the Claude Code CLI used to test the compatibility spike harness itself.

It behaves the way the real CLI was observed to behave against the fake provider (2.1.259):
posts to ANTHROPIC_BASE_URL/v1/messages?beta=true with the x-api-key header, streams first,
executes a tool_use by posting a tool_result turn, retries once on 529, exits 1 on a 4xx error
envelope, follows a 307 (forwarding the credential) and prints a JSON result line. Knobs via
CLI_STUB_MODE let a test make it misbehave: `leak` prints the key, `no_tool_result` never
answers a tool_use, `wrong_path` posts to /v1/other, `get_probe` GETs /v1/models first,
`header_leak` sends the key in an extra header, `echo_prompt` returns the prompt as its result,
`probe_no_retry` sends a HEAD probe first (like the real CLI) and never retries a 529.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def post(url: str, payload: dict, key: str, *, follow: bool = True, extra_headers: dict | None = None):
    body = json.dumps(payload).encode("utf-8")
    headers = {"content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01", **(extra_headers or {})}
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=30)


def main() -> int:
    if "--version" in sys.argv:
        print("cli-stub 0.1"); return 0
    mode = os.environ.get("CLI_STUB_MODE", "")
    base = os.environ["ANTHROPIC_BASE_URL"].rstrip("/")
    key = os.environ["ANTHROPIC_API_KEY"]
    model = os.environ.get("ANTHROPIC_MODEL", "stub-model")
    prompt = sys.argv[sys.argv.index("-p") + 1]
    path = "/v1/other" if mode == "wrong_path" else "/v1/messages?beta=true"
    messages = [{"role": "user", "content": prompt}]
    tools = [{"name": "Bash", "description": "run", "input_schema": {"type": "object"}}]
    if mode == "leak":
        print(f"debug key={key}")
    if mode == "probe_no_retry":
        try:
            urllib.request.urlopen(urllib.request.Request(base + "/api/hello", method="HEAD"), timeout=10)
        except urllib.error.HTTPError:
            pass
    if mode == "get_probe":
        try:
            urllib.request.urlopen(urllib.request.Request(base + "/v1/models", headers={"x-api-key": key}), timeout=10)
        except urllib.error.HTTPError:
            pass
    extra = {"x-debug-token": key} if mode == "header_leak" else None
    for turn in range(4):
        payload = {"model": model, "max_tokens": 64, "stream": turn == 0, "messages": messages, "tools": tools}
        attempts = 0
        while True:
            attempts += 1
            try:
                response = post(base + path, payload, key, extra_headers=extra)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (301, 302, 307, 308) and exc.headers.get("Location"):
                    # The observed CLI follows a redirect off the channel and takes its credential along.
                    try:
                        response = post(exc.headers["Location"], payload, key)
                        break
                    except urllib.error.HTTPError:
                        payload["stream"] = False  # ...and then falls back to a non-streaming request on the channel
                        continue
                if exc.code in (529, 500, 503) and attempts < 2 and mode != "probe_no_retry":
                    continue
                if exc.code in (529, 500, 503) and mode == "probe_no_retry":
                    print(json.dumps({"type": "result", "is_error": False, "result": "gave up quietly"}))
                    return 0
                print(json.dumps({"type": "result", "is_error": True, "api_error_status": exc.code}))
                return 1
        raw = response.read()
        text_parts, tool_use = [], None
        if response.headers.get("Content-Type", "").startswith("text/event-stream"):
            for line in raw.decode("utf-8").splitlines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                if event.get("type") == "content_block_start" and event["content_block"]["type"] == "tool_use":
                    tool_use = dict(event["content_block"])
                elif event.get("type") == "content_block_delta":
                    delta = event["delta"]
                    if delta["type"] == "text_delta":
                        text_parts.append(delta["text"])
                    elif delta["type"] == "input_json_delta" and tool_use is not None:
                        tool_use["input"] = json.loads(delta["partial_json"])
        else:
            message = json.loads(raw.decode("utf-8"))
            for block in message["content"]:
                if block["type"] == "text":
                    text_parts.append(block["text"])
                elif block["type"] == "tool_use":
                    tool_use = block
        if tool_use is None:
            print(json.dumps({"type": "result", "is_error": False, "result": prompt if mode == "echo_prompt" else "".join(text_parts)}))
            return 0
        if mode == "no_tool_result":
            print(json.dumps({"type": "result", "is_error": False, "result": "ignored the tool"}))
            return 0
        messages.append({"role": "assistant", "content": [tool_use]})
        messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use["id"], "content": "stub ran it"}]})
    print(json.dumps({"type": "result", "is_error": True, "result": "too many turns"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
