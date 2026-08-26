import { useAtomValue } from "@effect/atom-react";
import type { EnvironmentId, ProjectListEntriesResult } from "@t3tools/contracts";
import { AsyncResult, Atom } from "effect/unstable/reactivity";
import { useMemo } from "react";

import { projectEnvironment } from "~/state/projects";
import { subprojectRegistryPath } from "~/subprojectTag";

/**
 * The sub-projects living under a project's `Areas/` folder.
 *
 * `projectEnvironment.listEntries` is an atom family keyed by its arguments, so
 * every sidebar row belonging to the same project shares one listing rather
 * than issuing its own.
 */

const EMPTY_ENTRIES_ATOM = Atom.make(
  AsyncResult.initial<ProjectListEntriesResult, never>(false),
).pipe(Atom.withLabel("subproject-names:empty"));

const NO_NAMES: ReadonlyArray<string> = [];

export function useSubprojectNames(
  environmentId: EnvironmentId | null,
  workspaceRoot: string | null,
): ReadonlyArray<string> {
  const registryPath = workspaceRoot === null ? null : subprojectRegistryPath(workspaceRoot);
  // Hooks cannot be skipped, so the no-target case subscribes to a constant.
  const atom =
    environmentId === null || registryPath === null
      ? EMPTY_ENTRIES_ATOM
      : projectEnvironment.listEntries({ environmentId, input: { cwd: registryPath } });
  const result = useAtomValue(atom);

  return useMemo(() => {
    if (result._tag !== "Success") return NO_NAMES;
    // A checkout with no `Areas/` fails the listing rather than returning
    // nothing, and either way the answer is the same: no chips.
    return result.value.entries
      .filter((entry) => entry.kind === "directory")
      // `path` is what the listing returns; only the last segment names the
      // sub-project.
      .map((entry) => entry.path.replace(/\/+$/, "").split("/").pop() ?? "")
      .filter((name) => name.length > 0 && !name.startsWith("_") && !name.startsWith("."));
  }, [result]);
}
