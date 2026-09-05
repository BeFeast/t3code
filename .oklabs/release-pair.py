#!/usr/bin/env python3
"""Publish a receipt only for a verified desktop/server pair at canonical main."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://git.oklabs.uk"


def read_url(url):
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def main_sha():
    data = json.loads(read_url(BASE + "/api/v1/repos/BeFeast/t3code/branches/main"))
    sha = data["commit"]["id"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("canonical main returned an invalid SHA")
    return sha


def source_sha():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def eligible():
    if os.environ.get("CANARY") == "true":
        return False
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        return False
    return source_sha() == main_sha()


def sha256_file(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_download(url, expected):
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=120) as response:
        for block in iter(lambda: response.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError("published artifact differs from this build: " + url)


def deb_entry(index, version):
    for paragraph in index.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in paragraph.splitlines()
                      if ": " in line and not line.startswith(" "))
        if (fields.get("Package") == "t3code" and fields.get("Version") == version
                and fields.get("Architecture") == "amd64"):
            filename = fields["Filename"]
            if not filename.startswith("pool/resolute/main/") or ".." in filename.split("/"):
                raise ValueError("unexpected apt artifact path")
            if not re.fullmatch(r"[0-9a-f]{64}", fields.get("SHA256", "")):
                raise ValueError("invalid apt artifact checksum")
            return fields
    raise ValueError("matching desktop package is absent from apt index")


def publish():
    # Never interrupt a pair of uploads; this check runs after both exist.
    if not eligible():
        print("Source is no longer canonical main; artifacts retained without release receipt")
        return
    version = os.environ["OKLABS_VERSION"]
    debs = list(Path("build").glob("t3code_*.deb"))
    if len(debs) != 1:
        raise ValueError("expected exactly one desktop package")
    deb_version = subprocess.check_output(
        ["dpkg-deb", "-f", str(debs[0]), "Version"], text=True).strip()
    if deb_version != version:
        raise ValueError("desktop and server versions differ")
    index = read_url(BASE + "/api/packages/BeFeast/debian/dists/resolute/main/binary-amd64/Packages").decode()
    entry = deb_entry(index, version)
    deb_url = BASE + "/api/packages/BeFeast/debian/" + urllib.parse.quote(entry["Filename"], safe="/")
    server_name = "t3-" + version + ".tgz"
    server_url = (BASE + "/api/packages/BeFeast/generic/t3-server/"
                  + urllib.parse.quote(version, safe="") + "/"
                  + urllib.parse.quote(server_name, safe=""))
    deb_hash = sha256_file(debs[0])
    server_hash = sha256_file(Path("build") / server_name)
    if entry["SHA256"] != deb_hash:
        raise ValueError("apt package checksum differs from this build")
    verify_download(deb_url, deb_hash)
    verify_download(server_url, server_hash)
    receipt = dict(schema_version=1, version=version, deb_version=deb_version,
                   commit_sha=source_sha(), ref="refs/heads/main",
                   deb_url=deb_url, deb_sha256=deb_hash,
                   server_url=server_url, server_sha256=server_hash)
    # The consumer independently checks main again; a later main advance is
    # safe and makes this receipt ineligible rather than promoting stale code.
    if receipt["commit_sha"] != main_sha():
        print("main advanced during verification; no release receipt published")
        return
    body = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
    url = (BASE + "/api/packages/BeFeast/generic/t3-release/"
           + urllib.parse.quote(version, safe="") + "/release.json")
    auth = base64.b64encode(("oklabs-bot:" + os.environ["OKLABS_BOT_PAT"]).encode()).decode()
    request = urllib.request.Request(url, data=body, method="PUT",
                                    headers={"Authorization": "Basic " + auth,
                                             "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            if response.status != 201:
                raise ValueError("unexpected receipt upload status")
    except urllib.error.HTTPError as error:
        if error.code != 409:
            raise
        error.close()
    if json.loads(read_url(url)) != receipt:
        raise ValueError("existing receipt differs from this complete pair")
    Path("build/release.json").write_bytes(body)
    print("Verified complete release receipt: " + version)


if __name__ == "__main__":
    if sys.argv[1:] == ["eligible"]:
        allowed = eligible()
        with open(os.environ["GITHUB_ENV"], "a") as stream:
            stream.write("PUBLISH_ELIGIBLE=" + str(allowed).lower() + "\n")
        print("Publication eligible: " + str(allowed).lower())
    elif sys.argv[1:] == ["publish"]:
        publish()
    else:
        raise SystemExit("usage: release-pair.py eligible|publish")
