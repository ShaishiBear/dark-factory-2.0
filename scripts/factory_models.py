#!/usr/bin/env python3
"""Every model a factory run can route a stage to, read from the tree under test's policy.

The kernel resolves a stage's model per request: the request's own explicit choice (the
kernel's own requests name none), else `provider.model_overrides[role]`, else
`provider.architecture_model` for the architecture holdout, else `provider.model`
(`ClaudeCliProvider.model_for`, D-061). So the models a run can use are the worker model,
the architecture holdout's, and every override value; this script lists them, deduplicated
in that order, one slug per line:

    python3 scripts/factory_models.py --list

The worker workflow's preflight runs the pinned CLI once against each and refuses the run
if any is unreachable (`FACTORY_PREFLIGHT_MODEL_ROUTE_OK model=<slug>` per model). Before
D-061 the step listed the first two with an inline one-liner; a role routed to a third
model would have found its route closed mid-build, after the stages before it had spent
their budgets. The overrides are validated exactly as the kernel validates them at load
(`worker_policy.validate_model_overrides`), so a typo refuses the preflight rather than
passing a slug the kernel would refuse later. `--role <role>` prints the one model that
role resolves to, for a probe that must make the request a role actually makes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The tree under test is the working directory (D-036): its `.factory/kernel.json` is the
# policy read. The code is loaded from beside this file.
ROOT = Path.cwd().resolve()
sys.path.insert(0, str(HERE.parent))

from factory_kernel.worker_policy import ROLE_MAX_TURNS, validate_model_overrides  # noqa: E402

KERNEL_JSON = ROOT / ".factory" / "kernel.json"
ARCHITECTURE_HOLDOUT = "architecture-holdout"
REFUSED_PREFIX = "FACTORY_MODELS_REFUSED"


def _slug(raw: object, name: str, *, required: bool) -> str:
    """A model slug from the policy: a non-empty string, or, for a field that may be
    absent, the empty string for `None`/empty."""
    if raw is None and not required:
        return ""
    if not isinstance(raw, str) or (required and not raw.strip()):
        raise ValueError(f"{name} must be a non-empty string; got {raw!r}")
    return raw.strip()


def read_provider(policy: Path = KERNEL_JSON) -> dict[str, object]:
    """The routes the policy's `provider` names: `model`, `architecture_model` (empty when
    unset) and `model_overrides` (validated as the kernel validates them at load)."""
    try:
        raw = json.loads(policy.read_text(encoding="utf-8"))
        provider = raw["provider"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"cannot read provider from {policy}: {exc}") from exc
    if not isinstance(provider, dict):
        raise ValueError(f"provider in {policy} must be an object")
    return {
        "model": _slug(provider.get("model"), "provider.model", required=True),
        "architecture_model": _slug(
            provider.get("architecture_model"), "provider.architecture_model", required=False
        ),
        "model_overrides": validate_model_overrides(provider.get("model_overrides")),
    }


def configured_models(policy: Path = KERNEL_JSON) -> list[str]:
    """The worker model, the architecture holdout's, then every override value in policy
    order, each once: every distinct model a run can launch a stage on."""
    provider = read_provider(policy)
    overrides: dict[str, str] = provider["model_overrides"]  # type: ignore[assignment]
    ordered = [str(provider["model"]), str(provider["architecture_model"])]
    for model in overrides.values():
        ordered.append(model)
    models: list[str] = []
    for model in ordered:
        if model and model not in models:
            models.append(model)
    return models


def model_for_role(role: str, policy: Path = KERNEL_JSON) -> str:
    """The model `role` resolves to when its request names none: the provider's rule
    (`ClaudeCliProvider.model_for`) restated over the policy file, which the detector
    cross-checks against the provider for every role."""
    if role not in ROLE_MAX_TURNS:
        raise ValueError(f"role the worker policy does not know: {role!r}")
    provider = read_provider(policy)
    overrides: dict[str, str] = provider["model_overrides"]  # type: ignore[assignment]
    if role in overrides:
        return overrides[role]
    if role == ARCHITECTURE_HOLDOUT and provider["architecture_model"]:
        return str(provider["architecture_model"])
    return str(provider["model"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--policy",
        type=Path,
        default=KERNEL_JSON,
        help="the kernel policy to read; default: ./.factory/kernel.json",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--list",
        action="store_true",
        help="print every distinct model a run can use, one slug per line",
    )
    action.add_argument("--role", help="print the model this role resolves to")
    args = parser.parse_args(argv)
    try:
        if args.list:
            lines = configured_models(args.policy)
        else:
            lines = [model_for_role(args.role, args.policy)]
    except ValueError as exc:
        print(f"{REFUSED_PREFIX} {exc}", file=sys.stderr, flush=True)
        return 1
    for line in lines:
        print(line)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
