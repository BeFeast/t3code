import { describe, expect, it } from "vite-plus/test";

import {
  normalizeTagToken,
  parseLeadingTag,
  resolveSubprojectTag,
  subprojectRegistryPath,
  subprojectTagColorIndex,
} from "./subprojectTag";

/** Spelled as the folders under `Dev/Areas/` are. */
const DEV_AREAS = [
  "apertune",
  "befeast",
  "cosign",
  "finance-tracker",
  "halenote",
  "hedroom",
  "intake",
  "karaoke",
  "maestro",
  "ok-folio",
  "ok-gobot",
  "ok-player",
  "open-design",
  "panoptikon",
  "pomoi",
  "rehearsekit",
  "scribe-service",
  "t3code-fork",
  "tx10-clock",
];

describe("normalizeTagToken", () => {
  it("collapses the spellings the title generator actually produces", () => {
    expect(normalizeTagToken("ok-gobot")).toBe("okgobot");
    expect(normalizeTagToken("OKGoBot")).toBe("okgobot");
    expect(normalizeTagToken("OK GoBot")).toBe("okgobot");
    expect(normalizeTagToken("HedRoom")).toBe("hedroom");
  });
});

describe("parseLeadingTag", () => {
  it("splits an explicit prefix from the rest", () => {
    expect(parseLeadingTag("[hedroom] iOS native v1 — build")).toEqual({
      tag: "hedroom",
      rest: "iOS native v1 — build",
    });
  });

  it("keeps a tag-only title readable instead of blanking the row", () => {
    expect(parseLeadingTag("[hedroom]")).toEqual({ tag: "hedroom", rest: "hedroom" });
  });

  it("ignores titles with no prefix, an empty prefix, or a runaway bracket", () => {
    expect(parseLeadingTag("Hedroom iOS App V1")).toBeNull();
    expect(parseLeadingTag("[] nothing")).toBeNull();
    expect(parseLeadingTag(`[${"x".repeat(60)}] too long`)).toBeNull();
  });
});

describe("resolveSubprojectTag", () => {
  it("tags titles the model wrote on its own, with no rename", () => {
    // Real titles from the Dev project.
    expect(resolveSubprojectTag("Hedroom iOS App V1", DEV_AREAS)).toMatchObject({
      label: "hedroom",
      source: "name",
    });
    expect(resolveSubprojectTag("Build OKGoBot Execution Agent", DEV_AREAS)).toMatchObject({
      label: "ok-gobot",
      source: "name",
    });
    expect(resolveSubprojectTag("iOS ok-folio HQ", DEV_AREAS)).toMatchObject({
      label: "ok-folio",
      source: "name",
    });
    expect(resolveSubprojectTag("Create or Sync Halenote Repository", DEV_AREAS)).toMatchObject({
      label: "halenote",
      source: "name",
    });
  });

  it("keeps the whole title beside the chip when the name is embedded mid-sentence", () => {
    expect(resolveSubprojectTag("Interview Hedroom Open Issues", DEV_AREAS)).toEqual({
      label: "hedroom",
      title: "Interview Hedroom Open Issues",
      source: "name",
    });
  });

  it("lets an explicit prefix win and strips it from the text", () => {
    expect(resolveSubprojectTag("[hedroom] PDF export для документов", DEV_AREAS)).toEqual({
      label: "hedroom",
      title: "PDF export для документов",
      source: "prefix",
    });
  });

  it("spells a prefix the way the sub-project does", () => {
    expect(resolveSubprojectTag("[OKGoBot] retry policy", DEV_AREAS)).toMatchObject({
      label: "ok-gobot",
      source: "prefix",
    });
  });

  it("honours a prefix that names nothing known -- it is still intent", () => {
    expect(resolveSubprojectTag("[limenora] PRD revision", DEV_AREAS)).toMatchObject({
      label: "limenora",
      source: "prefix",
    });
  });

  it("prefers the longest matching name", () => {
    expect(resolveSubprojectTag("notes on ok-player timing", ["ok", "ok-player"])).toMatchObject({
      label: "ok-player",
    });
  });

  it("refuses to match on a name too short to be distinctive", () => {
    expect(resolveSubprojectTag("an AI experiment", ["ai"])).toBeNull();
  });

  it("returns null for the two thirds of titles that name no sub-project", () => {
    for (const title of [
      "Improve Project Workflow Guidance",
      "Bitwarden–1Password Sync Bridge",
      "Obsidian to Deploy Workflow",
      "Continue Forgejo Migration",
    ]) {
      expect(resolveSubprojectTag(title, DEV_AREAS)).toBeNull();
    }
  });

  it("handles an empty registry and an empty title", () => {
    expect(resolveSubprojectTag("Hedroom iOS App V1", [])).toBeNull();
    expect(resolveSubprojectTag("", DEV_AREAS)).toBeNull();
  });
});

describe("subprojectTagColorIndex", () => {
  it("gives one sub-project the same colour every time, whatever its casing", () => {
    const a = subprojectTagColorIndex("hedroom", 8);
    expect(subprojectTagColorIndex("HedRoom", 8)).toBe(a);
    expect(a).toBeGreaterThanOrEqual(0);
    expect(a).toBeLessThan(8);
  });

  it("stays in range and does not divide by zero", () => {
    expect(subprojectTagColorIndex("hedroom", 0)).toBe(0);
    for (const name of DEV_AREAS) {
      const index = subprojectTagColorIndex(name, 6);
      expect(index).toBeGreaterThanOrEqual(0);
      expect(index).toBeLessThan(6);
    }
  });
});

describe("subprojectRegistryPath", () => {
  it("points at the Areas folder of the workspace", () => {
    expect(subprojectRegistryPath("/home/god/Obsidian Vault/Dev")).toBe(
      "/home/god/Obsidian Vault/Dev/Areas",
    );
    expect(subprojectRegistryPath("/home/god/Obsidian Vault/Dev/")).toBe(
      "/home/god/Obsidian Vault/Dev/Areas",
    );
  });

  it("has nothing to offer a blank root", () => {
    expect(subprojectRegistryPath("   ")).toBeNull();
  });
});
