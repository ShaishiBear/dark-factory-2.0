import json
from pathlib import Path
import tempfile
import unittest

from factory_kernel.benchmark import load_results, load_suite, score


SHA = "a" * 40


class FactoryBenchmarkTests(unittest.TestCase):
    def write(self, root: Path, name: str, value: object) -> Path:
        path = root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def suite(self, root: Path, *, visibility: str = "public"):
        return load_suite(
            self.write(
                root,
                f"{visibility}.json",
                {
                    "version": "1.0",
                    "suite_id": f"suite-{visibility}",
                    "visibility": visibility,
                    "cases": [
                        {"id": "ambiguous", "description": "Ambiguity stops.", "expected": "stop"},
                        {"id": "good", "description": "Good evidence merges.", "expected": "merge"},
                    ],
                },
            )
        )

    def test_exact_results_pass_and_are_sha_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite = self.suite(root)
            results_path = self.write(
                root,
                "results.json",
                {
                    "version": "1.0",
                    "factory_sha": SHA,
                    "results": [
                        {"id": "ambiguous", "outcome": "stop"},
                        {"id": "good", "outcome": "merge"},
                    ],
                },
            )
            results = load_results(results_path, factory_sha=SHA)
            value = score([suite], results=results, factory_sha=SHA)
            self.assertEqual(value["verdict"], "pass")
            self.assertEqual(value["cases_total"], 2)
            self.assertEqual(value["cases_passed"], 2)
            self.assertFalse(value["private_suite_present"])
            self.assertRegex(value["case_set_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(value["results_sha256"], r"^[0-9a-f]{64}$")

    def test_wrong_outcome_is_a_failed_benchmark_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            suite = self.suite(root)
            value = score(
                [suite],
                results={"ambiguous": "merge", "good": "merge"},
                factory_sha=SHA,
            )
            self.assertEqual(value["verdict"], "fail")
            self.assertEqual(value["failures"], [
                {"id": "ambiguous", "expected": "stop", "observed": "merge"}
            ])

    def test_missing_or_extra_case_result_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            suite = self.suite(Path(tmp))
            with self.assertRaisesRegex(ValueError, "coverage mismatch"):
                score([suite], results={"ambiguous": "stop"}, factory_sha=SHA)
            with self.assertRaisesRegex(ValueError, "coverage mismatch"):
                score(
                    [suite],
                    results={"ambiguous": "stop", "good": "merge", "extra": "merge"},
                    factory_sha=SHA,
                )

    def test_hidden_authority_can_require_private_suite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public = self.suite(root, visibility="public")
            with self.assertRaisesRegex(ValueError, "private hidden benchmark"):
                score(
                    [public],
                    results={"ambiguous": "stop", "good": "merge"},
                    factory_sha=SHA,
                    require_private=True,
                )
            private = self.suite(root, visibility="private")
            # Duplicate IDs across public/private suites are prohibited to prevent result ambiguity.
            with self.assertRaisesRegex(ValueError, "duplicate benchmark case id"):
                score(
                    [public, private],
                    results={"ambiguous": "stop", "good": "merge"},
                    factory_sha=SHA,
                    require_private=True,
                )

    def test_results_must_bind_to_exact_factory_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.write(
                root,
                "results.json",
                {"version": "1.0", "factory_sha": "b" * 40, "results": []},
            )
            with self.assertRaisesRegex(ValueError, "exact factory SHA"):
                load_results(path, factory_sha=SHA)


if __name__ == "__main__":
    unittest.main()
