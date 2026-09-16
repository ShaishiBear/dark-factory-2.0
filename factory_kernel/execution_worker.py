"""Canonical kernel model calls consume the host's cumulative allowance."""
from .execution_client import ExecutionClient


class ExecutionWorker:
    def __init__(self, provider, github):
        self.provider, self.github = provider, github
        self.client = None

    def __getattr__(self, name):
        return getattr(self.provider, name)

    def run(self, request, *, transcript=None, before_retry=None):
        # Lazy so read-only kernel commands need no spending capability. There is no
        # local/absent-secret fallback when a kernel command reaches a real model call.
        if self.client is None:
            self.client = ExecutionClient.from_environment(self.github)
        return self.client.run(self.provider, request, transcript=transcript, before_retry=before_retry)
