import type { RemoteT3RunnerOptions } from "@t3tools/ssh/tunnel";
import * as Option from "effect/Option";
import serverPackageJson from "../../../server/package.json" with { type: "json" };
import type { DesktopEnvironment } from "../app/DesktopEnvironment.ts";

// The remote uses the app's upstream release archive, so it needs neither
// Node nor npm. Development points the remote at
// a source checkout instead so the two sides can be iterated together.
export const resolveDesktopSshCliRunner = (
  environment: Pick<
    DesktopEnvironment["Service"],
    "isDevelopment" | "devRemoteT3ServerEntryPath" | "appVersion"
  >,
): RemoteT3RunnerOptions => {
  const devRemoteEntryPath = Option.getOrUndefined(environment.devRemoteT3ServerEntryPath);
  if (environment.isDevelopment && devRemoteEntryPath !== undefined) {
    return {
      nodeScriptPath: devRemoteEntryPath,
      nodeEngineRange: serverPackageJson.engines.node,
    };
  }
  // Fork build metadata identifies the desktop build, not an upstream archive.
  return { archiveVersion: environment.appVersion.split("+")[0] ?? environment.appVersion };
};
