"""A stand-in for the process under validation: an HTTP server on --port that answers /health
and, on GET /ask, calls the provider the way the app does (OPENROUTER_BASE_URL + the OpenAI
path, `Authorization: Bearer $OPENROUTER_API_KEY`) and echoes what came back. It runs as a
child of the launcher under test, so it only ever sees its own environment."""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._reply(200, {"status": "ok"})
            return
        if self.path.startswith("/ask"):
            base = os.environ.get("OPENROUTER_BASE_URL", "")
            key = os.environ.get("OPENROUTER_API_KEY", "")
            model = "wrong/model" if "wrong" in self.path else "test/model"
            req = urllib.request.Request(base + "/chat/completions", method="POST",
                                         data=json.dumps({"model": model, "messages": [{"role": "user", "content": "hi"}]}).encode(),
                                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    self._reply(200, {"upstream_status": resp.status, "body": json.loads(resp.read().decode()), "base": base,
                                      "key_looks_like_channel_token": not key.startswith("real-")})
            except urllib.error.HTTPError as exc:
                self._reply(200, {"upstream_status": exc.code, "body": json.loads(exc.read().decode()), "base": base,
                                  "key_looks_like_channel_token": not key.startswith("real-")})
            return
        self._reply(404, {"error": "no"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    print("stub app starting", flush=True)
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
