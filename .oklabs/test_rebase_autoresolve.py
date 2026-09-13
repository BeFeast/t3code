import importlib.util
import pathlib
import unittest

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("ra", HERE / "rebase-autoresolve.py")
ra = importlib.util.module_from_spec(spec)
import sys
sys.modules["ra"] = ra
spec.loader.exec_module(ra)


def conflict(ours, base, theirs, label="abc1234 (patch)"):
    return (
        "<<<<<<< HEAD\n" + "".join(l + "\n" for l in ours)
        + "||||||| parent of abc1234 (patch)\n" + "".join(l + "\n" for l in base)
        + "=======\n" + "".join(l + "\n" for l in theirs)
        + f">>>>>>> {label}\n"
    )


class ComposeRule(unittest.TestCase):
    def test_both_added_at_same_spot_keeps_head_first(self):
        # preload.ts: upstream added onSnapShotEvent, we added onChatReplyToSelection
        text = "  a,\n" + conflict(["  onSnapShotEvent: 1,"], [], ["  onChatReplyToSelection: 2,"]) + "  z,\n"
        out, rules = ra.resolve_text(text)
        self.assertEqual(rules, ["compose"])
        self.assertEqual(out, "  a,\n  onSnapShotEvent: 1,\n  onChatReplyToSelection: 2,\n  z,\n")

    def test_head_deleted_neighbour_patch_appended(self):
        # editor.ts: upstream removed isExternalLauncherError, patch appended after it
        base = ["", "export const isExternalLauncherError = Schema.is(X);"]
        theirs = base + ["", "export interface RemoteEditorTarget {}"]
        text = "tail\n" + conflict([], base, theirs)
        out, rules = ra.resolve_text(text)
        self.assertEqual(rules, ["compose"])
        self.assertEqual(out, "tail\n\nexport interface RemoteEditorTarget {}\n")
        self.assertNotIn("isExternalLauncherError", out)

    def test_rewritten_line_is_real(self):
        with self.assertRaises(ra.Unresolvable):
            ra.resolve_text(conflict(["const x = 1;"], ["const x = 0;"], ["const x = 2;"]))

    def test_refactored_block_is_real(self):
        # DesktopWindow.ts shape: upstream replaced the block, patch inserted into old block
        base = ["  menuTemplate.push(a);", "  void runPromise(popup({ window, template }));", "});"]
        ours = ["  menuTemplate.push(a);", "  void runPromise(popup({ window: ownerWindow, template }));", "  });", "};"]
        theirs = ["  menuTemplate.push(a);", "  menuTemplate.push(...reply(window.webContents, params));", "  void runPromise(popup({ window, template }));", "});"]
        with self.assertRaises(ra.Unresolvable):
            ra.resolve_text(conflict(ours, base, theirs))


class ImportsRule(unittest.TestCase):
    def test_lucide_first_conflict(self):
        base = ['import { Code2, Eye, FolderTree, Globe2, LoaderCircle } from "lucide-react";']
        ours = ['import { Code2, Eye, FolderTree, Globe2 } from "lucide-react";']
        theirs = ["import {", "  Code2,", "  Eye,", "  FoldHorizontal,", "  FolderTree,", "  Globe2,", "  LoaderCircle,", "  UnfoldHorizontal,", '} from "lucide-react";']
        out, rules = ra.resolve_text(conflict(ours, base, theirs))
        self.assertEqual(rules, ["imports"])
        self.assertEqual(out, 'import { Code2, Eye, FolderTree, Globe2, FoldHorizontal, UnfoldHorizontal } from "lucide-react";\n')

    def test_lucide_second_conflict_drops_both_removals(self):
        base = ["import {", "  Code2,", "  Eye,", "  FoldHorizontal,", "  FolderTree,", "  Globe2,", "  LoaderCircle,", "  UnfoldHorizontal,", '} from "lucide-react";']
        ours = ['import { Code2, Eye, FolderTree, Globe2, FoldHorizontal, UnfoldHorizontal } from "lucide-react";']
        theirs = ['import { Code2, Eye, FolderTree, Globe2, LoaderCircle } from "lucide-react";']
        out, rules = ra.resolve_text(conflict(ours, base, theirs))
        self.assertEqual(out, 'import { Code2, Eye, FolderTree, Globe2 } from "lucide-react";\n')

    def test_vi_and_new_module(self):
        base = ['import { beforeEach } from "vite-plus/test";']
        ours = ['import { beforeEach, vi } from "vite-plus/test";']
        theirs = ['import { HttpClient, HttpClientResponse } from "effect/unstable/http";', 'import { beforeEach } from "vite-plus/test";']
        out, rules = ra.resolve_text(conflict(ours, base, theirs))
        self.assertEqual(rules, ["imports"])
        self.assertEqual(
            out,
            'import { beforeEach, vi } from "vite-plus/test";\nimport { HttpClient, HttpClientResponse } from "effect/unstable/http";\n',
        )

    def test_long_import_wraps(self):
        names = [f"VeryLongIdentifierNumber{i}" for i in range(6)]
        base = [f'import {{ {", ".join(names[:4])} }} from "m";']
        ours = [f'import {{ {", ".join(names[:5])} }} from "m";']
        theirs = [f'import {{ {", ".join(names[:4] + names[5:])} }} from "m";']
        out, _ = ra.resolve_text(conflict(ours, base, theirs))
        self.assertTrue(out.startswith("import {\n  VeryLongIdentifierNumber0,\n"))
        self.assertTrue(out.endswith('  VeryLongIdentifierNumber5,\n} from "m";\n'))
        self.assertTrue(all(len(l) <= 100 for l in out.split("\n")))

    def test_type_imports_kept_separate(self):
        base = ['import type { A } from "m";']
        ours = ['import type { A, B } from "m";']
        theirs = ['import type { A } from "m";', 'import { c } from "m";']
        out, _ = ra.resolve_text(conflict(ours, base, theirs))
        self.assertEqual(out, 'import type { A, B } from "m";\nimport { c } from "m";\n')

    def test_default_import_is_real(self):
        base = ['import React from "react";']
        with self.assertRaises(ra.Unresolvable):
            ra.resolve_text(conflict(['import React, { useState } from "react";'], base, ['import React, { useRef } from "react";']))

    def test_import_next_to_code_is_real(self):
        base = ['import { a } from "m";', "const x = 1;"]
        with self.assertRaises(ra.Unresolvable):
            ra.resolve_text(conflict(['import { a, b } from "m";', "const x = 2;"], base, ['import { a, c } from "m";', "const x = 1;"]))


