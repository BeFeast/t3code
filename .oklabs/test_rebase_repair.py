"""Unit tests for the pure parts of the repair lane (rebase-repair-lib.py).

Run: python3 -m unittest discover -s .oklabs -p test_rebase_repair.py -v
"""

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("rr", HERE / "rebase-repair-lib.py")
rr = importlib.util.module_from_spec(spec)
sys.modules["rr"] = rr
spec.loader.exec_module(rr)

TEMPLATE = (HERE / "rebase-repair-prompt.md").read_text(encoding="utf-8")
SCHEMA = json.loads((HERE / "rebase-repair-schema.json").read_text(encoding="utf-8"))


class PackageMapping(unittest.TestCase):
    def test_maps_apps_and_packages(self):
        self.assertEqual(rr.package_for("apps/web/src/components/Sidebar.tsx"), "web")
        self.assertEqual(rr.package_for("apps/server/src/http.ts"), "server")
        self.assertEqual(rr.package_for("apps/desktop/src/ElectronShell.ts"), "desktop")
        self.assertEqual(rr.package_for("packages/contracts/src/editor.ts"), "contracts")
        self.assertEqual(rr.package_for("packages/shared/src/sourceControl.test.ts"), "shared")
        self.assertEqual(rr.package_for("packages/client-runtime/src/x.ts"), "client-runtime")

    def test_uncovered_paths_map_to_nothing(self):
        for path in (".forgejo/workflows/sync.yml", "apps/marketing/index.ts", "packages/ssh/x.ts", "docs/a.md", "apps/webby/x.ts"):
            self.assertIsNone(rr.package_for(path), path)

    def test_run_order_and_dedup(self):
        paths = ["apps/web/a.tsx", "apps/desktop/b.ts", "apps/web/c.ts", "packages/contracts/d.ts", "README.md"]
        self.assertEqual(
            rr.packages_for(paths),
            [("contracts", "packages/contracts"), ("web", "apps/web"), ("desktop", "apps/desktop")],
        )
        self.assertEqual(rr.packages_for([]), [])


class MarkerCheck(unittest.TestCase):
    def test_finds_all_marker_kinds(self):
        text = "a\n<<<<<<< HEAD\nb\n||||||| parent of 123\nc\n=======\nd\n>>>>>>> 123 (patch)\ne\n"
        self.assertEqual(rr.find_markers(text), [2, 4, 6, 8])

    def test_setext_underline_is_not_a_marker(self):
        self.assertEqual(rr.find_markers("Title\n=======\nbody\n"), [])

    def test_separator_counts_once_a_real_marker_is_present(self):
        self.assertEqual(rr.find_markers("<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> p\n"), [1, 3, 5])

    def test_indented_or_longer_runs_are_not_markers(self):
        self.assertEqual(rr.find_markers("  <<<<<<< not a marker\n<<<<<<<< eight\n========\n"), [])

    def test_files_missing_or_binary_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            clean = pathlib.Path(tmp, "clean.ts")
            clean.write_text("const a = 1;\n")
            dirty = pathlib.Path(tmp, "dirty.ts")
            dirty.write_text("<<<<<<< HEAD\nconst a = 1;\n=======\nconst a = 2;\n>>>>>>> x\n")
            binary = pathlib.Path(tmp, "blob.bin")
            binary.write_bytes(b"\xff\xfe<<<<<<< \x00")
            found = rr.markers_in_files([str(clean), str(dirty), str(binary), str(pathlib.Path(tmp, "gone.ts"))])
            self.assertEqual(found, [f"{dirty}:1", f"{dirty}:3", f"{dirty}:5"])


