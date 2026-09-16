import unittest
from unittest.mock import patch

from factory_kernel.feedback_sandbox import DockerSandbox, SandboxError


IMAGE = "sha256:" + "a" * 64


class SandboxPolicyTests(unittest.TestCase):
    def test_only_immutable_local_image(self):
        for image in ("python:latest", "--privileged", "", "sha256:" + "x" * 64):
            with self.assertRaises(ValueError):
                DockerSandbox(image)

    def test_no_host_mounts_network_privilege_or_image_entrypoint(self):
        args = DockerSandbox(IMAGE).create_args("test-name")
        for option, value in (("--network", "none"), ("--read-only", "--cap-drop"),
                              ("--cap-drop", "ALL"), ("--user", "65534:65534"),
                              ("--security-opt", "no-new-privileges=true"),
                              ("--pids-limit", "32"), ("--memory", "128m"),
                              ("--memory-swap", "128m"), ("--entrypoint", "python"),
                              ("--pull", "never"), ("--log-driver", "none")):
            self.assertEqual(args[args.index(option) + 1], value)
        self.assertNotIn("--volume", args)
        self.assertNotIn("--mount", args)
        self.assertNotIn("--privileged", args)
        self.assertNotIn("--env-file", args)

    def test_image_volumes_and_wrong_identity_refused(self):
        for facts in ({"Id": IMAGE, "Os": "windows", "Config": {}},
                      {"Id": IMAGE, "Os": "linux", "Config": {"Volumes": {"/data": {}}}},
                      {"Id": "different", "Os": "linux", "Config": {}}):
            import json
            sandbox = DockerSandbox(IMAGE)
            with patch.object(sandbox, "_control", return_value=json.dumps([facts]).encode()):
                with self.assertRaises(SandboxError):
                    sandbox.preflight()

    def test_failed_create_still_cleans_exact_unique_container(self):
        sandbox, commands = DockerSandbox(IMAGE), []
        def control(args, **kwargs):
            commands.append(args)
            if args[0] == "create":
                raise SandboxError("create interrupted")
            return b""
        with patch.object(sandbox, "_control", side_effect=control):
            with self.assertRaises(SandboxError):
                sandbox.evaluate("def solve(x): return x", [1], seconds=1)
        name = commands[0][commands[0].index("--name") + 1]
        self.assertTrue(name.startswith("factory-feedback-"))
        self.assertEqual(commands[-1], ["rm", "--force", name])

    def test_cleanup_failure_is_not_hidden(self):
        sandbox = DockerSandbox(IMAGE)
        with patch.object(sandbox, "_control", side_effect=SandboxError("cleanup unavailable")):
            with self.assertRaisesRegex(SandboxError, "cleanup unavailable"):
                sandbox.evaluate("def solve(x): return x", [1], seconds=1, cancelled=lambda: True)

    def test_invalid_payload_never_creates_container(self):
        sandbox = DockerSandbox(IMAGE)
        with patch.object(sandbox, "_control") as control:
            for code in ("", "x" * 24_001):
                with self.assertRaises(ValueError):
                    sandbox.evaluate(code, [1], seconds=1)
            control.assert_not_called()


if __name__ == "__main__":
    unittest.main()
