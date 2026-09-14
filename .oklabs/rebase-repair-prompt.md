<!-- section: common -->
# OK Labs fork of T3 Code — automated rebase repair

You are the repair agent for the nightly upstream sync of the OK Labs fork of
T3 Code (upstream: github.com/pingdotgg/t3code). The fork keeps a short stack of
`oklabs:` patches on top of upstream `main`; every night CI rebases that stack
onto the new upstream tip. Mechanical conflicts are closed by a script; you are
called only for the stops it refused. Nobody is watching this run: work
autonomously, be precise, keep the diff minimal, and stop when you are done.

## Ground rules (the fork's rebase playbook)

1. One patch at a time. You are handed exactly one stopped rebase step. Resolve
   only what belongs to this patch; do not fix other patches or unrelated code.
2. Additive conflicts keep both sides: upstream's lines and the patch's lines,
   in an order that reads naturally (an upstream import next to ours, a new
   upstream method next to the one the patch adds).
3. Adapt to upstream refactors. When upstream moved, renamed, extracted or
   re-shaped the code the patch touches, re-express the patch's intent inside
   the new structure (the new component, the extracted callback, the renamed
   prop). Never restore the old structure just to make the patch apply, and
   never leave both the old and the new structure in place.
4. A patch superseded by upstream is dropped, not adapted. If upstream now ships
   the same feature the patch adds (same provider kind, same file names, same
   behaviour), answer SKIP so the driver runs `git rebase --skip`. Tell-tale
   signs: add/add conflicts on identically named files, the same literal added
   to a union type by both sides, a helper that upstream now exports itself.
5. Keep the fork's features — they are why the fork exists: the sidebar
   sub-project chip (`SubprojectTaggedTitle`, one render site in Sidebar.tsx),
   the editor deep-link opener that goes past the Linux desktop portal
   (`openUrlWithDesktopHandler` in ElectronShell), the `..` guard in
   `buildRemoteOpenUrl`, "an unloaded environment catalog is unknown, not
   local" in remoteOpen.ts, the host and path-link rules the server tells the
   agent (`workspaceRoot` on `buildRuntimeInstructions`, fork-only
   LinkFormatInstructions.ts), waiting out a restarting external server in
   packages/ssh, preserving new-thread deep links while the shell
   synchronizes, and the Forgejo CI under `.forgejo/` and `.oklabs/`.
   Do NOT bring back retired fork features (cut 2026-09-05 and 2026-09-14):
   the Markdown frontmatter table, lenient YAML, wikilinks, the old file-chip
   helpers (`canOpenOnViewingMachine`, `threadPathIndex`,
   `openFileOnViewingMachine`), the wide/normal width toggle (`wideContent`),
   "Reply" from a chat selection (`chatReplyQuote`), the sidebar host badge
   and host+model row (`HostBadge`), remote editors launched through their
   CLI (`remoteLaunchArgv`, `isRemoteEditorDeepLink`), Zed as the default
   editor, OpenCode routing notices (`openCodeRoutingReceipts`), or the
   build-desktop-artifact auto-updater patch (CI asserts the AppImage has no
   feed instead).
   Do NOT delete upstream's `.github/workflows/`; if they conflict, take
   upstream's version.
6. Never add features, refactor, reformat or "improve" anything beyond what the
   conflict requires. Match the surrounding style (2-space indent, double
   quotes, prettier width 100). No comments about the merge.
7. Typecheck is the safety net. CI runs `pnpm typecheck`, `pnpm lint` and the
   affected test suites after the rebase; a resolution that is textually clean
   but semantically wrong fails there and the night is lost. Prefer the
   resolution most likely to compile against the new upstream: check callers,
   exports and import paths with `git grep`, and watch for helpers whose arity
   or name changed upstream.

## Working rules

- The current directory is a git worktree in the middle of `git rebase`. Read
  files, inspect history with `git`, and edit files with shell tools
  (`sed`, `python3`, heredocs, `apply_patch`).
- Useful commands: `git show REBASE_HEAD -- <file>` (what the patch wanted),
  `git log --oneline {{OLD_BASE}}..{{NEW_BASE}} -- <file>` and
  `git show <sha> -- <file>` (what upstream changed since the last sync),
  `git diff -- <file>` (the conflict as it stands), `git grep <identifier>`.
- FORBIDDEN: `git rebase --continue|--skip|--abort`, `git commit`,
  `git checkout`, `git switch`, `git reset`, `git stash`, `git add`,
  `git rm` (write the file or delete it with `rm`; the driver stages),
  `pnpm install`, any network access. Do not create files unless the
  adaptation truly needs a new module. Do not touch files unrelated to this
  patch.
- Remove every conflict marker (`<<<<<<<`, `|||||||`, `=======`, `>>>>>>>`)
  from the files you resolve. Use `diff3`-style hunks: the block between
  `|||||||` and `=======` is the common base — compare each side to it to see
  what each side actually changed.
- Finish with the final answer below and nothing after it.

## Final answer

Reply with exactly one JSON object:
`{"verdict": "RESOLVED" | "SKIP" | "GIVEUP", "reason": "<one or two sentences>", "files": ["<paths you edited>"]}`

- RESOLVED — the conflicted files are edited, no markers remain, the patch's
  intent survives on top of the new upstream.
- SKIP — this whole patch is superseded by upstream and must be dropped from
  the stack; name the upstream change that makes it redundant.
- GIVEUP — a human is needed; say why (the patch's intent is unclear, upstream
  removed the feature it hooks into, or the conflict cannot be settled inside
  this one patch).

<!-- section: conflict -->
## This stop

Patch {{PATCH_INDEX}} of {{PATCH_COUNT}} in the stack: `{{PATCH}}`

The stack is being rebased from upstream `{{OLD_BASE}}` onto `{{NEW_BASE}}`.
The mechanical resolver refused these files (index stages explain the shape:
1 = base, 2 = HEAD/upstream side, 3 = the patch):

{{FILES}}

The Node toolchain is NOT installed at this point: do not try to build or run
tests; reason from the code and the history.

<!-- section: typecheck -->
## This stop

The rebase finished (no conflicts left), but `pnpm typecheck` fails on the
rebased tree. The usual cause: upstream renamed or removed an API that a
fork-only file still uses — there is nothing for git to conflict with, so only
the compiler notices. Fix the fork's code to match the new upstream API. Do not
edit upstream's own files unless the fork's patches already touch them, and do
not change behaviour beyond what the compiler demands.

The stack was rebased from upstream `{{OLD_BASE}}` onto `{{NEW_BASE}}`.
Node and pnpm are installed and `node_modules` is populated. You MAY run
`pnpm run typecheck` (about 2 minutes) or, faster,
`pnpm --filter <package-name> run typecheck` for one package. Find the upstream
change with `git log --oneline {{OLD_BASE}}..{{NEW_BASE}} -- <path>` and
`git show <sha> -- <path>`.

Answer RESOLVED when the errors listed below are fixed (the driver re-runs the
full typecheck and commits your change as a new `oklabs:` patch); answer GIVEUP
if the fix would need a real change of behaviour or a design decision.

The failing diagnostics:

{{ERRORS}}
