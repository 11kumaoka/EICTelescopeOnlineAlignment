"""Host-side mounting regressions; Docker is mocked, not a runtime smoke test."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class CheckoutMountTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="telescope launcher ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.checkout = self.directory / "checkout with spaces"
        (self.checkout / "environment").mkdir(parents=True)
        launcher = Path(__file__).resolve().parents[1] / "environment/run.sh"
        shutil.copyfile(launcher, self.checkout / "environment/run.sh")
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        self.record = self.directory / "docker.jsonl"
        self.common = self.directory / "shared metadata"
        self.common.mkdir()
        docker = self.bin / "docker"
        docker.write_text('#!/usr/bin/env python3\n'
            'import json,os,sys\n'
            'with open(os.environ["LAUNCH_TEST_RECORD"],"a") as f:\n'
            ' f.write(json.dumps(sys.argv[1:])+"\\n")\n'
            'if sys.argv[1]=="inspect":\n'
            ' print(os.environ.get("LAUNCH_TEST_MOUNT", "") if "Mounts" in sys.argv[3] else "test-image")\n')
        docker.chmod(0o755)
        git = self.bin / "git"
        git.write_text('#!/usr/bin/env python3\nimport os\nprint(os.environ["LAUNCH_TEST_COMMON"])\n')
        git.chmod(0o755)
        self.environment = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
            LAUNCH_TEST_RECORD=str(self.record), LAUNCH_TEST_COMMON=str(self.common))
        self.environment.pop("TESTBENCH_CONTAINER", None)

    def run_launcher(self, *arguments):
        result = subprocess.run(["bash", "environment/run.sh", *arguments],
            cwd=self.checkout, env=self.environment, text=True, capture_output=True)
        calls = [json.loads(line) for line in self.record.read_text().splitlines()] if self.record.exists() else []
        return result, calls

    def test_plain_checkout_mount_and_literal_arguments(self):
        (self.checkout / ".git").mkdir()
        result, calls = self.run_launcher("python3", "a b.py", "$(literal)")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)
        argv = calls[0]
        self.assertEqual(argv[0], "run")
        self.assertIn(str(self.checkout) + ":/work", argv)
        self.assertNotIn("--mount", argv)
        self.assertEqual(argv[-3:], ["python3", "a b.py", "$(literal)"])

    def test_worktree_exposes_shared_git_metadata_read_only(self):
        (self.checkout / ".git").write_text("gitdir: unused-by-mock\n")
        result, calls = self.run_launcher("true")
        self.assertEqual(result.returncode, 0, result.stderr)
        argv = calls[0]
        self.assertEqual(argv[argv.index("--mount") + 1],
            f"type=bind,source={self.common},target={self.common},readonly")
        self.assertIn(str(self.checkout) + ":/work", argv)

    def test_wrong_existing_container_is_refused_before_exec(self):
        self.environment.update(TESTBENCH_CONTAINER="old-container", LAUNCH_TEST_MOUNT=str(self.directory))
        result, calls = self.run_launcher("true")
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not mount this checkout", result.stderr)
        self.assertEqual([call[0] for call in calls], ["inspect"])

    def test_worktree_refuses_existing_container_even_with_matching_mount(self):
        (self.checkout / ".git").write_text("gitdir: unused-by-mock\n")
        self.environment.update(TESTBENCH_CONTAINER="old-container", LAUNCH_TEST_MOUNT=str(self.checkout))
        result, calls = self.run_launcher("true")
        self.assertEqual(result.returncode, 2)
        self.assertIn("default launcher", result.stderr)
        self.assertEqual([call[0] for call in calls], ["inspect"])

    def test_matching_plain_checkout_can_use_existing_container(self):
        (self.checkout / ".git").mkdir()
        self.environment.update(TESTBENCH_CONTAINER="same-container", LAUNCH_TEST_MOUNT=str(self.checkout))
        result, calls = self.run_launcher("true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[0] for call in calls], ["inspect", "inspect", "exec"])


if __name__ == "__main__":
    unittest.main()
