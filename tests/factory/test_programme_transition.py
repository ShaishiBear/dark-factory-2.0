"""Programme transition (C07): the fixed forward phase sequence, uncertainty and its recovery,
stop semantics, the exact identity binding, the release decision's precedence with unknown
kept closed, and journal/effect-store agreement."""
from __future__ import annotations

import unittest

from factory_kernel.programme_transition import (
    EVENTS,
    PHASES,
    RECONCILIATION_REQUIRED,
    RELEASE_ORDER,
    REMOTE_REQUESTS,
    STOP_REFUSED_EVENTS,
    TransitionRefused,
    advance,
    compare_stores,
    replay,
    request_release,
    transition_identity,
)

FORWARD = ("request_fence", "observe_fence", "begin_drain", "observe_drained", "reconcile", "retire_predecessor",
           "request_successor", "observe_successor", "request_release", "observe_release")


def ready(**overrides) -> dict:
    value = {"observed": True, "consent": True, "successor": True, "fenced": True, "queued": 0, "active": 0,
             "pending_effects": 0, "accounting_reconciled": True}
    value.update(overrides)
    return value


class PhaseMachineTests(unittest.TestCase):
    def test_the_forward_sequence_is_the_only_legal_one_and_nothing_skips(self):
        phase = "reviewed"
        for event, expected in zip(FORWARD, PHASES[1:]):
            phase = advance(phase, event)
            self.assertEqual(phase, expected)
        self.assertEqual(replay([(e, False) for e in FORWARD]), "released")
        # Every event from every phase other than its own predecessor is refused.
        for event, (before, _) in EVENTS.items():
            for phase in PHASES:
                if phase == before:
                    continue
                with self.subTest(event=event, phase=phase), self.assertRaises(TransitionRefused):
                    advance(phase, event)
        with self.assertRaises(TransitionRefused):
            advance("released", "request_fence")
        with self.assertRaises(TransitionRefused):
            advance("draining", "nonsense")

    def test_stop_permits_reconciliation_and_refuses_activation_and_release(self):
        self.assertEqual(advance("fence_requested", "observe_fence", stop=True), "fenced")
        self.assertEqual(advance("drained", "reconcile", stop=True), "reconciled")
        self.assertEqual(advance("successor_requested", "mark_uncertain", stop=True), "successor_requested:uncertain")
        self.assertEqual(advance("successor_requested:uncertain", "observe_effect", stop=True), "successor_observed")
        for event in STOP_REFUSED_EVENTS:
            before = EVENTS[event][0]
            with self.subTest(event=event), self.assertRaises(TransitionRefused):
                advance(before, event, stop=True)
        # Every new remote effect and retirement is refused under stop; nothing else is.
        self.assertEqual(STOP_REFUSED_EVENTS, {"request_fence", "request_successor", "request_release", "retire_predecessor"})
        for event in set(EVENTS) - STOP_REFUSED_EVENTS:
            self.assertEqual(advance(EVENTS[event][0], event, stop=True), EVENTS[event][1], event)

    def test_uncertain_requests_recover_only_by_observing_the_same_request(self):
        for request, (before, observed) in REMOTE_REQUESTS.items():
            with self.subTest(request=request):
                uncertain = advance(request, "mark_uncertain")
                self.assertEqual(uncertain, request + ":uncertain")
                self.assertEqual(advance(uncertain, "mark_uncertain"), uncertain, "still unknown, no new transition")
                self.assertEqual(advance(uncertain, "observe_effect"), observed)
                self.assertEqual(advance(uncertain, "observe_no_effect"), before, "a definite no-effect refusal returns to before the request")
                for event in ("request_fence", "begin_drain", "reconcile", "request_release", "observe_drained"):
                    with self.assertRaises(TransitionRefused):
                        advance(uncertain, event)
        with self.assertRaises(TransitionRefused):
            advance("draining", "mark_uncertain")  # only a remote request can be uncertain
        with self.assertRaises(TransitionRefused):
            advance("fenced", "observe_no_effect")

    def test_reconciliation_required_is_reachable_everywhere_and_terminal(self):
        for phase in PHASES + ("fence_requested:uncertain",):
            self.assertEqual(advance(phase, "require_reconciliation"), RECONCILIATION_REQUIRED)
        for event in list(EVENTS) + ["mark_uncertain", "observe_effect"]:
            with self.subTest(event=event), self.assertRaises(TransitionRefused):
                advance(RECONCILIATION_REQUIRED, event)


