"""Read-only view of a governed programme transition (WP03): the phase its journal receipts
derive, the pending remote request if any, journal/effect-store agreement, and the release
decision over an independently observed precondition file. No remote call, no effect, no
writer other than the bounded output record. Refuses a history that names another owner
(same rule as the claim views)."""
from __future__ import annotations

import argparse
from typing import Any

from .claim_views import ClaimViewRefused, _events, _read_json, _write
from .frontdoor_intent import IntentRefused
from .programme_transition import TransitionRefused, compare_stores, request_release
from .transition_journal import pending_request, receipts_of, verify

STORE_STATES = {"started", "observed_success", "observed_failure", "uncertain"}


def transition_status(args: argparse.Namespace, cfg: Any) -> int:
    try:
        if not isinstance(args.transition, str) or len(args.transition) != 64 or any(c not in "0123456789abcdef" for c in args.transition):
            raise ClaimViewRefused("transition id must be 64 lowercase hex characters")
        if args.store_state is not None and args.store_state not in STORE_STATES:
            raise ClaimViewRefused(f"store state must be one of {sorted(STORE_STATES)}")
        events = _events(args.state_dir, args.owner, args.project, cfg.repository)
        receipts = receipts_of(events, args.transition)
        phase = verify(receipts)
        pending = pending_request(receipts)
        observation = _read_json(args.observation, what="release observation")
        release = request_release(observation) if observation is not None else None
    except (IntentRefused, ClaimViewRefused, TransitionRefused, ValueError) as exc:
        print(f"FACTORY_TRANSITION_REFUSED reason={str(exc)[:300]!r}", flush=True)
        return 1
    agreement = compare_stores(phase, args.store_state) if args.store_state is not None or pending is not None else None
    record = {
        "schema": "dark-factory/transition-status", "schema_version": "1.0", "authority": "projection-only",
        "repository": cfg.repository, "project": args.project, "project_version": len(events),
        "transition_id": args.transition, "phase": phase, "receipts": len(receipts),
        "pending_request": {"request_id": pending["request_id"], "request_sha256": pending["request_sha256"], "event": pending["event"]} if pending else None,
        "store_state": args.store_state, "store_agreement": agreement,
        "release": release, "remote_calls": 0,
    }
    digest = _write(args.output, record)
    print(f"FACTORY_TRANSITION project={args.project} transition={args.transition[:12]} phase={phase} receipts={len(receipts)} "
          f"pending={pending['request_id'][:8] if pending else '-'} agreement={agreement or '-'} "
          f"release={release['status'] if release else '-'} sha256={digest}", flush=True)
    return 0


__all__ = ["transition_status"]
