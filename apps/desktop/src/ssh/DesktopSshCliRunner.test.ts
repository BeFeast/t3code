import { assert, describe, it } from "@effect/vitest";
import { buildRemoteLaunchScript, SshInvalidArchiveVersionError } from "@t3tools/ssh/tunnel";
import * as Option from "effect/Option";

import { resolveDesktopSshCliRunner } from "./DesktopSshCliRunner.ts";

describe("desktop SSH CLI runner", () => {
  const packaged = {
    isDevelopment: false,
    devRemoteT3ServerEntryPath: Option.none<string>(),
  };

  it("uses the upstream archive identity for a packaged fork without changing its app version", () => {
    const appVersion = "0.0.41-nightly.20260914.1707+oklabs.202609141400.37df4e9";
    const environment = { ...packaged, appVersion };
    const script = buildRemoteLaunchScript(resolveDesktopSshCliRunner(environment));

    assert.equal(
      script,
      buildRemoteLaunchScript({ archiveVersion: "0.0.41-nightly.20260914.1707" }),
    );
    assert.notInclude(script, "+oklabs");
    assert.equal(environment.appVersion, appVersion);
  });

  it("preserves exact stable, preview, and nightly archive versions", () => {
    for (const appVersion of [
      "0.0.41",
      "0.0.41-preview.20260914.1693",
      "0.0.41-nightly.20260914.1707",
    ]) {
      assert.equal(
        buildRemoteLaunchScript(resolveDesktopSshCliRunner({ ...packaged, appVersion })),
        buildRemoteLaunchScript({ archiveVersion: appVersion }),
      );
    }
  });

  it("keeps the development source runner ahead of the packaged archive", () => {
    const runner = resolveDesktopSshCliRunner({
      isDevelopment: true,
      devRemoteT3ServerEntryPath: Option.some("/workspace/t3/apps/server/dist/bin.mjs"),
      appVersion: "0.0.41-nightly.20260914.1707+oklabs.local",
    });
    assert.equal(runner.nodeScriptPath, "/workspace/t3/apps/server/dist/bin.mjs");
    assert.isString(runner.nodeEngineRange);
    assert.isUndefined(runner.archiveVersion);
    assert.include(buildRemoteLaunchScript(runner), "T3_ARCHIVE_MODE=0");
  });

  it("still rejects invalid archive paths after selecting the desktop runner", () => {
    for (const appVersion of ["../other+oklabs.local", "1.2.3/evil+oklabs.local"]) {
      assert.throws(
        () => buildRemoteLaunchScript(resolveDesktopSshCliRunner({ ...packaged, appVersion })),
        SshInvalidArchiveVersionError,
      );
    }
  });
});
