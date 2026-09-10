#!/usr/bin/env python3
"""Exercise the workflow's actual skip gate against real fork histories.

Run: python3 -m unittest discover -s .oklabs -p test_sync_skip.py -v
"""

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".forgejo/workflows/sync.yml"


class SyncSkipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Sync test")
        self.git("config", "user.email", "sync@example.invalid")
        self.git("config", "core.hooksPath", "/dev/null")
        self.base = self.commit("base")
        self.fork = self.commit("fork")
        self.git("update-ref", "refs/remotes/origin/main", self.fork)
        self.git("checkout", "-b", "upstream", self.base)
        self.upstream = self.commit("upstream")
        self.git("update-ref", "refs/remotes/origin/upstream", self.upstream)
        self.git("checkout", "main")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, content):
        (self.repo / "file").write_text(content + "\n")
        self.git("add", "file")
        self.git("commit", "-m", content)
        return self.git("rev-parse", "HEAD")

    def run_gate(self, *, force="false", mirror=None):
        # Extract the production block, not a copy of its condition. Stop at
        # the mirror push: this fixture must never contact a real remote.
        workflow = WORKFLOW.read_text()
        start = workflow.index('          echo "upstream: $OLD -> $NEW"')
        end = workflow.index('          if [ "$OLD" != "$NEW" ]; then', start)
        script = textwrap.dedent(workflow[start:end]) + "echo SYNC_CONTINUE\n"
        env_file = self.repo / "github-env"
        env_file.write_text("")
        result = subprocess.run(
            ["bash", "-e", "-c", script], cwd=self.repo, check=True,
            capture_output=True, text=True,
            env={**os.environ, "OLD": mirror or self.upstream,
                 "NEW": self.upstream, "FORCE": force, "GITHUB_ENV": str(env_file)},
        )
        return "SYNC_SKIP=1" in env_file.read_text(), "SYNC_CONTINUE" in result.stdout

    def test_failed_rebase_retries_with_unchanged_mirror(self):
        result = subprocess.run(
            ["git", "rebase", self.upstream], cwd=self.repo,
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.git("diff", "--name-only", "--diff-filter=U"))
        self.git("rebase", "--abort")
        self.assertEqual(self.git("rev-parse", "origin/upstream"), self.upstream)
        self.assertEqual(self.run_gate(), (False, True))

    def test_converged_main_skips_with_unchanged_mirror(self):
        self.git("checkout", "-b", "repaired", self.upstream)
        repaired = self.commit("reapplied fork")
        self.git("update-ref", "refs/remotes/origin/main", repaired)
        self.assertEqual(self.run_gate(), (True, False))

    def test_force_rechecks_converged_main(self):
        self.git("update-ref", "refs/remotes/origin/main", self.upstream)
        self.assertEqual(self.run_gate(force="true"), (False, True))

    def test_changed_mirror_still_advances(self):
        self.git("update-ref", "refs/remotes/origin/main", self.upstream)
        self.assertEqual(self.run_gate(mirror=self.base), (False, True))


if __name__ == "__main__":
    unittest.main()
