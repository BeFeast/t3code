#!/usr/bin/env bash
# Pack apps/server into an npm-installable tarball and PUT it to the OK Forge
# generic registry. Same rules as /usr/local/bin/t3code-oklabs-publish-server
# (do not use `npm publish` or `npm pack` — see the t3code-oklabs-build runbook).
#
# Env:
#   PUBLISH_SERVER  false = pack artifact only (default true)
#   OKLABS_BOT_PAT  (required for publication)
#   VERSION         (required) — must match the desktop stamp byte-for-byte
#   REPO            (default: cwd)
set -euo pipefail

REPO="${REPO:-$(pwd)}"
VERSION="${VERSION:?VERSION is required}"
REGISTRY="https://git.oklabs.uk/api/packages/BeFeast/generic/t3-server"
DIST="$REPO/apps/server/dist"
RESOURCE_MONITOR="$REPO/native/resource-monitor/target/x86_64-unknown-linux-gnu/release/t3-resource-monitor"

[[ -x "$DIST/bin.mjs" ]] || { echo "$DIST/bin.mjs missing" >&2; exit 1; }
[[ -d "$DIST/client" ]] || { echo "$DIST/client missing" >&2; exit 1; }
[[ -x "$RESOURCE_MONITOR" ]] || { echo "resource monitor missing at $RESOURCE_MONITOR" >&2; exit 1; }

STAGE=$(mktemp -d -t t3code-server-publish-XXXXXX)
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT
PKG="$STAGE/package"
mkdir -p "$PKG"

python3 - "$REPO" "$VERSION" "$PKG/package.json" <<'PY'
import json, sys, yaml
repo, version, out = sys.argv[1], sys.argv[2], sys.argv[3]
with open(f"{repo}/apps/server/package.json", encoding="utf-8") as fh:
    pkg = json.load(fh)
with open(f"{repo}/pnpm-workspace.yaml", encoding="utf-8") as fh:
    workspace = yaml.safe_load(fh)
catalog = workspace.get("catalog") or {}

def resolve(specs):
    resolved = {}
    for name, spec in (specs or {}).items():
        if spec == "catalog:":
            key = name.split(">")[-1]
            if key not in catalog:
                raise SystemExit(f"catalog entry missing for {name} (looked up {key})")
            resolved[name] = catalog[key]
        else:
            resolved[name] = spec
    return resolved

published = {
    "name": pkg["name"],
    "repository": pkg["repository"],
    "bin": pkg["bin"],
    "type": pkg["type"],
    "version": version,
    "engines": pkg["engines"],
    "files": pkg["files"],
    "dependencies": resolve(pkg.get("dependencies")),
    "overrides": resolve(workspace.get("overrides")),
}
if any(spec == "catalog:" for spec in published["dependencies"].values()):
    raise SystemExit("unresolved catalog: specifier left in dependencies")
with open(out, "w", encoding="utf-8") as fh:
    json.dump(published, fh, indent=2)
    fh.write("\n")
PY

STAGED=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$PKG/package.json")
[[ "$STAGED" == "$VERSION" ]] || { echo "staged version $STAGED != $VERSION" >&2; exit 1; }

cp -a "$DIST" "$PKG/dist"
mkdir -p "$PKG/dist/resource-monitor/linux-x64"
cp -f "$RESOURCE_MONITOR" "$PKG/dist/resource-monitor/linux-x64/t3-resource-monitor"
chmod +x "$PKG/dist/resource-monitor/linux-x64/t3-resource-monitor"

TARBALL="$STAGE/t3-${VERSION}.tgz"
tar -czf "$TARBALL" -C "$STAGE" --owner=0 --group=0 --numeric-owner package
LISTING="$STAGE/tarball.listing"
tar -tzf "$TARBALL" >"$LISTING"
for required in package/package.json package/dist/bin.mjs package/dist/resource-monitor/linux-x64/t3-resource-monitor; do
  grep -qx "$required" "$LISTING" || { echo "tarball missing $required" >&2; exit 1; }
done

# Copy aside for the workflow artifact (VERSION may contain '+')
mkdir -p "$REPO/build"
cp -f "$TARBALL" "$REPO/build/t3-${VERSION}.tgz"

if [[ "${PUBLISH_SERVER:-true}" == "false" ]]; then
  echo "packed matching server artifact; publication disabled"
  exit 0
fi
TOKEN="${OKLABS_BOT_PAT:?OKLABS_BOT_PAT is required}"

CODE=$(curl -sS -o /dev/null -w '%{http_code}' -u "oklabs-bot:${TOKEN}" \
  --upload-file "$TARBALL" \
  "$REGISTRY/${VERSION}/t3-${VERSION}.tgz")
case "$CODE" in
  201) echo "uploaded t3-${VERSION}.tgz" ;;
  409) echo "$VERSION already present — idempotent skip" ;;
  *) echo "registry upload HTTP $CODE" >&2; exit 1 ;;
esac

# prove visibility (no pipe into jq -e from curl in a way that can SIGPIPE:
# write the body first)
curl -fsS -m 30 -o "$STAGE/pkgs.json" \
  "https://git.oklabs.uk/api/v1/packages/BeFeast?type=generic&q=t3-server"
python3 - "$STAGE/pkgs.json" "$VERSION" <<'PY'
import json, sys
pkgs = json.load(open(sys.argv[1]))
ver = sys.argv[2]
if not any(p.get("name") == "t3-server" and p.get("version") == ver for p in pkgs):
    raise SystemExit(f"uploaded {ver} but it is not visible in the package index")
print(f"visible: t3-server {ver}")
PY
