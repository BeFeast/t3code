#!/usr/bin/env python3
"""OK Labs: resolve the boring rebase conflicts, stop on the real ones.

Run inside a repository whose rebase just stopped on conflicts written in
``diff3`` style (``git -c merge.conflictStyle=diff3 rebase ...``). Every
conflict hunk is classified against its base section and resolved only when
the resolution is mechanical:

* ``compose`` -- neither side rewrote a base line; each side only inserted
  and/or deleted whole lines. Deletions of both sides are applied, insertions
  of both sides are kept (HEAD first, then the patch). This is the classic
  "upstream added a sibling method / removed a neighbouring line" conflict.
* ``imports`` -- the hunk consists solely of named ES ``import { ... } from``
  statements. Specifiers are merged per module: base plus what either side
  added, minus what either side removed.

Anything else (a rewritten line, a refactored block, a non-import statement
next to an import) is a real conflict: the file is left untouched with its
markers and the script exits 1. The caller decides what to do; it never
guesses.

The typecheck after the rebase remains the real safety net: a resolution that
is textually sound but semantically wrong fails there, and the sync stops
without pushing ``main``.

Usage: rebase-autoresolve.py [--summary FILE]
  --summary FILE   append one JSON object per resolved file (for the log/alert)
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field

MARK_OURS = "<<<<<<< "
MARK_BASE = "||||||| "
MARK_SEP = "======="
MARK_THEIRS = ">>>>>>> "

IMPORT_RE = re.compile(
    r'^import\s+(?P<kind>type\s+)?\{(?P<names>[^}]*)\}\s+from\s+(?P<quote>["\'])(?P<module>[^"\']+)(?P=quote);?$'
)
MAX_LINE = 100  # prettier printWidth used by the repository


class Unresolvable(Exception):
    """The hunk is a real conflict; carry the reason for the log."""


@dataclass
class Hunk:
    ours: list[str]
    base: list[str]
    theirs: list[str]
    label_theirs: str = ""
    rule: str = ""
    result: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def split_conflicts(text: str) -> list[object]:
    """Return a list mixing plain strings (unchanged runs) and Hunk objects."""
    lines = text.split("\n")
    out: list[object] = []
    plain: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.startswith(MARK_OURS):
            plain.append(line)
            i += 1
            continue
        if plain:
            out.append("\n".join(plain) + "\n")
            plain = []
        i += 1
        ours: list[str] = []
        while i < n and not lines[i].startswith(MARK_BASE):
            if lines[i].startswith(MARK_SEP) and lines[i].rstrip() == MARK_SEP:
                raise Unresolvable("conflict is not in diff3 style (no base section)")
            ours.append(lines[i])
            i += 1
        if i >= n:
            raise Unresolvable("unterminated conflict: missing base marker")
        i += 1
        base: list[str] = []
        while i < n and lines[i].rstrip() != MARK_SEP:
            base.append(lines[i])
            i += 1
        if i >= n:
            raise Unresolvable("unterminated conflict: missing separator")
        i += 1
        theirs: list[str] = []
        while i < n and not lines[i].startswith(MARK_THEIRS):
            theirs.append(lines[i])
            i += 1
        if i >= n:
            raise Unresolvable("unterminated conflict: missing closing marker")
        label = lines[i][len(MARK_THEIRS):]
        i += 1
        out.append(Hunk(ours=ours, base=base, theirs=theirs, label_theirs=label))
    if plain:
        out.append("\n".join(plain))
    return out


# --------------------------------------------------------------------------
# Rule: compose (insert/delete only on both sides)
# --------------------------------------------------------------------------


def pure_edits(base: list[str], side: list[str]) -> tuple[dict[int, list[str]], set[int]] | None:
    """Insertions keyed by base index and deleted base indices, or None if the
    side rewrote a line (a difflib ``replace``)."""
    matcher = difflib.SequenceMatcher(None, base, side, autojunk=False)
    inserts: dict[int, list[str]] = {}
    deletes: set[int] = set()
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            inserts.setdefault(i1, []).extend(side[j1:j2])
        elif tag == "delete":
            deletes.update(range(i1, i2))
        else:
            return None
    return inserts, deletes


def resolve_compose(hunk: Hunk) -> list[str] | None:
    ours = pure_edits(hunk.base, hunk.ours)
    theirs = pure_edits(hunk.base, hunk.theirs)
    if ours is None or theirs is None:
        return None
    ins_o, del_o = ours
    ins_t, del_t = theirs
    result: list[str] = []
    for index in range(len(hunk.base) + 1):
        result.extend(ins_o.get(index, []))
        result.extend(ins_t.get(index, []))
        if index < len(hunk.base) and index not in del_o and index not in del_t:
            result.append(hunk.base[index])
    return result


# --------------------------------------------------------------------------
# Rule: imports (named ES imports only)
# --------------------------------------------------------------------------


def parse_imports(lines: list[str]) -> dict[tuple[str, bool], list[str]] | None:
    """Map (module, is_type) -> specifier list, or None if the block holds
    anything but named ``import { ... } from "..."`` statements."""
    text = " ".join(part.strip() for part in lines if part.strip())
    if not text:
        return {}
    statements = [s.strip() for s in text.split(";") if s.strip()]
    parsed: dict[tuple[str, bool], list[str]] = {}
    for statement in statements:
        normalized = re.sub(r"\s+", " ", statement)
        match = IMPORT_RE.match(normalized)
        if not match:
            return None
        key = (match.group("module"), match.group("kind") is not None)
        if key in parsed:
            return None  # duplicated statement for one module: not our business
        names = [n.strip() for n in match.group("names").split(",") if n.strip()]
        parsed[key] = names
    return parsed


def merge_names(base: list[str], ours: list[str], theirs: list[str]) -> list[str]:
    removed = (set(base) - set(ours)) | (set(base) - set(theirs))
    result = [n for n in base if n not in removed]
    for side in (ours, theirs):
        for name in side:
            if name not in result and name not in set(base):
                result.append(name)
    return result


def format_import(module: str, is_type: bool, names: list[str]) -> list[str]:
    kind = "type " if is_type else ""
    single = f"import {kind}{{ {', '.join(names)} }} from \"{module}\";"
    if len(single) <= MAX_LINE:
        return [single]
    body = [f"  {name}," for name in names]
    return [f"import {kind}{{", *body, f"}} from \"{module}\";"]


def resolve_imports(hunk: Hunk) -> list[str] | None:
    base = parse_imports(hunk.base)
    ours = parse_imports(hunk.ours)
    theirs = parse_imports(hunk.theirs)
    if base is None or ours is None or theirs is None:
        return None
    keys: list[tuple[str, bool]] = []
    for source in (base, ours, theirs):
        for key in source:
            if key not in keys:
                keys.append(key)
    result: list[str] = []
    for key in keys:
        in_base = key in base
        if in_base and (key not in ours or key not in theirs):
            continue  # one side dropped the whole statement
        names = merge_names(base.get(key, []), ours.get(key, base.get(key, [])), theirs.get(key, base.get(key, [])))
        if not names:
            continue
        result.extend(format_import(key[0], key[1], names))
    return result


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


RULES = (("compose", resolve_compose), ("imports", resolve_imports))


def resolve_hunk(hunk: Hunk) -> None:
    for name, rule in RULES:
        result = rule(hunk)
        if result is not None:
            hunk.rule = name
            hunk.result = result
            return
    raise Unresolvable(
        f"real conflict against {hunk.label_theirs or 'patch'}: "
        f"HEAD {len(hunk.ours)} lines / base {len(hunk.base)} / patch {len(hunk.theirs)}"
    )


def resolve_text(text: str) -> tuple[str, list[str]]:
    parts = split_conflicts(text)
    rules: list[str] = []
    out: list[str] = []
    for part in parts:
        if isinstance(part, Hunk):
            resolve_hunk(part)
            rules.append(part.rule)
            if part.result:
                out.append("\n".join(part.result) + "\n")
        else:
            out.append(part)
    return "".join(out), rules


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def unmerged_stages() -> dict[str, set[int]]:
    """Map each unmerged path to its index stages (1 = base, 2 = HEAD, 3 = patch)."""
    stages: dict[str, set[int]] = {}
    for entry in git("ls-files", "-u", "-z").split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        stages.setdefault(path, set()).add(int(meta.split()[2]))
    return stages


def describe_stages(stages: set[int]) -> str:
    if stages == {2, 3}:
        return "both sides added this file (no common base to merge against)"
    if stages == {1, 2}:
        return "deleted by the patch, modified by HEAD"
    if stages == {1, 3}:
        return "deleted by HEAD, modified by the patch"
    return f"unmerged index stages {sorted(stages)}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--summary", help="append a JSON line per resolved file")
    args = parser.parse_args()

    conflicted = [p for p in git("diff", "--name-only", "--diff-filter=U").split("\n") if p]
    if not conflicted:
        print("autoresolve: no conflicted files", file=sys.stderr)
        return 1
    stages = unmerged_stages()
    patch = ""
    try:
        patch = git("log", "-1", "--format=%h %s", "REBASE_HEAD").strip()
    except subprocess.CalledProcessError:
        pass

    resolved: list[dict[str, object]] = []
    failures: list[str] = []
    for path in conflicted:
        # Only a three-way hunk can be classified. An add/add conflict has no base
        # section, so ``compose`` would splice two unrelated files into one (run 240:
        # our ForgejoSourceControlProvider.ts vs upstream's); a modify/delete has no
        # markers at all and would be staged as-is, silently undoing the deletion.
        if stages.get(path, set()) != {1, 2, 3}:
            failures.append(f"{path}: {describe_stages(stages.get(path, set()))}")
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            new_text, rules = resolve_text(text)
        except (Unresolvable, UnicodeDecodeError) as exc:
            failures.append(f"{path}: {exc}")
            continue
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(new_text)
        git("add", "--", path)
        resolved.append({"patch": patch, "file": path, "rules": rules})
        print(f"autoresolve: {path} <- {', '.join(rules)} ({patch})")

    if args.summary and resolved:
        with open(args.summary, "a", encoding="utf-8") as handle:
            for entry in resolved:
                handle.write(json.dumps(entry) + "\n")

    if failures:
        print(f"autoresolve: REAL conflict in {patch}", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