class IdentityTests(unittest.TestCase):
    def test_identity_is_exact_and_binds_every_field(self):
        raw = {"repository_id": 42, "generation": 3, "old_programme_sha256": "a" * 64, "successor_sha256": "b" * 64,
               "plan_sha256": "c" * 64, "consent_sha256": "d" * 64, "source_sha": "e" * 40}
        identity = transition_identity(raw)
        self.assertEqual(len(identity.transition_id), 64)
        self.assertEqual(identity.to_dict()["transition_id"], identity.transition_id)
        self.assertNotEqual(identity.transition_id, transition_identity({**raw, "generation": 4}).transition_id)
        for name, mutate in (("missing", lambda r: r.pop("plan_sha256")), ("extra", lambda r: r.update(x=1)),
                             ("bad repo", lambda r: r.update(repository_id=0)), ("bool repo", lambda r: r.update(repository_id=True)),
                             ("short sha", lambda r: r.update(source_sha="e" * 39)), ("upper", lambda r: r.update(plan_sha256="C" * 64)),
                             ("same programme", lambda r: r.update(successor_sha256="a" * 64))):
            value = dict(raw); mutate(value)
            with self.subTest(name), self.assertRaises(TransitionRefused):
                transition_identity(value)


class ReleaseTests(unittest.TestCase):
    def test_ready_is_eligible_and_each_single_cause_reports_its_own_code(self):
        self.assertEqual(request_release(ready()), {"status": "eligible", "reason_codes": [], "remote_calls": 0})
        for overrides, code in ((dict(observed=False), "observation_missing"), (dict(consent=False), "consent_invalid"),
                                (dict(successor=False), "successor_unobserved"), (dict(queued=1), "predecessor_not_drained"),
                                (dict(active=1), "predecessor_not_drained"), (dict(pending_effects=1), "effect_uncertain"),
                                (dict(accounting_reconciled=False), "accounting_unreconciled"), (dict(fenced=False), "fence_absent")):
            with self.subTest(code=code):
                self.assertEqual(request_release(ready(**overrides)), {"status": "blocked", "reason_codes": [code], "remote_calls": 0})

    def test_combined_causes_are_listed_in_precedence_order_and_the_observer_comes_first(self):
        blocked = request_release(ready(accounting_reconciled=False, consent=False, active=2, fenced=False))
        self.assertEqual(blocked["reason_codes"], ["fence_absent", "consent_invalid", "predecessor_not_drained", "accounting_unreconciled"])
        self.assertEqual(request_release(ready(observed=False, consent=False, queued=3))["reason_codes"], ["observation_missing"],
                         "without a current observer result nothing else is known")
        self.assertEqual([code for _, code in RELEASE_ORDER][:4],
                         ["observation_missing", "fence_absent", "consent_invalid", "successor_unobserved"])

    def test_unknown_keeps_the_fence(self):
        for overrides, code in ((dict(consent=None), "consent_invalid"), (dict(consent="yes"), "consent_invalid"),
                                (dict(queued=None), "predecessor_not_drained"), (dict(active=-1), "predecessor_not_drained"),
                                (dict(active=True), "predecessor_not_drained"), (dict(pending_effects="0"), "effect_uncertain"),
                                (dict(successor=1), "successor_unobserved"), (dict(accounting_reconciled=None), "accounting_unreconciled")):
            with self.subTest(overrides=overrides):
                self.assertEqual(request_release(ready(**overrides)), {"status": "blocked", "reason_codes": [code], "remote_calls": 0})
        missing = ready(); del missing["successor"]
        self.assertEqual(request_release(missing)["reason_codes"], ["successor_unobserved"])
        with self.assertRaises(TransitionRefused):
            request_release(ready(extra=True))
        with self.assertRaises(TransitionRefused):
            request_release(None)


class StoreAgreementTests(unittest.TestCase):
    def test_journal_and_effect_store_agreement_never_picks_the_side_further_ahead(self):
        self.assertEqual(compare_stores("release_requested", "started"), "consistent")
        self.assertEqual(compare_stores("release_requested", "uncertain"), "uncertain")
        self.assertEqual(compare_stores("release_requested", "observed_success"), RECONCILIATION_REQUIRED, "the store is ahead of the journal: mismatch, not progress")
        self.assertEqual(compare_stores("release_requested", None), RECONCILIATION_REQUIRED, "the journal says requested, the store never saw it")
        self.assertEqual(compare_stores("release_requested:uncertain", "started"), "uncertain")
        self.assertEqual(compare_stores("release_requested:uncertain", "observed_failure"), RECONCILIATION_REQUIRED)
        self.assertEqual(compare_stores("released", "observed_success"), "consistent")
        self.assertEqual(compare_stores("released", None), "consistent")
        self.assertEqual(compare_stores("released", "started"), RECONCILIATION_REQUIRED, "the journal is ahead of the store")
        self.assertEqual(compare_stores("reviewed", "uncertain"), RECONCILIATION_REQUIRED)
        self.assertEqual(compare_stores(RECONCILIATION_REQUIRED, "observed_success"), RECONCILIATION_REQUIRED)
        with self.assertRaises(TransitionRefused):
            compare_stores("nowhere", None)


if __name__ == "__main__":
    unittest.main()
