#!/usr/bin/env python3
"""OK Labs: the pure parts of the rebase repair lane (see rebase-repair.sh).

Everything in here is a function of its inputs and has no git or network side
effects, so it can be unit-tested without a repository. rebase-repair.sh calls
the subcommands; test_rebase_repair.py calls the functions.

Subcommands:
  prompt   --template FILE --mode conflict|typecheck [--var K=V|K=@file]... [--context FILE]
  packages PATH...                 print "<id>\t<dir>" per affected test package
  markers  PATH...                 list leftover conflict markers, exit 1 if any
  verdict  FILE                    three lines: VERDICT, reason, JSON list of files (from the agent's last message)
  usage    EVENTS.jsonl            token/command totals of a `codex exec --json` run, as JSON
  transcript EVENTS.jsonl          human-readable rendering of a `codex exec --json` run
  tests-summary LOG                "N passed, M skipped" parsed from a vitest log
  range-diff-summary FILE          "= a, ! b, < c, > d" parsed from `git range-diff` output
  receipt  --summary F [--autoresolved F] [--tests F] [--range-diff F] [--run-url URL] [--pushed 0|1] [--test-mode 0|1] [--patches OLD:NEW]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# file -> package mapping (the packages whose suites the gate runs)
# --------------------------------------------------------------------------

# Order is the run order; sequential on purpose (parallel suites OOM the runner).
TEST_PACKAGES: tuple[tuple[str, str], ...] = (
    ("contracts", "packages/contracts"),
    ("shared", "packages/shared"),
    ("client-runtime", "packages/client-runtime"),
    ("server", "apps/server"),
    ("web", "apps/web"),
    ("desktop", "apps/desktop"),
)


def package_for(path: str) -> str | None:
    """Test-package id for a repository path, or None when no suite covers it."""
    normalized = path.strip().lstrip("./")
    for package_id, directory in TEST_PACKAGES:
        if normalized == directory or normalized.startswith(directory + "/"):
            return package_id
    return None


def packages_for(paths: list[str]) -> list[tuple[str, str]]:
    """Unique (id, dir) pairs in canonical run order for the given paths."""
    wanted = {package_for(p) for p in paths}
    return [(pid, d) for pid, d in TEST_PACKAGES if pid in wanted]


# --------------------------------------------------------------------------
# conflict markers
# --------------------------------------------------------------------------

MARKER_RE = re.compile(r"^(<{7}( |$)|>{7}( |$)|\|{7}( |$)|={7}$)")


def find_markers(text: str) -> list[int]:
    """1-based line numbers holding conflict markers.

    A bare ``=======`` is a legitimate setext underline in Markdown, so it only
    counts when the file also carries a ``<<<<<<<``/``>>>>>>>``/``|||||||``
    marker.
    """
    hits: list[tuple[int, bool]] = []
    for number, line in enumerate(text.split("\n"), start=1):
        match = MARKER_RE.match(line)
        if match:
            hits.append((number, line.startswith("=======")))
    if hits and all(is_sep for _, is_sep in hits):
        return []
    return [number for number, _ in hits]


def markers_in_files(paths: list[str]) -> list[str]:
    """``path:line`` entries for every marker in the existing files."""
    found: list[str] = []
    for path in paths:
        file = Path(path)
        if not file.is_file():
            continue  # deleted on purpose (modify/delete resolved as delete)
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        found.extend(f"{path}:{n}" for n in find_markers(text))
    return found


# --------------------------------------------------------------------------
# prompt assembly
# --------------------------------------------------------------------------

SECTION_RE = re.compile(r"^<!--\s*section:\s*([a-z-]+)\s*-->\s*$", re.MULTILINE)
VAR_RE = re.compile(r"\{\{([A-Z_]+)\}\}")
MODES = ("conflict", "typecheck")


def split_sections(template: str) -> dict[str, str]:
    """Map section name -> body for a template split by ``<!-- section: x -->``."""
    sections: dict[str, str] = {}
    matches = list(SECTION_RE.finditer(template))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(template)
        sections[match.group(1)] = template[match.end():end].strip("\n") + "\n"
    return sections


def build_prompt(template: str, mode: str, variables: dict[str, str], context: str = "") -> str:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
    sections = split_sections(template)
    missing = [name for name in ("common", mode) if name not in sections]
    if missing:
        raise ValueError(f"template lacks section(s): {', '.join(missing)}")
    text = sections["common"].rstrip("\n") + "\n\n" + sections[mode].rstrip("\n") + "\n"
    if context.strip():
        text += "\n## Context gathered by the driver\n\n" + context.rstrip("\n") + "\n"

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in variables:
            raise KeyError(f"prompt variable {{{{{key}}}}} has no value")
        return variables[key]

    return VAR_RE.sub(substitute, text)


# --------------------------------------------------------------------------
# agent output
# --------------------------------------------------------------------------

VERDICTS = ("RESOLVED", "SKIP", "GIVEUP")
VERDICT_LINE_RE = re.compile(r"^\s*\**VERDICT\**\s*[:=]\s*\**\s*(RESOLVED|SKIP|GIVEUP)\**\s*(?:[-—:]\s*(.*))?$", re.IGNORECASE)


def parse_verdict(text: str) -> tuple[str, str, list[str]]:
    """(verdict, reason, files) from the agent's final message.

    Accepts the JSON object the output schema asks for (possibly wrapped in a
    code fence) or a plain ``VERDICT: X — reason`` line; returns ``NONE`` when
    neither is present so the driver can fall back to its own checks.
    """
    stripped = text.strip()
    candidates = [stripped]
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if fence:
        candidates.insert(0, fence.group(1))
    brace = stripped.rfind("{")
    if brace >= 0:
        candidates.append(stripped[brace:])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and str(data.get("verdict", "")).upper() in VERDICTS:
            files = data.get("files") or []
            if not isinstance(files, list):
                files = []
            return (
                str(data["verdict"]).upper(),
                str(data.get("reason") or "").strip(),
                [str(f) for f in files],
            )
    for line in reversed(stripped.split("\n")):
        match = VERDICT_LINE_RE.match(line)
        if match:
            return match.group(1).upper(), (match.group(2) or "").strip(), []
    return "NONE", "", []


def iter_events(text: str):
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue


def summarize_usage(events_text: str) -> dict[str, int]:
    """Totals over every ``turn.completed`` usage block plus item counts."""
    totals = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "turns": 0,
        "commands": 0,
        "messages": 0,
    }
    for event in iter_events(events_text):
        kind = event.get("type")
        if kind == "turn.completed":
            totals["turns"] += 1
            for key, value in (event.get("usage") or {}).items():
                if key in totals and isinstance(value, int):
                    totals[key] += value
        elif kind == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "command_execution":
                totals["commands"] += 1
            elif item.get("type") == "agent_message":
                totals["messages"] += 1
    return totals


def render_transcript(events_text: str, max_output_lines: int = 20) -> str:
    """Readable log of what the agent said and ran."""
    out: list[str] = []
    for event in iter_events(events_text):
        kind = event.get("type")
        if kind == "thread.started":
            out.append(f"[thread {event.get('thread_id', '?')}]")
        elif kind == "item.completed":
            item = event.get("item") or {}
            item_type = item.get("type")
            if item_type == "agent_message":
                out.append("agent: " + (item.get("text") or "").rstrip())
            elif item_type == "reasoning":
                text = (item.get("text") or "").strip()
                if text:
                    out.append("thinking: " + text.split("\n")[0][:200])
            elif item_type == "command_execution":
                out.append(f"$ {item.get('command', '')}  -> exit {item.get('exit_code')}")
                output = (item.get("aggregated_output") or "").rstrip("\n")
                if output:
                    lines = output.split("\n")
                    shown = lines[:max_output_lines]
                    out.extend("    " + l for l in shown)
                    if len(lines) > max_output_lines:
                        out.append(f"    ... ({len(lines) - max_output_lines} more lines)")
            elif item_type in ("file_change", "patch"):
                out.append(f"edit: {json.dumps(item.get('changes') or item.get('path') or item)[:300]}")
        elif kind == "turn.completed":
            usage = event.get("usage") or {}
            out.append(
                "[turn done: in={input_tokens} cached={cached_input_tokens} out={output_tokens} reasoning={reasoning_output_tokens}]".format(
                    **{k: usage.get(k, 0) for k in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")}
                )
            )
        elif kind == "error":
            out.append("ERROR: " + json.dumps(event)[:500])
    return "\n".join(out) + ("\n" if out else "")


# --------------------------------------------------------------------------
# gate output parsing
# --------------------------------------------------------------------------

TESTS_LINE_RE = re.compile(r"^\s*Tests\s+(.+?)\s*\(\d+\)\s*$", re.MULTILINE)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
RANGE_DIFF_RE = re.compile(r"^\s*(?:\d+|-):\s+\S+\s+([=!<>])\s+", re.MULTILINE)


def tests_summary(log: str) -> str:
    """Last vitest ``Tests N passed | M skipped (T)`` line, normalised.

    ``vp test run`` colours its summary even without a TTY, so the escape
    sequences are stripped first.
    """
    lines = TESTS_LINE_RE.findall(ANSI_RE.sub("", log))
    if not lines:
        return "no test summary found"
    return re.sub(r"\s*\|\s*", ", ", lines[-1]).strip()


def range_diff_summary(text: str) -> dict[str, int]:
    counts = {"=": 0, "!": 0, "<": 0, ">": 0}
    for symbol in RANGE_DIFF_RE.findall(ANSI_RE.sub("", text)):
        counts[symbol] += 1
    return counts


def format_range_diff(counts: dict[str, int], patches: tuple[int, int] | None = None) -> str:
    text = (
        f"{counts.get('=', 0)} unchanged, {counts.get('!', 0)} modified, "
        f"{counts.get('<', 0)} dropped, {counts.get('>', 0)} added"
    )
    if patches:
        rows = sum(counts.values())
        expected = max(patches)
        if rows < expected:
            text += f" (PARTIAL: {rows} of {expected} rows — read the run log)"
    return text


# --------------------------------------------------------------------------
# receipt
# --------------------------------------------------------------------------


def read_jsonl(path: str | None) -> list[dict]:
    if not path or not Path(path).is_file():
        return []
    return [e for e in iter_events(Path(path).read_text(encoding="utf-8")) if isinstance(e, dict)]


def build_receipt(
    repairs: list[dict],
    autoresolved: list[dict],
    tests: dict[str, str],
    range_diff: dict[str, int] | None,
    run_url: str,
    pushed: bool,
    test_mode: bool,
    patches: tuple[int, int] | None = None,
) -> str:
    """Telegram text for a run in which the repair lane did the work."""
    header = "t3code sync REPAIRED by agent"
    if test_mode:
        header += " (TEST MODE, push disabled)"
    elif pushed:
        header += " — gate green, main pushed"
    lines = [header]
    stops = [r for r in repairs if r.get("mode") == "conflict"]
    typechecks = [r for r in repairs if r.get("mode") == "typecheck"]
    if stops:
        lines.append(f"patches repaired: {len(stops)}")
        for entry in stops:
            verdict = entry.get("verdict", "?")
            label = "dropped (superseded)" if verdict == "SKIP" else "resolved"
            lines.append(f"- {entry.get('patch', '?')[:70]} -> {label}")
            for file in entry.get("files") or []:
                lines.append(f"    {file}")
            reason = (entry.get("reason") or "").strip()
            if reason:
                lines.append(f"    why: {reason[:200]}")
    for entry in typechecks:
        lines.append(f"typecheck repair: {entry.get('verdict', '?')} ({', '.join(entry.get('files') or [])[:200]})")
        reason = (entry.get("reason") or "").strip()
        if reason:
            lines.append(f"    why: {reason[:200]}")
    if autoresolved:
        lines.append(f"mechanical auto-resolves: {len(autoresolved)}")
        for entry in autoresolved:
            lines.append(f"- {entry.get('file')} <- {','.join(entry.get('rules') or [])}")
    iterations = sum(int(r.get("iteration_calls", 1)) for r in repairs)
    seconds = sum(int(r.get("seconds", 0)) for r in repairs)
    usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}
    for entry in repairs:
        for key in usage:
            usage[key] += int((entry.get("usage") or {}).get(key, 0))
    lines.append(
        f"agent: {iterations} call(s), {seconds // 60}m{seconds % 60:02d}s, "
        f"tokens in {usage['input_tokens']} (cached {usage['cached_input_tokens']}) / out {usage['output_tokens']} "
        f"(reasoning {usage['reasoning_output_tokens']})"
    )
    if tests:
        lines.append("gate: " + "; ".join(f"{k}: {v}" for k, v in tests.items()))
    if patches:
        lines.append(f"stack: {patches[0]} -> {patches[1]} patches")
    if range_diff is not None:
        lines.append("range-diff old stack vs new: " + format_range_diff(range_diff, patches))
    if run_url:
        lines.append(run_url)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def read_var(spec: str) -> tuple[str, str]:
    key, _, value = spec.partition("=")
    if not key or not _:
        raise SystemExit(f"--var expects KEY=VALUE, got {spec!r}")
    if value.startswith("@"):
        value = Path(value[1:]).read_text(encoding="utf-8").rstrip("\n")
    return key, value


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prompt")
    p.add_argument("--template", required=True)
    p.add_argument("--mode", required=True, choices=MODES)
    p.add_argument("--var", action="append", default=[])
    p.add_argument("--context")

    p = sub.add_parser("packages")
    p.add_argument("paths", nargs="*")

    p = sub.add_parser("markers")
    p.add_argument("paths", nargs="*")

    p = sub.add_parser("verdict")
    p.add_argument("file")

    p = sub.add_parser("usage")
    p.add_argument("file")

    p = sub.add_parser("transcript")
    p.add_argument("file")

    p = sub.add_parser("tests-summary")
    p.add_argument("file")

    p = sub.add_parser("range-diff-summary")
    p.add_argument("file")

    p = sub.add_parser("receipt")
    p.add_argument("--summary", required=True)
    p.add_argument("--autoresolved")
    p.add_argument("--tests")
    p.add_argument("--range-diff")
    p.add_argument("--run-url", default="")
    p.add_argument("--pushed", default="0")
    p.add_argument("--test-mode", default="0")
    p.add_argument("--patches", default="", help="OLD:NEW patch counts, e.g. 55:55")

    args = parser.parse_args(argv)

    if args.command == "prompt":
        variables = dict(read_var(v) for v in args.var)
        context = Path(args.context).read_text(encoding="utf-8") if args.context else ""
        try:
            sys.stdout.write(build_prompt(Path(args.template).read_text(encoding="utf-8"), args.mode, variables, context))
        except (KeyError, ValueError) as exc:
            print(f"prompt: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.command == "packages":
        paths = args.paths or [l.strip() for l in sys.stdin.read().split("\n") if l.strip()]
        for package_id, directory in packages_for(paths):
            print(f"{package_id}\t{directory}")
        return 0
    if args.command == "markers":
        paths = args.paths or [l.strip() for l in sys.stdin.read().split("\n") if l.strip()]
        found = markers_in_files(paths)
        for hit in found:
            print(hit)
        return 1 if found else 0
    if args.command == "verdict":
        verdict, reason, files = parse_verdict(Path(args.file).read_text(encoding="utf-8", errors="replace"))
        # Three lines, not one tab-separated line: an empty reason must not shift the columns.
        print(verdict)
        print(" ".join(reason.split()))
        print(json.dumps(files))
        return 0
    if args.command == "usage":
        print(json.dumps(summarize_usage(Path(args.file).read_text(encoding="utf-8", errors="replace"))))
        return 0
    if args.command == "transcript":
        sys.stdout.write(render_transcript(Path(args.file).read_text(encoding="utf-8", errors="replace")))
        return 0
    if args.command == "tests-summary":
        print(tests_summary(Path(args.file).read_text(encoding="utf-8", errors="replace")))
        return 0
    if args.command == "range-diff-summary":
        print(format_range_diff(range_diff_summary(Path(args.file).read_text(encoding="utf-8", errors="replace"))))
        return 0
    if args.command == "receipt":
        tests: dict[str, str] = {}
        if args.tests and Path(args.tests).is_file():
            for line in Path(args.tests).read_text(encoding="utf-8").split("\n"):
                key, _, value = line.partition("\t")
                if key and value:
                    tests[key] = value
        range_diff = None
        if args.range_diff and Path(args.range_diff).is_file():
            range_diff = range_diff_summary(Path(args.range_diff).read_text(encoding="utf-8", errors="replace"))
        patches = None
        if re.fullmatch(r"\d+:\d+", args.patches):
            old_n, new_n = args.patches.split(":")
            patches = (int(old_n), int(new_n))
        print(
            build_receipt(
                read_jsonl(args.summary),
                read_jsonl(args.autoresolved),
                tests,
                range_diff,
                args.run_url,
                args.pushed == "1",
                args.test_mode == "1",
                patches,
            )
        )
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
