/**
 * The sub-project a thread is about, for the sidebar chip.
 *
 * One T3 project per domain (`Dev`, `HomeLab`, `Health`) means a single list
 * holds threads about a dozen different products. The sub-project is not a
 * field on a thread and adding one would cross contracts, decider, projector, a
 * migration and mobile -- so this reads the only signal already present: the
 * title.
 *
 * A thread title cannot be set when the thread is created, only renamed
 * afterwards, so a scheme that depends on the author writing a marker up front
 * would tag almost nothing. Matching a known sub-project name anywhere in the
 * title needs no such cooperation: the model routinely writes "Hedroom iOS App
 * V1" or "Build OKGoBot Execution Agent" on its own. An explicit `[tag]` prefix
 * still wins when present, so a rename remains the override for the rest.
 */

/** Below this, a name is too generic to match on without inviting collisions. */
const MIN_MATCHABLE_LENGTH = 4;

/** Guards against a pathological "[...]" spanning the whole title. */
const MAX_PREFIX_TAG_LENGTH = 40;

export interface SubprojectTag {
  /** Chip text, spelled the way the sub-project itself is. */
  readonly label: string;
  /** What to render beside the chip. */
  readonly title: string;
  readonly source: "prefix" | "name";
}

/**
 * `ok-gobot`, `OKGoBot` and `OK GoBot` are one name.
 *
 * The model picks its own casing and word separators every time, so comparing
 * anything but a stripped, lowercased form loses most real matches.
 */
export function normalizeTagToken(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

/** The `[hedroom]` in `[hedroom] Email для клиентов`, plus the rest. */
export function parseLeadingTag(title: string): { tag: string; rest: string } | null {
  const match = new RegExp(`^\\s*\\[([^\\]]{1,${MAX_PREFIX_TAG_LENGTH}})\\]\\s*(.*)$`, "s").exec(
    title,
  );
  if (match === null) return null;
  const tag = (match[1] ?? "").trim();
  if (tag.length === 0) return null;
  const rest = (match[2] ?? "").trim();
  // A title that is nothing but a tag keeps the tag as its own text: an empty
  // sidebar row reads as a bug.
  return { tag, rest: rest.length > 0 ? rest : tag };
}

/**
 * The sub-project this title is about, or null.
 *
 * `knownNames` is spelled as the sub-projects are (`ok-gobot`), and comes from
 * the workspace itself, so it needs no separate config to stay current.
 */
export function resolveSubprojectTag(
  title: string,
  knownNames: ReadonlyArray<string>,
): SubprojectTag | null {
  const prefix = parseLeadingTag(title);
  if (prefix !== null) {
    // An explicit tag is intent, so it stands even when it names nothing known
    // -- but spell it the canonical way when it does.
    const normalized = normalizeTagToken(prefix.tag);
    const known = knownNames.find((name) => normalizeTagToken(name) === normalized);
    return { label: known ?? prefix.tag, title: prefix.rest, source: "prefix" };
  }

  const haystack = normalizeTagToken(title);
  if (haystack.length === 0) return null;

  // Longest wins: `ok-folio` and `ok` would both match, and the specific name is
  // the informative one.
  let best: string | null = null;
  let bestLength = 0;
  for (const name of knownNames) {
    const needle = normalizeTagToken(name);
    if (needle.length < MIN_MATCHABLE_LENGTH || needle.length <= bestLength) continue;
    if (haystack.includes(needle)) {
      best = name;
      bestLength = needle.length;
    }
  }
  return best === null ? null : { label: best, title, source: "name" };
}

/**
 * A stable bucket for the chip's colour, so one sub-project always looks the
 * same. FNV-1a, kept local: this module has no other dependency and a shared
 * helper would only widen what a rebase has to reconcile.
 */
export function subprojectTagColorIndex(label: string, buckets: number): number {
  if (buckets <= 0) return 0;
  let hash = 0x81_1c_9d_c5;
  const normalized = normalizeTagToken(label);
  for (let index = 0; index < normalized.length; index += 1) {
    hash ^= normalized.charCodeAt(index);
    hash = Math.imul(hash, 0x01_00_01_93) >>> 0;
  }
  return hash % buckets;
}

/**
 * Sub-project names for a project rooted at `workspaceRoot`.
 *
 * The vault keeps one folder per sub-project under `Areas/`, in every domain,
 * so the directory listing is the registry. Anything else (a code checkout with
 * no `Areas/`) yields nothing and simply shows no chips.
 */
export const SUBPROJECT_REGISTRY_DIRECTORY = "Areas";

export function subprojectRegistryPath(workspaceRoot: string): string | null {
  const trimmed = workspaceRoot.trim().replace(/\/+$/, "");
  if (trimmed.length === 0) return null;
  return `${trimmed}/${SUBPROJECT_REGISTRY_DIRECTORY}`;
}
