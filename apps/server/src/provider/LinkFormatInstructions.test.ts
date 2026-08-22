import { describe, expect, it } from "@effect/vitest";

import {
  getSessionHostLabel,
  linkFormatInstructions,
  setSessionHostLabel,
} from "./LinkFormatInstructions.ts";
import { buildRuntimeInstructions } from "./RuntimeInstructions.ts";

describe("linkFormatInstructions", () => {
  it("omits the block when no workspace root is bound", () => {
    expect(linkFormatInstructions(undefined)).toBeUndefined();
    expect(linkFormatInstructions("")).toBeUndefined();
  });

  it("omits the host line until the server records which machine it is", () => {
    setSessionHostLabel(undefined);
    expect(getSessionHostLabel()).toBeUndefined();
    expect(linkFormatInstructions("/workspace") ?? "").not.toContain(
      "This session's server runs on",
    );
  });

  it("names the host so the agent can tell a local checkout from an ssh one", () => {
    setSessionHostLabel("maestro");
    const append = linkFormatInstructions("/home/god/Obsidian Vault/Dev") ?? "";
    expect(append).toContain("This session's server runs on maestro");
    expect(append).toContain("reachable only over ssh");
    setSessionHostLabel(undefined);
  });

  it("treats a blank label as unknown rather than printing an empty host", () => {
    setSessionHostLabel("   ");
    expect(getSessionHostLabel()).toBeUndefined();
    expect(linkFormatInstructions("/workspace") ?? "").not.toContain(
      "This session's server runs on",
    );
  });

  it("trims a label written with stray whitespace", () => {
    setSessionHostLabel("  sindri  ");
    expect(linkFormatInstructions("/workspace") ?? "").toContain(
      "This session's server runs on sindri.",
    );
    setSessionHostLabel(undefined);
  });

  it("names the live workspace root so the relative-vs-absolute rule has an anchor", () => {
    const append = linkFormatInstructions("/home/god/Obsidian Vault/Dev");
    expect(append).toContain(
      "The workspace root for this session is: /home/god/Obsidian Vault/Dev",
    );
  });

  it("states both path forms and the two shapes that are not chips", () => {
    const append = linkFormatInstructions("/workspace") ?? "";
    expect(append).toContain("Inside the workspace root");
    expect(append).toContain("Outside the workspace root");
    // The renderer drops any inline-code span containing whitespace, and any
    // path with no directory segment, so the prompt has to name both.
    expect(append).toContain("containing a space");
    expect(append).toContain("bare filename");
  });

  it("forbids repeating the root folder name, the most common broken chip", () => {
    const append = linkFormatInstructions("/home/god/Obsidian Vault/Dev") ?? "";
    // `Dev/Areas/x.md` under a root that already ends in /Dev resolves to
    // Dev/Dev/Areas/x.md. It reads correct to anyone who cannot see the root,
    // so the prompt has to call it out by name rather than imply it.
    expect(append).toContain("Never repeat the root's own folder name");
  });

  it("warns that repository paths are outside the workspace root", () => {
    const append = linkFormatInstructions("/home/god/Obsidian Vault/Dev") ?? "";
    // A thread about a code project names files the way the repo does, e.g.
    // docs/architecture.md, which resolves under the vault and does not exist.
    expect(append).toContain("Files in a code repository are almost always outside this root");
  });

  it("rides on the runtime instructions every provider builds, after upstream's blocks", () => {
    setSessionHostLabel("maestro");
    const withoutRoot = buildRuntimeInstructions({ harness: "Claude Code" });
    const withRoot = buildRuntimeInstructions({
      harness: "Claude Code",
      workspaceRoot: "/workspace",
    });
    // Exactly upstream's text, then a blank line, then ours: the model reads
    // the same prompt the one-line ClaudeAdapter wiring used to assemble.
    expect(withRoot).toBe(`${withoutRoot}\n\n${linkFormatInstructions("/workspace")}`);
    expect(withRoot).toContain("This session's server runs on maestro");
    expect(buildRuntimeInstructions({ harness: "Claude Code", workspaceRoot: undefined })).toBe(
      withoutRoot,
    );
    expect(buildRuntimeInstructions({ harness: "Codex", workspaceRoot: "" })).toBe(
      buildRuntimeInstructions({ harness: "Codex" }),
    );
    setSessionHostLabel(undefined);
  });
});