class PromptAssembly(unittest.TestCase):
    VARS = {
        "PATCH": "abc1234 oklabs: width toggle",
        "PATCH_INDEX": "3",
        "PATCH_COUNT": "55",
        "OLD_BASE": "1111111",
        "NEW_BASE": "2222222",
        "FILES": "- `apps/web/x.tsx` — modified on both sides",
    }

    def test_conflict_prompt_has_playbook_rules_and_stop(self):
        prompt = rr.build_prompt(TEMPLATE, "conflict", self.VARS, "### Patch being applied\n    abc\n")
        for phrase in (
            "One patch at a time",
            "keep both sides",
            "Adapt to upstream refactors",
            "superseded by upstream is dropped",
            "width toggle",
            "sub-project tags",
            "Reply",
            "Zed SSH",
            "LinkFormat",
            "Never add features",
            "Typecheck is the safety net",
            "`.github/workflows/`",
        ):
            self.assertIn(phrase, prompt, phrase)
        self.assertIn("Patch 3 of 55 in the stack: `abc1234 oklabs: width toggle`", prompt)
        self.assertIn("from upstream `1111111` onto `2222222`", prompt)
        self.assertIn("- `apps/web/x.tsx` — modified on both sides", prompt)
        self.assertIn("## Context gathered by the driver", prompt)
        self.assertIn("### Patch being applied", prompt)
        self.assertNotIn("{{", prompt)
        self.assertNotIn("pnpm typecheck` fails", prompt)  # typecheck section excluded

    def test_typecheck_prompt_excludes_conflict_section(self):
        variables = {"OLD_BASE": "a", "NEW_BASE": "b", "ERRORS": "apps/server/src/x.ts(1,1): error TS2339: nope"}
        prompt = rr.build_prompt(TEMPLATE, "typecheck", variables)
        self.assertIn("error TS2339", prompt)
        self.assertIn("pnpm typecheck` fails", prompt)
        self.assertNotIn("The mechanical resolver refused", prompt)
        self.assertNotIn("## Context gathered by the driver", prompt)
        self.assertNotIn("{{", prompt)

    def test_missing_variable_is_an_error(self):
        with self.assertRaises(KeyError):
            rr.build_prompt(TEMPLATE, "conflict", {k: v for k, v in self.VARS.items() if k != "FILES"})

    def test_unknown_mode_and_missing_section(self):
        with self.assertRaises(ValueError):
            rr.build_prompt(TEMPLATE, "lint", self.VARS)
        with self.assertRaises(ValueError):
            rr.build_prompt("<!-- section: common -->\nx\n", "conflict", self.VARS)

    def test_final_answer_matches_schema_verdicts(self):
        for verdict in SCHEMA["properties"]["verdict"]["enum"]:
            self.assertIn(verdict, TEMPLATE)
        self.assertEqual(set(SCHEMA["required"]), {"verdict", "reason", "files"})


class VerdictParsing(unittest.TestCase):
    def test_json_object(self):
        text = '{"verdict": "RESOLVED", "reason": "kept both", "files": ["apps/web/a.tsx"]}'
        self.assertEqual(rr.parse_verdict(text), ("RESOLVED", "kept both", ["apps/web/a.tsx"]))

    def test_fenced_json_with_prose(self):
        text = 'Done.\n```json\n{"verdict":"SKIP","reason":"upstream #11436 ships it","files":[]}\n```\n'
        self.assertEqual(rr.parse_verdict(text), ("SKIP", "upstream #11436 ships it", []))

    def test_plain_verdict_line(self):
        self.assertEqual(rr.parse_verdict("I merged them.\nVERDICT: RESOLVED — both sides kept"), ("RESOLVED", "both sides kept", []))
        self.assertEqual(rr.parse_verdict("**VERDICT:** GIVEUP - unclear intent")[0], "GIVEUP")

    def test_no_verdict(self):
        self.assertEqual(rr.parse_verdict("I could not decide."), ("NONE", "", []))
        self.assertEqual(rr.parse_verdict(""), ("NONE", "", []))
        self.assertEqual(rr.parse_verdict('{"verdict": "MAYBE"}'), ("NONE", "", []))


class CodexEvents(unittest.TestCase):
    EVENTS = "\n".join(
        json.dumps(e)
        for e in [
            {"type": "thread.started", "thread_id": "t1"},
            {"type": "item.completed", "item": {"id": "i0", "type": "agent_message", "text": "Looking."}},
            {"type": "item.completed", "item": {"id": "i1", "type": "command_execution", "command": "git diff", "aggregated_output": "a\nb\n", "exit_code": 0}},
            {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 7, "reasoning_output_tokens": 3}},
            {"type": "turn.completed", "usage": {"input_tokens": 50, "cached_input_tokens": 0, "output_tokens": 5, "reasoning_output_tokens": 0}},
            {"type": "item.completed", "item": {"id": "i2", "type": "agent_message", "text": "{\"verdict\":\"RESOLVED\"}"}},
        ]
    )

    def test_usage_totals(self):
        usage = rr.summarize_usage(self.EVENTS + "\nnot json\n")
        self.assertEqual(usage["input_tokens"], 150)
        self.assertEqual(usage["cached_input_tokens"], 40)
        self.assertEqual(usage["output_tokens"], 12)
        self.assertEqual(usage["turns"], 2)
        self.assertEqual(usage["commands"], 1)
        self.assertEqual(usage["messages"], 2)

    def test_transcript_rendering(self):
        text = rr.render_transcript(self.EVENTS)
        self.assertIn("[thread t1]", text)
        self.assertIn("agent: Looking.", text)
        self.assertIn("$ git diff  -> exit 0", text)
        self.assertIn("    a", text)
        self.assertIn("[turn done: in=100 cached=40 out=7 reasoning=3]", text)


