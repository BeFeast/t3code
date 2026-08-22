/**
 * File-path formatting rules and the session host, appended to the runtime
 * instructions every provider adapter builds (`RuntimeInstructions.ts`).
 *
 * The chat renderer turns path-shaped text into clickable chips using pure
 * string heuristics (`apps/web/src/markdown-links.ts`) with no filesystem
 * check, so a path written in the wrong shape either renders as inert text or
 * renders as a chip that fails on click. Which shape is right depends on the
 * session's workspace root, which the model cannot know unless it is told.
 *
 * The Claude Code preset ships its own rule -- "Reference code as
 * `file_path:line_number`" -- that is correct for a repo-rooted project and
 * wrong for anything outside the root. This block runs after it and is
 * deliberately specific enough to override it.
 *
 * Wiring: `buildRuntimeInstructions({ ..., workspaceRoot })` appends the block
 * after upstream's own `<runtime_info>` and PR-linking blocks. Only the Claude
 * adapter passes `workspaceRoot` today; any other adapter gets the same block
 * by passing its session cwd, with no change here.
 *
 * Keep it short. It is prepended to every turn's cached prefix, and every line
 * here is a line the model reads before it reads the user.
 */

const LINK_FORMAT_RULES = `## File paths in your replies

Paths you write are rendered as clickable chips. Use these forms so they resolve:

- Inside the workspace root: a path relative to the root with at least one
  directory segment, optionally \`:line\` or \`:line:column\`.
  Example: \`Areas/pomoi/_index.md:42\`. Opens in the side panel.
- Never repeat the root's own folder name at the front of a relative path. If
  the root ends in \`/Dev\`, the file is \`Areas/x.md\`, never \`Dev/Areas/x.md\` --
  that resolves to \`Dev/Dev/Areas/x.md\` and does not exist.
- Outside the workspace root: the full absolute path.
  Example: \`/home/god/src/t3code/package.json\`.
- Files in a code repository are almost always outside this root, even when the
  conversation is about that repository. \`docs/architecture.md\` is a path
  inside the repo, not inside the workspace -- write
  \`/home/god/src/PROJECT/docs/architecture.md\`. If you are unsure whether a
  path is under the root above, write it absolute.
- A path containing a space is never a chip inside backticks. Write it as a
  markdown link with an angle-bracketed destination instead:
  [AGENTS.md](</home/god/Obsidian Vault/AGENTS.md>).
- A bare filename with no directory segment and no line number is not a chip.
  Either give it a directory prefix or accept that it stays plain text.

Do not link a path that does not exist. A chip whose target is missing fails on
click and the failure is cached against that path.`;

/**
 * The host line comes from `setSessionHostLabel`, called once at startup with
 * the same descriptor label the UI shows. The agent needs it to decide whether
 * a checkout is local or has to be reached over ssh, and it cannot infer it:
 * today that fact is carried by a suffix people hand-type into the project
 * name ("Dev (maestro)"), which is wrong the moment a project moves and
 * invisible if anyone cleans the name up. Stating it here makes the signal
 * explicit instead of inferred from a label. Omitted when unknown, for the
 * same reason as the root.
 */
let sessionHostLabel: string | undefined;

/**
 * Records the machine this server runs on, once, at startup.
 *
 * Called from `ServerEnvironment` where the label is computed, so the agent
 * text and the UI can never disagree about the host's name. The host is a
 * property of the process, not of a request, so it is set here rather than
 * threaded through the adapter -- which would mean adding a service to the
 * Claude driver's environment and stubbing it in every upstream test that
 * builds one. Unset is a supported state: the host line is simply omitted,
 * exactly as it was before this existed.
 */
export const setSessionHostLabel = (label: string | undefined): void => {
  const trimmed = label?.trim();
  sessionHostLabel = trimmed !== undefined && trimmed.length > 0 ? trimmed : undefined;
};

/** Visible for tests. */
export const getSessionHostLabel = (): string | undefined => sessionHostLabel;

/**
 * The block to append after the shared runtime instructions, or undefined when
 * no workspace root is bound: a thread can be created before a workspace is
 * bound, and then the relative-vs-absolute rule has no anchor and stating a
 * root would be a lie, so the whole block is omitted rather than shipped
 * half-true.
 */
export const linkFormatInstructions = (workspaceRoot: string | undefined): string | undefined => {
  if (workspaceRoot === undefined || workspaceRoot.length === 0) return undefined;
  const host =
    sessionHostLabel === undefined
      ? ""
      : `\nThis session's server runs on ${sessionHostLabel}. Every path above is on that machine; a checkout on any other host is reachable only over ssh.`;
  return `${LINK_FORMAT_RULES}\n\nThe workspace root for this session is: ${workspaceRoot}${host}`;
};
