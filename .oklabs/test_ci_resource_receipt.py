import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("ci_resource", Path(__file__).with_name("ci-resource-receipt.py"))
resource = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resource)


class ResourceReceiptTests(unittest.TestCase):
    def test_preserves_oom_counters_and_marks_unavailable_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "memory.peak").write_text("987654321\n")
            (root / "memory.events").write_text("oom 1\noom_kill 1\n")
            result = resource.read_cgroup(root)
            self.assertEqual(result["memory.peak"]["value"], "987654321")
            self.assertIn("oom_kill 1", result["memory.events"]["value"])
            self.assertEqual(result["cpu.stat"]["unavailable"], "FileNotFoundError")

    def test_does_not_capture_ambient_secrets_or_process_arguments(self):
        with patch.dict(os.environ, OKLABS_BOT_PAT="do-not-capture", GITHUB_RUN_NUMBER="217"), patch.object(resource, "command_value", return_value={"unavailable": "fixture"}), patch.object(resource, "read_cgroup", return_value={}):
            result = resource.receipt()
        encoded = json.dumps(result)
        self.assertNotIn("do-not-capture", encoded)
        self.assertNotIn("OKLABS_BOT_PAT", encoded)
        self.assertEqual(result["run_number"], "217")
        self.assertIn("unavailable", result["phase_times"])

    def test_missing_tool_is_explicit(self):
        with patch.object(resource.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(resource.command_value(["rustc", "--version"]), {"unavailable": "FileNotFoundError"})


if __name__ == "__main__":
    unittest.main()
