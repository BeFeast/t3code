import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

spec = importlib.util.spec_from_file_location("release_pair", Path(__file__).with_name("release-pair.py"))
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
SHA = "a" * 40
VERSION = "0.1.0+oklabs.202609051200.aaaaaaa"


class EligibilityTests(unittest.TestCase):
    def test_canary_never_looks_up_main_or_publishes(self):
        with patch.dict(os.environ, CANARY="true", GITHUB_REF="refs/heads/main"), patch.object(release, "main_sha") as main:
            self.assertFalse(release.eligible())
            main.assert_not_called()

    def test_non_main_dispatch_does_not_publish(self):
        with patch.dict(os.environ, CANARY="false", GITHUB_REF="refs/heads/ci-canary"), patch.object(release, "main_sha") as main:
            self.assertFalse(release.eligible())
            main.assert_not_called()

    def test_old_commit_rerun_does_not_publish(self):
        with patch.dict(os.environ, CANARY="false", GITHUB_REF="refs/heads/main"), patch.object(release, "source_sha", return_value=SHA), patch.object(release, "main_sha", return_value="b" * 40):
            self.assertFalse(release.eligible())

    def test_current_main_is_eligible(self):
        with patch.dict(os.environ, CANARY="false", GITHUB_REF="refs/heads/main"), patch.object(release, "source_sha", return_value=SHA), patch.object(release, "main_sha", return_value=SHA):
            self.assertTrue(release.eligible())

    def test_invalid_main_response_fails_closed(self):
        with patch.object(release, "read_url", return_value=b'{"commit":{"id":"main"}}'):
            with self.assertRaises(ValueError):
                release.main_sha()


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old = os.getcwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, self.old)
        Path("build").mkdir()
        self.deb = b"desktop fixture"
        self.server = b"server fixture"
        Path("build/t3code_fixture.deb").write_bytes(self.deb)
        Path("build/t3-" + VERSION + ".tgz").write_bytes(self.server)
        self.deb_hash = hashlib.sha256(self.deb).hexdigest()
        self.index = ("Package: t3code\nVersion: " + VERSION + "\nArchitecture: amd64\n"
                      "Filename: pool/resolute/main/t3code_fixture.deb\nSHA256: " + self.deb_hash + "\n").encode()
        self.saved = None
        self.uploads = 0

    def response(self, data, status=200):
        response = io.BytesIO(data)
        response.status = status
        return response

    def urlopen(self, request, timeout=None):
        if not isinstance(request, str):
            self.uploads += 1
            self.saved = request.data
            return self.response(b"", 201)
        if request.endswith("/Packages"):
            return self.response(self.index)
        if request.endswith(".deb"):
            return self.response(self.deb)
        if request.endswith(".tgz"):
            return self.response(self.server)
        if request.endswith("release.json"):
            return self.response(self.saved)
        raise AssertionError(request)

    @contextlib.contextmanager
    def fixture(self, heads=None):
        with patch.dict(os.environ, OKLABS_VERSION=VERSION, OKLABS_BOT_PAT="fixture-only"), patch.object(release, "eligible", return_value=True), patch.object(release, "source_sha", return_value=SHA), patch.object(release, "main_sha", side_effect=heads or [SHA]), patch.object(release.subprocess, "check_output", return_value=VERSION), patch.object(release.urllib.request, "urlopen", side_effect=self.urlopen):
            yield

    def test_complete_pair_produces_download_verified_receipt(self):
        with self.fixture():
            release.publish()
        receipt = json.loads(Path("build/release.json").read_text())
        self.assertEqual(receipt["commit_sha"], SHA)
        self.assertEqual(receipt["deb_version"], VERSION)
        self.assertEqual(receipt["server_sha256"], hashlib.sha256(self.server).hexdigest())
        self.assertEqual(self.uploads, 1)

    def test_matching_immutable_receipt_is_idempotent(self):
        with self.fixture():
            release.publish()
        saved_urlopen = self.urlopen
        def conflict(request, timeout=None):
            if not isinstance(request, str):
                raise urllib.error.HTTPError(request.full_url, 409, "Conflict", {}, None)
            return saved_urlopen(request, timeout)
        self.urlopen = conflict
        with self.fixture():
            release.publish()
        self.assertEqual(self.uploads, 1)

    def test_conflicting_immutable_receipt_is_not_overwritten(self):
        with self.fixture():
            release.publish()
        receipt = json.loads(self.saved)
        receipt["commit_sha"] = "b" * 40
        self.saved = json.dumps(receipt).encode()
        saved_urlopen = self.urlopen
        def conflict(request, timeout=None):
            if not isinstance(request, str):
                raise urllib.error.HTTPError(request.full_url, 409, "Conflict", {}, None)
            return saved_urlopen(request, timeout)
        self.urlopen = conflict
        with self.fixture(), self.assertRaisesRegex(ValueError, "existing receipt differs"):
            release.publish()
        self.assertEqual(self.uploads, 1)

    def test_main_advance_during_verification_leaves_no_receipt(self):
        with self.fixture(["b" * 40]):
            release.publish()
        self.assertEqual(self.uploads, 0)
        self.assertFalse(Path("build/release.json").exists())

    def test_existing_server_conflict_cannot_be_vouched_for(self):
        self.server = b"older incompatible tarball at same version"
        with self.fixture(), self.assertRaisesRegex(ValueError, "differs"):
            release.publish()
        self.assertEqual(self.uploads, 0)

    def test_missing_desktop_pair_fails_closed(self):
        self.index = self.index.replace(VERSION.encode(), b"older-version")
        with self.fixture(), self.assertRaisesRegex(ValueError, "absent"):
            release.publish()
        self.assertEqual(self.uploads, 0)

    def test_apt_path_cannot_escape_registry(self):
        self.index = self.index.replace(b"pool/resolute/main/t3code_fixture.deb", b"https://outside.invalid/x")
        with self.fixture(), self.assertRaisesRegex(ValueError, "path"):
            release.publish()
        self.assertEqual(self.uploads, 0)


class BuildOnlyPackingTests(unittest.TestCase):
    def test_canary_packs_matching_server_without_token(self):
        script = Path(__file__).with_name("publish-server.sh").resolve()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "apps/server/dist/client").mkdir(parents=True)
            binary = root / "apps/server/dist/bin.mjs"
            binary.write_text("#!/usr/bin/env node\n")
            binary.chmod(0o755)
            monitor = root / "native/resource-monitor/target/x86_64-unknown-linux-gnu/release/t3-resource-monitor"
            monitor.parent.mkdir(parents=True)
            monitor.write_text("fixture")
            monitor.chmod(0o755)
            (root / "apps/server/package.json").write_text(json.dumps(dict(
                name="t3", repository="test", bin={"t3": "dist/bin.mjs"},
                type="module", engines={}, files=["dist"], dependencies={})))
            (root / "pnpm-workspace.yaml").write_text("catalog: {}\n")
            env = dict(os.environ, REPO=directory, VERSION=VERSION, PUBLISH_SERVER="false")
            env.pop("OKLABS_BOT_PAT", None)
            result = release.subprocess.run(["bash", str(script)], env=env,
                                            capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            import tarfile
            with tarfile.open(root / "build" / ("t3-" + VERSION + ".tgz")) as archive:
                package = json.load(archive.extractfile("package/package.json"))
                self.assertEqual(package["version"], VERSION)
                self.assertIn("package/dist/bin.mjs", archive.getnames())
            self.assertIn("publication disabled", result.stdout)


if __name__ == "__main__":
    unittest.main()
