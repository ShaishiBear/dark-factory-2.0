"""Experience packets (SPECIFICATION 11.1, C10, WP11): bounded, role-scoped, advisory; blind roles
get nothing before any search, at the one payload funnel both worker paths pass through."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from factory_kernel import experience
from factory_kernel.canonical import sha256_value
from factory_kernel.experience import (BLIND_ROLES, MARKER, RETRIEVAL_ROLES, ExperienceRefused, carries_learned_material,
                                       lesson_records, no_memory_packet, packet_context, retrieve, task_from_artifacts)
from factory_kernel.lessons import admit, evaluate, parse_policy
from factory_kernel.runtime import KernelRuntime, NeedsHuman, RunPaths
from factory_kernel.worker_policy import AUTHORITY_ROLES, ROLE_TOOLS
from tests.factory.test_factory_authority_bounds import _Provider, _runtime
from tests.factory.test_lessons import POLICY, gates

TASK = {"contamination_group_id": "issue-7"}
POLICY_IMPLEMENT = parse_policy({**POLICY, "eligible_roles": ["implement", "plan"], "task_families": ["operator"]})


def proposal(**overrides) -> dict:
    base = {"schema": "dark-factory/lesson-proposal", "schema_version": "1.0", "status": "proposed", "plan_digest": "b" * 64,
            "predicate_id": "new-architecture-dependency-v1", "hypothesis": "The strict operator refuses equal values.",
            "causal_mechanism": "Boundary comparison decides admission.", "outcome": "provisional", "selected_candidate_id": "strict",
            "mechanism_families": ["operator"], "scope": "this contract, workload, environment and decision policy only",
            "not_established": ["global optimality", "generality beyond the measured cohort", "admission"]}
    base.update(overrides)
    return base


def admitted_record(*, group="issue-7", lineage=(), **overrides) -> dict:
    prop = proposal(**overrides)
    admission = admit({**prop, "id": "lesson-1"}, evaluate(gates(), POLICY_IMPLEMENT), lineage=lineage)
    return {"proposal": {**prop, "id": "lesson-1"}, "admission": admission, "plan": {"contamination_group_id": group}}


class RetrieveTests(unittest.TestCase):
    def test_every_non_allowlisted_role_gets_an_empty_blind_packet_before_any_search(self) -> None:
        source = mock.Mock(side_effect=AssertionError("a blind role must never search the records"))
        for role in sorted((set(ROLE_TOOLS) | AUTHORITY_ROLES | BLIND_ROLES) - RETRIEVAL_ROLES):
            with self.subTest(role):
                packet = retrieve(role, TASK, ("app/x.py",), records=source, policy=POLICY_IMPLEMENT)
                self.assertEqual((packet.mode, packet.items, packet.records_examined), ("blind", (), 0))
                self.assertEqual(packet_context(packet), "")
        source.assert_not_called()
        self.assertTrue(AUTHORITY_ROLES <= BLIND_ROLES)
        self.assertTrue({"test_author", "conformance"} <= BLIND_ROLES)
        self.assertFalse(RETRIEVAL_ROLES & BLIND_ROLES)

    def test_a_permitted_role_receives_bound_eligible_applicable_lessons_with_provenance_and_counter_evidence(self) -> None:
        record = admitted_record(lineage=["lesson-0"])
        packet = retrieve("implement", TASK, ("app/x.py",), records=lambda: [record], policy=POLICY_IMPLEMENT)
        self.assertEqual((packet.mode, len(packet.items), packet.records_examined), ("retrieval", 1, 1))
        (item,) = packet.items
        self.assertEqual((item["lesson_id"], item["applicability"], item["qualification_status"]), ("lesson-1", "same-contract", "UNPROVEN"))
        self.assertEqual(item["provenance"]["proposal_sha256"], record["admission"]["proposal_sha256"])
        self.assertEqual(item["provenance"]["record_sha256"], record["admission"]["record_sha256"])
        self.assertEqual(item["provenance"]["policy_sha256"], POLICY_IMPLEMENT.sha256)
        self.assertEqual(item["counter_evidence"], {"not_established": ["global optimality", "generality beyond the measured cohort", "admission"],
                                                    "lineage": ["lesson-0"]})
        text = packet_context(packet)
        self.assertTrue(text.startswith("\n\n" + MARKER))
        self.assertIn("The strict operator refuses equal values.", text)
        self.assertTrue(carries_learned_material(text))
        self.assertEqual(packet.to_dict()["authority"], "advisory-context-only")
        self.assertNotIn("items", packet.record())
        self.assertEqual(packet.record()["item_refs"][0]["record_sha256"], record["admission"]["record_sha256"])
        self.assertEqual(packet.input_digest, sha256_value({"role": "implement", "mode": "retrieval", "task_digest": packet.task_digest,
                                                            "items": [item]}))
        # A declared family, with no shared contract, is the other applicability route.
        by_family = retrieve("plan", {"families": ["operator"]}, (), records=[admitted_record(group="issue-9")], policy=POLICY_IMPLEMENT)
        self.assertEqual(by_family.items[0]["applicability"], "declared-family")

    def test_unbound_ineligible_and_inapplicable_records_are_skipped_and_counted(self) -> None:
        bound = admitted_record()
        unbound = admitted_record(); unbound["proposal"]["hypothesis"] = "edited after admission"
        proposed = admitted_record(); proposed["admission"] = admit({**proposed["proposal"]}, evaluate(gates(policy=False), None))
        elsewhere = admitted_record(group="issue-9")
        packet = retrieve("implement", TASK, (), records=[bound, unbound, proposed, elsewhere, "junk", {"proposal": {}}], policy=POLICY_IMPLEMENT)
        self.assertEqual([i["lesson_id"] for i in packet.items], ["lesson-1"])
        self.assertEqual(packet.skipped, {"unbound": 1, "ineligible": 1, "inapplicable": 1, "over_bound": 0, "malformed": 2})
        self.assertEqual(packet.records_examined, 6)
        # Role not eligible under the policy, no policy, or not current: nothing is retrieved.
        self.assertEqual(retrieve("repair", TASK, (), records=[bound], policy=POLICY_IMPLEMENT).skipped["ineligible"], 1)
        self.assertEqual(retrieve("implement", TASK, (), records=[bound], policy=None).items, ())
        self.assertEqual(retrieve("implement", TASK, (), records=[bound], policy=POLICY_IMPLEMENT, current=False).items, ())

    def test_the_packet_is_bounded_by_count_and_bytes(self) -> None:
        records = [admitted_record(hypothesis=f"Lesson number {n}.") for n in range(5)]
        packet = retrieve("implement", TASK, (), records=records, policy=POLICY_IMPLEMENT)
        self.assertEqual((len(packet.items), packet.skipped["over_bound"]), (3, 2))
        two = retrieve("implement", TASK, (), {"max_items": 2}, records=records, policy=POLICY_IMPLEMENT)
        self.assertEqual((len(two.items), two.skipped["over_bound"]), (2, 3))
        tiny = retrieve("implement", TASK, (), {"max_bytes": 10}, records=records, policy=POLICY_IMPLEMENT)
        self.assertEqual((tiny.items, tiny.skipped["over_bound"]), ((), 5))
        self.assertLessEqual(packet.bytes, experience.DEFAULT_BUDGET["max_bytes"])
        for bad in ({"max_items": -1}, {"max_bytes": True}, {"max_items": "3"}):
            with self.subTest(bad), self.assertRaises(ExperienceRefused):
                retrieve("implement", TASK, (), bad, records=records, policy=POLICY_IMPLEMENT)
        with self.assertRaises(ExperienceRefused):
            retrieve("", TASK, (), records=records, policy=POLICY_IMPLEMENT)
        with self.assertRaises(ExperienceRefused):
            retrieve("implement", "task", (), records=records, policy=POLICY_IMPLEMENT)

    def test_the_no_memory_challenger_packet_is_empty_and_says_what_it_lacks(self) -> None:
        packet = no_memory_packet("implement", TASK, ("app/x.py",))
        self.assertEqual((packet.mode, packet.items, packet_context(packet)), ("no-memory", (), ""))
        self.assertIn("no learned packet and no previous-winner exemplar were in the generation context", packet.limitations)
        self.assertEqual(packet.task_digest, retrieve("implement", TASK, ("app/x.py",), records=[], policy=None).task_digest)
        self.assertNotEqual(packet.input_digest, retrieve("implement", TASK, ("app/x.py",), records=[admitted_record()],
                                                         policy=POLICY_IMPLEMENT).input_digest)

    def test_lesson_records_and_task_identity_come_from_the_runs_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp)
            self.assertEqual((lesson_records(artifacts), task_from_artifacts(artifacts)), ([], {}))
            record = admitted_record()
            (artifacts / "investigation-implement.json").write_text(json.dumps({
                "lesson_proposal": record["proposal"], "lesson_admission": record["admission"], "plan": record["plan"]}), encoding="utf-8")
            (artifacts / "investigation-green.json").write_text("not json", encoding="utf-8")
            (artifacts / "investigation-repair.json").write_text(json.dumps({"lesson_proposal": None}), encoding="utf-8")
            found = lesson_records(artifacts)
            self.assertEqual([r["source"] for r in found], ["investigation-implement.json"])
            self.assertEqual(found[0]["admission"], record["admission"])
            (artifacts / "task-contract.json").write_text(json.dumps({"issue": {"number": 7}}), encoding="utf-8")
            self.assertEqual(task_from_artifacts(artifacts), {"contamination_group_id": "issue-7"})
            (artifacts / "task-contract.json").write_text(json.dumps({"issue": {"number": True}}), encoding="utf-8")
            self.assertEqual(task_from_artifacts(artifacts), {})
            self.assertEqual(lesson_records(Path(tmp) / "missing"), [])


class PayloadFunnelTests(unittest.TestCase):
    """Role visibility at the one funnel: `_experience_context` on both `_agent` paths."""

    def prepare(self, tmp: Path, provider, *, cls=KernelRuntime):
        paths = RunPaths.create(tmp, "run")
        (paths.artifacts / "task-contract.json").write_text(json.dumps({"issue": {"number": 7}}), encoding="utf-8")
        record = admitted_record()
        (paths.artifacts / "investigation-implement.json").write_text(json.dumps({
            "lesson_proposal": record["proposal"], "lesson_admission": record["admission"], "plan": record["plan"]}), encoding="utf-8")
        rt = _runtime(tmp, provider, cls=cls)
        rt._lesson_policy = lambda: POLICY_IMPLEMENT
        return rt, paths

    def test_a_permitted_role_gets_the_packet_and_a_blind_role_gets_none_on_the_base_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = _Provider()
            rt, paths = self.prepare(Path(tmp), provider)
            env = {"ARTIFACTS_DIR": str(paths.artifacts)}
            with contextlib.redirect_stdout(io.StringIO()):
                rt._agent("implement", Path(tmp), paths, context="issue body", env=env)
            (request,) = provider.requests
            self.assertIn(MARKER, request.prompt)
            self.assertIn("The strict operator refuses equal values.", request.prompt)
            self.assertIn("issue body", request.prompt)
            recorded = json.loads((paths.artifacts / "experience-implement.json").read_text(encoding="utf-8"))
            self.assertEqual((recorded["mode"], recorded["item_count"], recorded["authority"]), ("retrieval", 1, "advisory-context-only"))
            # The record is by reference only: the artifacts directory is readable by the run's
            # tool-bearing roles, and `test_author` and `conformance` are blind. No lesson text on disk.
            raw = (paths.artifacts / "experience-implement.json").read_text(encoding="utf-8")
            self.assertNotIn("strict operator", raw)
            self.assertNotIn("items", recorded)
            self.assertEqual(recorded["item_refs"], [{"lesson_id": "lesson-1", "proposal_sha256": admitted_record()["admission"]["proposal_sha256"],
                                                      "record_sha256": admitted_record()["admission"]["record_sha256"]}])
            self.assertEqual(recorded["input_digest"], retrieve("implement", TASK, (), records=lesson_records(paths.artifacts),
                                                                policy=POLICY_IMPLEMENT).input_digest)
            for path in paths.artifacts.iterdir():
                self.assertTrue(path.name.startswith("investigation-") or "strict operator" not in path.read_text(encoding="utf-8"), path.name)
            for role in ("holdout", "architecture-holdout", "contract-certifier", "design-certifier", "governor-certifier", "test_author", "conformance"):
                with self.subTest(role):
                    provider.requests.clear()
                    with mock.patch("factory_kernel.runtime.lesson_records", side_effect=AssertionError("blind roles never search")), \
                            contextlib.redirect_stdout(io.StringIO()):
                        rt._agent(role, Path(tmp), paths, context="the diff", env=env)
                    (request,) = provider.requests
                    self.assertNotIn(MARKER, request.prompt)
                    self.assertNotIn("strict operator", request.prompt)
                    recorded = json.loads((paths.artifacts / f"experience-{role}.json").read_text(encoding="utf-8"))
                    self.assertEqual((recorded["mode"], recorded["item_count"], recorded["records_examined"]), ("blind", 0, 0))

    def test_a_blind_role_handed_learned_material_in_its_context_is_refused_before_any_model_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = _Provider()
            rt, paths = self.prepare(Path(tmp), provider)
            smuggled = "the diff" + packet_context(retrieve("implement", TASK, (), records=lesson_records(paths.artifacts), policy=POLICY_IMPLEMENT))
            self.assertTrue(carries_learned_material(smuggled))
            with self.assertRaises(NeedsHuman), contextlib.redirect_stdout(io.StringIO()):
                rt._agent("holdout", Path(tmp), paths, context=smuggled, env={"ARTIFACTS_DIR": str(paths.artifacts)})
            self.assertEqual(provider.requests, [])
            self.assertFalse((paths.artifacts / "experience-holdout.json").exists())
            # The same material is what a permitted role legitimately receives.
            with contextlib.redirect_stdout(io.StringIO()):
                rt._agent("repair", Path(tmp), paths, context=smuggled, env={"ARTIFACTS_DIR": str(paths.artifacts)})
            self.assertEqual(len(provider.requests), 1)

    def test_the_worker_path_passes_through_the_same_funnel(self) -> None:
        from factory_kernel import worker_runtime as wr

        with tempfile.TemporaryDirectory() as tmp:
            provider = _Provider()
            rt, paths = self.prepare(Path(tmp), provider, cls=wr.WorkerControlledRuntime)
            rt._assert_clean = lambda cwd: None
            rt._refuse_literal_artifacts_dir = lambda cwd: None
            rt._record_agent = lambda *a, **k: None
            env = {"ARTIFACTS_DIR": str(paths.artifacts)}
            with mock.patch.object(wr, "method_block", return_value=""), mock.patch.object(wr, "may_change_repo", return_value=False), \
                    contextlib.redirect_stdout(io.StringIO()):
                # `plan` is a permitted, non-mutation role: the worker path's post-stage Git checks do
                # not apply, and the packet still arrives through the shared funnel.
                rt._agent("plan", Path(tmp), paths, context="issue body", env=env)
                (request,) = provider.requests
                self.assertIn(MARKER, request.prompt)
                provider.requests.clear()
                with mock.patch("factory_kernel.runtime.lesson_records", side_effect=AssertionError("blind roles never search")):
                    rt._agent("conformance", Path(tmp), paths, context="findings", env=env)
                (request,) = provider.requests
                self.assertNotIn(MARKER, request.prompt)
                with self.assertRaises(NeedsHuman):
                    rt._agent("holdout", Path(tmp), paths, context=MARKER + " smuggled", env=env)

    def test_without_an_installed_policy_every_packet_is_empty_and_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            provider = _Provider()
            rt, paths = self.prepare(Path(tmp), provider)
            rt._lesson_policy = KernelRuntime._lesson_policy.__get__(rt)
            self.assertIsNone(rt._lesson_policy())  # this branch installs no policy
            with contextlib.redirect_stdout(io.StringIO()):
                rt._agent("implement", Path(tmp), paths, context="issue body", env={"ARTIFACTS_DIR": str(paths.artifacts)})
            (request,) = provider.requests
            self.assertNotIn(MARKER, request.prompt)
            record_path = paths.artifacts / "experience-implement.json"
            self.assertTrue(record_path.exists(), "every stage records its packet, empty or not")
            recorded = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual((recorded["mode"], recorded["item_count"], recorded["records_examined"], recorded["skipped"]["ineligible"]),
                             ("retrieval", 0, 1, 1))


if __name__ == "__main__":
    unittest.main()