class Parsing(unittest.TestCase):
    def test_non_diff3_style_rejected(self):
        text = "<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> abc (p)\n"
        with self.assertRaises(ra.Unresolvable):
            ra.resolve_text(text)

    def test_multiple_hunks_and_untouched_text(self):
        text = "head\n" + conflict(["a"], [], ["b"]) + "middle\n" + conflict(['import { x } from "m";'], ['import { } from "m";'], ['import { y } from "m";']) + "tail"
        out, rules = ra.resolve_text(text)
        self.assertEqual(rules, ["compose", "imports"])
        self.assertEqual(out, 'head\na\nb\nmiddle\nimport { x, y } from "m";\ntail')


class IndexStages(unittest.TestCase):
    """main() must refuse conflicts that have no three-way hunk to classify."""

    def _repo(self):
        import os
        import subprocess
        import tempfile

        root = tempfile.mkdtemp(prefix="autoresolve-")
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "HOME": root,
        }

        def run(*args, check=True):
            return subprocess.run(["git", "-c", "merge.conflictStyle=diff3", *args], cwd=root, env=env, check=check, capture_output=True, text=True)

        run("init", "-q", "-b", "main")
        pathlib.Path(root, "keep.txt").write_text("keep\n")
        run("add", "keep.txt")
        run("commit", "-q", "-m", "base")
        return root, env, run

    def _autoresolve(self, root, env):
        import subprocess

        return subprocess.run(["python3", str(HERE / "rebase-autoresolve.py")], cwd=root, env=env, capture_output=True, text=True)

    def test_add_add_is_real(self):
        root, env, run = self._repo()
        run("checkout", "-q", "-b", "patch")
        pathlib.Path(root, "new.ts").write_text("export const ours = 1;\n")
        run("add", "new.ts")
        run("commit", "-q", "-m", "patch adds new.ts")
        run("checkout", "-q", "main")
        pathlib.Path(root, "new.ts").write_text("export const upstream = 2;\n")
        run("add", "new.ts")
        run("commit", "-q", "-m", "upstream adds new.ts")
        run("checkout", "-q", "patch")
        self.assertNotEqual(run("rebase", "main", check=False).returncode, 0)
        result = self._autoresolve(root, env)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("both sides added", result.stderr)
        self.assertIn("<<<<<<<", pathlib.Path(root, "new.ts").read_text())
        # still unmerged: nothing was staged as a resolution
        self.assertEqual(run("diff", "--name-only", "--diff-filter=U").stdout.strip(), "new.ts")

    def test_modify_delete_is_real(self):
        root, env, run = self._repo()
        run("checkout", "-q", "-b", "patch")
        pathlib.Path(root, "keep.txt").write_text("keep\npatched\n")
        run("commit", "-q", "-am", "patch edits keep.txt")
        run("checkout", "-q", "main")
        run("rm", "-q", "keep.txt")
        run("commit", "-q", "-m", "upstream deletes keep.txt")
        run("checkout", "-q", "patch")
        self.assertNotEqual(run("rebase", "main", check=False).returncode, 0)
        result = self._autoresolve(root, env)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("deleted by HEAD", result.stderr)
        self.assertEqual(run("diff", "--name-only", "--diff-filter=U").stdout.strip(), "keep.txt")


if __name__ == "__main__":
    unittest.main()
