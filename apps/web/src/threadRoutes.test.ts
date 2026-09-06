import { describe, expect, it } from "vite-plus/test";
import { scopeThreadRef } from "@t3tools/client-runtime/environment";
import { ThreadId } from "@t3tools/contracts";
import { DraftId } from "./composerDraftStore";

import {
  buildDraftThreadRouteParams,
  buildThreadRouteParams,
  resolveActiveThreadRouteRef,
  resolveThreadRouteRenderState,
  resolveThreadRouteRef,
  resolveThreadRouteTarget,
} from "./threadRoutes";

describe("threadRoutes", () => {
  it("builds canonical thread route params from a scoped ref", () => {
    const ref = scopeThreadRef("env-1" as never, ThreadId.make("thread-1"));

    expect(buildThreadRouteParams(ref)).toEqual({
      environmentId: "env-1",
      threadId: "thread-1",
    });
  });

  it("resolves a scoped ref only when both params are present", () => {
    expect(
      resolveThreadRouteRef({
        environmentId: "env-1",
        threadId: "thread-1",
      }),
    ).toEqual({
      environmentId: "env-1",
      threadId: "thread-1",
    });

    expect(resolveThreadRouteRef({ environmentId: "env-1" })).toBeNull();
    expect(resolveThreadRouteRef({ threadId: "thread-1" })).toBeNull();
  });

  it("builds canonical draft route params from a draft id", () => {
    expect(buildDraftThreadRouteParams(DraftId.make("draft-1"))).toEqual({
      draftId: "draft-1",
    });
  });

  it("resolves draft and server route targets", () => {
    expect(
      resolveThreadRouteTarget({
        environmentId: "env-1",
        threadId: "thread-1",
      }),
    ).toEqual({
      kind: "server",
      threadRef: {
        environmentId: "env-1",
        threadId: "thread-1",
      },
    });

    expect(
      resolveThreadRouteTarget({
        draftId: "draft-1",
      }),
    ).toEqual({
      kind: "draft",
      draftId: "draft-1",
    });
  });

  it("resolves the backing thread while a draft route is being promoted", () => {
    const target = resolveThreadRouteTarget({ draftId: "draft-1" });

    expect(
      resolveActiveThreadRouteRef(target, {
        environmentId: "env-1" as never,
        threadId: ThreadId.make("draft-thread"),
        promotedTo: scopeThreadRef("env-2" as never, ThreadId.make("server-thread")),
      }),
    ).toEqual({
      environmentId: "env-2",
      threadId: "server-thread",
    });
  });

  it("does not treat a draft's reserved thread ref as an active sidebar thread", () => {
    const target = resolveThreadRouteTarget({ draftId: "draft-1" });

    expect(
      resolveActiveThreadRouteRef(target, {
        environmentId: "env-1" as never,
        threadId: ThreadId.make("draft-thread"),
        promotedTo: null,
      }),
    ).toBeNull();
  });

  it("keeps shell-only server threads in the loading state", () => {
    expect(
      resolveThreadRouteRenderState({
        bootstrapComplete: true,
        shellSynchronizationFailed: false,
        shellStatus: "live",
        serverThreadShellExists: true,
        serverThreadDetailExists: false,
        serverThreadDetailDeleted: false,
        draftThreadExists: false,
      }),
    ).toBe("loading");
  });

  it("renders server details and local drafts when they are ready", () => {
    expect(
      resolveThreadRouteRenderState({
        bootstrapComplete: true,
        shellSynchronizationFailed: false,
        shellStatus: "live",
        serverThreadShellExists: true,
        serverThreadDetailExists: true,
        serverThreadDetailDeleted: false,
        draftThreadExists: false,
      }),
    ).toBe("ready");
    expect(
      resolveThreadRouteRenderState({
        bootstrapComplete: true,
        shellSynchronizationFailed: false,
        shellStatus: "live",
        serverThreadShellExists: false,
        serverThreadDetailExists: false,
        serverThreadDetailDeleted: false,
        draftThreadExists: true,
      }),
    ).toBe("ready");
  });

  it("distinguishes bootstrap loading from a missing thread", () => {
    expect(
      resolveThreadRouteRenderState({
        bootstrapComplete: false,
        shellSynchronizationFailed: false,
        shellStatus: "empty",
        serverThreadShellExists: false,
        serverThreadDetailExists: false,
        serverThreadDetailDeleted: false,
        draftThreadExists: false,
      }),
    ).toBe("loading");
    expect(
      resolveThreadRouteRenderState({
        bootstrapComplete: true,
        shellSynchronizationFailed: false,
        shellStatus: "live",
        serverThreadShellExists: false,
        serverThreadDetailExists: false,
        serverThreadDetailDeleted: false,
        draftThreadExists: false,
      }),
    ).toBe("missing");
  });

  it("redirects deleted shell-only threads", () => {
    expect(
      resolveThreadRouteRenderState({
        bootstrapComplete: true,
        shellSynchronizationFailed: false,
        shellStatus: "live",
        serverThreadShellExists: true,
        serverThreadDetailExists: false,
        serverThreadDetailDeleted: true,
        draftThreadExists: false,
      }),
    ).toBe("missing");
  });
  it("keeps a new thread route while a stale shell catches up", () => {
    const missingFromCache = {
      bootstrapComplete: true,
      shellSynchronizationFailed: false,
      serverThreadShellExists: false,
      serverThreadDetailExists: false,
      serverThreadDetailDeleted: false,
      draftThreadExists: false,
    };
    const states = [
      resolveThreadRouteRenderState({ ...missingFromCache, shellStatus: "cached" }),
      resolveThreadRouteRenderState({ ...missingFromCache, shellStatus: "synchronizing" }),
      resolveThreadRouteRenderState({
        ...missingFromCache,
        shellStatus: "live",
        serverThreadShellExists: true,
      }),
      resolveThreadRouteRenderState({
        ...missingFromCache,
        shellStatus: "live",
        serverThreadShellExists: true,
        serverThreadDetailExists: true,
      }),
    ];
    expect(states).toEqual(["loading", "loading", "loading", "ready"]);
    expect(states).not.toContain("missing");
    expect(resolveThreadRouteRenderState({ ...missingFromCache, shellStatus: "live" })).toBe(
      "missing",
    );
  });

  it("renders cached details and local drafts without waiting for a live shell", () => {
    const cached = {
      bootstrapComplete: true,
      shellSynchronizationFailed: false,
      shellStatus: "cached" as const,
      serverThreadShellExists: false,
      serverThreadDetailExists: false,
      serverThreadDetailDeleted: false,
      draftThreadExists: false,
    };
    expect(resolveThreadRouteRenderState({ ...cached, serverThreadDetailExists: true })).toBe(
      "ready",
    );
    expect(resolveThreadRouteRenderState({ ...cached, draftThreadExists: true })).toBe("ready");
    expect(resolveThreadRouteRenderState({ ...cached, serverThreadShellExists: true })).toBe(
      "loading",
    );
  });
  it("gives the deep link up once synchronization failed, so the error can surface", () => {
    // A dead server with a cached snapshot: the shell falls back to "cached"
    // and records an error. Waiting for "live" here would be a blank screen
    // with no end; the upstream "missing" path at least redirects.
    const unreachable = {
      bootstrapComplete: true,
      shellSynchronizationFailed: true,
      shellStatus: "cached" as const,
      serverThreadShellExists: false,
      serverThreadDetailExists: false,
      serverThreadDetailDeleted: false,
      draftThreadExists: false,
    };
    expect(resolveThreadRouteRenderState(unreachable)).toBe("missing");
    // A thread the cache does know keeps rendering from the cached shell.
    expect(resolveThreadRouteRenderState({ ...unreachable, serverThreadShellExists: true })).toBe(
      "loading",
    );
    // Once the stream is back (status "live" clears the error) the usual rules apply.
    expect(
      resolveThreadRouteRenderState({
        ...unreachable,
        shellSynchronizationFailed: false,
        shellStatus: "synchronizing",
      }),
    ).toBe("loading");
  });

  it("keeps explicit deletion authoritative while the shell is cached or synchronizing", () => {
    for (const shellStatus of ["cached", "synchronizing"] as const) {
      expect(
        resolveThreadRouteRenderState({
          bootstrapComplete: true,
          shellSynchronizationFailed: false,
          shellStatus,
          serverThreadShellExists: true,
          serverThreadDetailExists: false,
          serverThreadDetailDeleted: true,
          draftThreadExists: false,
        }),
      ).toBe("missing");
    }
  });
});
