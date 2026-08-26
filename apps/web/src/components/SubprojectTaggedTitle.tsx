import type { EnvironmentId } from "@t3tools/contracts";

import { cn } from "~/lib/utils";
import { resolveSubprojectTag, subprojectTagColorIndex } from "~/subprojectTag";
import { useSubprojectNames } from "~/useSubprojectNames";

/**
 * A thread title with the sub-project it is about pulled out into a chip.
 *
 * One T3 project per domain means `Dev` alone holds threads about a dozen
 * products; the chip is what tells them apart at a glance. See `subprojectTag.ts`
 * for why the title is the signal.
 */

/**
 * Muted, low-chroma pairs: the chip labels a row, it does not compete with the
 * unread and active states that already colour it. Each entry carries both
 * schemes so the palette does not need a theme lookup.
 */
const TAG_TONES = [
  "bg-sky-500/12 text-sky-700 dark:text-sky-300",
  "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
  "bg-violet-500/12 text-violet-700 dark:text-violet-300",
  "bg-amber-500/14 text-amber-700 dark:text-amber-300",
  "bg-rose-500/12 text-rose-700 dark:text-rose-300",
  "bg-teal-500/12 text-teal-700 dark:text-teal-300",
] as const;

export function SubprojectTaggedTitle(props: {
  readonly title: string;
  readonly environmentId: EnvironmentId | null;
  readonly workspaceRoot: string | null;
}) {
  const names = useSubprojectNames(props.environmentId, props.workspaceRoot);
  const tag = resolveSubprojectTag(props.title, names);

  if (tag === null) return props.title;

  const tone = TAG_TONES[subprojectTagColorIndex(tag.label, TAG_TONES.length)] ?? TAG_TONES[0];
  return (
    <>
      <span
        className={cn(
          "mr-1 inline-flex shrink-0 items-center rounded-[0.4em] px-[0.4em] py-px align-baseline text-[0.85em] font-medium",
          tone,
        )}
      >
        {tag.label}
      </span>{" "}
      {tag.title}
    </>
  );
}
