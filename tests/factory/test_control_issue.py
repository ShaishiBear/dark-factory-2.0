"""Control records must not erase work identities or poison the programme inventory."""
from copy import deepcopy
import unittest
from unittest.mock import Mock

from factory_kernel.control_issue import original_owner_stop
from factory_kernel.programme import ProgrammeRefused, compile_programme
from factory_kernel.programme_runtime import ProgrammeQueue
from tests.factory.test_factory_programme import BOT, REPO, FakeGitHub


class ControlIssueTests(unittest.TestCase):
    def setUp(self):
        self.github = FakeGitHub()
        self.row = {"number": 194, "title": "Owner requested factory stop", "state": "closed",
                    "body": "<!-- dark-factory-owner-stop:" + "a" * 32 + " -->\n\nPause work.\n",
                    "user": {"login": BOT, "type": "Bot"}, "labels": []}
        self.issue = {key: self.row[key] for key in ("number", "title", "body")}
        self.issue.update(lastEditedAt=None, author={"login": BOT.removesuffix("[bot]"), "__typename": "Bot"})
        self.response = {"data": {"repository": {"nameWithOwner": REPO, "issue": self.issue}}}
        self.github.json = Mock(return_value=self.response)

    def test_original_app_stop_is_control_even_after_close_or_label_removal(self):
        self.github.rows = [self.row]
        programme = compile_programme(self.github.source, repository=REPO)
        for state in ("open", "closed"):
            self.row["state"] = state
            with self.subTest(state=state):
                self.assertEqual(ProgrammeQueue(self.github, "main").inventory(programme), {})
        self.github.json.assert_called()
        self.assertIn("number=194", self.github.json.call_args.args[0])

    def test_marker_title_or_label_alone_cannot_hide_an_edited_work_item(self):
        for edited in ("2026-09-16T08:00:00Z", ""):
            self.issue["lastEditedAt"] = edited
            self.github.rows = [self.row]
            with self.subTest(edited=edited), self.assertRaisesRegex(ProgrammeRefused, "lost its programme binding"):
                ProgrammeQueue(self.github, "main").inventory(compile_programme(self.github.source, repository=REPO))

    def test_missing_or_changed_platform_identity_refuses(self):
        invalid = []
        for key in ("lastEditedAt", "author", "number", "body", "title"):
            value = deepcopy(self.response)
            del value["data"]["repository"]["issue"][key]
            invalid.append(value)
        for key, value in (("number", 195), ("body", self.row["body"] + "edited"),
                           ("title", "Changed"), ("author", {"login": "intruder", "__typename": "User"}),
                           ("author", {"login": BOT, "__typename": "User"})):
            response = deepcopy(self.response)
            response["data"]["repository"]["issue"][key] = value
            invalid.append(response)
        invalid += [{}, {"data": None}, {"data": ["malformed"]}, {"data": {"repository": None}},
                    {"data": {"repository": {"nameWithOwner": "elsewhere/repo", "issue": self.issue}}},
                    {**self.response, "errors": [{"message": "partial response"}]}]
        for response in invalid:
            self.github.json.return_value = response
            with self.subTest(response=response):
                self.assertFalse(original_owner_stop(self.github, self.row, BOT))

    def test_non_control_shapes_refuse_before_platform_read(self):
        for changes in ({"user": {"login": "intruder", "type": "User"}},
                        {"title": "A work item"}, {"number": True}, {"body": "marker removed"},
                        {"body": self.row["body"] + "<!-- dark-factory-programme:forged -->"},
                        {"body": self.row["body"].replace("Pause work.", " " * 5)},
                        {"body": self.row["body"].replace("Pause work.", "x" * 2001)}):
            with self.subTest(changes=changes):
                self.assertFalse(original_owner_stop(self.github, {**self.row, **changes}, BOT))
        self.github.json.assert_not_called()

    def test_query_error_is_not_forgiven(self):
        self.github.json.side_effect = RuntimeError("GitHub unavailable")
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            original_owner_stop(self.github, self.row, BOT)


if __name__ == "__main__":
    unittest.main()