class GateParsing(unittest.TestCase):
    def test_tests_summary(self):
        log = "stuff\n Test Files  381 passed (381)\n      Tests  4925 passed | 1 skipped (4926)\n   Duration  120s\n"
        self.assertEqual(rr.tests_summary(log), "4925 passed, 1 skipped")
        self.assertEqual(rr.tests_summary("      Tests  418 passed (418)\n"), "418 passed")
        self.assertEqual(rr.tests_summary("nothing here"), "no test summary found")

    def test_range_diff_summary(self):
        text = (
            "1:  aaaaaaa = 1:  bbbbbbb oklabs: one\n"
            "2:  ccccccc ! 2:  ddddddd oklabs: two\n"
            "3:  eeeeeee < -:  ------- feat: dropped\n"
            "-:  ------- > 3:  fffffff oklabs: repair\n"
            "    @@ hunk noise = not a row\n"
        )
        self.assertEqual(rr.range_diff_summary(text), {"=": 1, "!": 1, "<": 1, ">": 1})
        self.assertEqual(rr.format_range_diff({"=": 47, "!": 7, "<": 2, ">": 0}), "47 unchanged, 7 modified, 2 dropped, 0 added")


class Receipt(unittest.TestCase):
    def test_receipt_lists_everything(self):
        repairs = [
            {"mode": "conflict", "patch": "db4e1a02c fix(editor): support Zed SSH deep links", "verdict": "RESOLVED",
             "files": ["apps/desktop/src/ElectronShell.ts"], "reason": "kept both", "seconds": 300,
             "usage": {"input_tokens": 1000, "cached_input_tokens": 200, "output_tokens": 50, "reasoning_output_tokens": 10}, "iteration": 1},
            {"mode": "conflict", "patch": "4cd2bcd4f feat(source-control): add Forgejo provider", "verdict": "SKIP",
             "files": [], "reason": "upstream #11436 ships Forgejo", "seconds": 120, "usage": {}, "iteration": 2},
            {"mode": "typecheck", "patch": "typecheck", "verdict": "RESOLVED", "files": ["apps/web/x.tsx"], "reason": "renamed prop", "seconds": 90, "usage": {"input_tokens": 10}, "iteration": 3},
        ]
        autoresolved = [{"file": "apps/web/Sidebar.tsx", "rules": ["compose"], "patch": "x"}]
        tests = {"typecheck": "ok", "lint": "ok", "web": "4925 passed, 1 skipped"}
        text = rr.build_receipt(repairs, autoresolved, tests, {"=": 50, "!": 4, "<": 1, ">": 1}, "https://run/1", True, False)
        self.assertIn("REPAIRED by agent — gate green, main pushed", text)
        self.assertIn("patches repaired: 2", text)
        self.assertIn("Zed SSH deep links -> resolved", text)
        self.assertIn("Forgejo provider -> dropped (superseded)", text)
        self.assertIn("    apps/desktop/src/ElectronShell.ts", text)
        self.assertIn("typecheck repair: RESOLVED (apps/web/x.tsx)", text)
        self.assertIn("mechanical auto-resolves: 1", text)
        self.assertIn("agent: 3 call(s), 8m30s, tokens in 1010 (cached 200) / out 50 (reasoning 10)", text)
        self.assertIn("gate: typecheck: ok; lint: ok; web: 4925 passed, 1 skipped", text)
        self.assertIn("range-diff old stack vs new: 50 unchanged, 4 modified, 1 dropped, 1 added", text)
        self.assertTrue(text.endswith("https://run/1"))

    def test_receipt_test_mode_header(self):
        text = rr.build_receipt([], [], {}, None, "", False, True)
        self.assertTrue(text.startswith("t3code sync REPAIRED by agent (TEST MODE, push disabled)"))
        self.assertNotIn("main pushed", text)


if __name__ == "__main__":
    unittest.main()
