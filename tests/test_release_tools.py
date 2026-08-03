"""Release bundle inventory and verification contracts."""

from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SBOM_SCRIPT = ROOT / "tools" / "gen-bundle-sbom.py"
SPEC = importlib.util.spec_from_file_location("session_optimizer_sbom", SBOM_SCRIPT)
sbom = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(sbom)


def test_sbom_has_one_hashed_component_per_file(tmp_path):
    (tmp_path / "a").write_text("a")
    nested = tmp_path / "d"
    nested.mkdir()
    (nested / "b").write_text("b")
    document = sbom.build_sbom("1.2.3", [str(tmp_path)])
    assert document["bomFormat"] == "CycloneDX"
    assert document["metadata"]["component"]["version"] == "1.2.3"
    assert len(document["components"]) == 2
    assert all(len(c["hashes"][0]["content"]) == 64 for c in document["components"])


def test_release_bundle_builds_verifies_and_rejects_tampering(tmp_path):
    out = tmp_path / "dist"
    subprocess.run(["bash", "tools/build-release-bundle.sh", str(out)], cwd=ROOT, check=True)
    bundle = out / "session-optimizer.tar.gz"
    checksum = out / "session-optimizer.tar.gz.sha256"
    manifest = out / "EXECUTABLE-MANIFEST.sha256"
    document = json.loads((out / "session-optimizer.cdx.json").read_text())
    assert bundle.is_file() and manifest.read_text().strip()
    assert document["components"]

    good = subprocess.run(
        ["bash", "tools/verify-release-bundle.sh", str(bundle), str(checksum), str(manifest)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert good.returncode == 0, good.stderr
    with bundle.open("ab") as handle:
        handle.write(b"tampered")
    bad = subprocess.run(
        ["bash", "tools/verify-release-bundle.sh", str(bundle), str(checksum), str(manifest)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert bad.returncode == 1
    assert "INTEGRITY FAILURE" in bad.stderr


def test_release_verifier_rejects_path_traversal_before_extraction(tmp_path):
    bundle = tmp_path / "unsafe.tar.gz"
    payload = b"must not escape"
    with tarfile.open(bundle, "w:gz") as archive:
        member = tarfile.TarInfo("../escaped.txt")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    checksum = tmp_path / "unsafe.tar.gz.sha256"
    checksum.write_text(f"{hashlib.sha256(bundle.read_bytes()).hexdigest()}  {bundle.name}\n")
    manifest = tmp_path / "manifest.sha256"
    manifest.write_text("")

    result = subprocess.run(
        ["bash", "tools/verify-release-bundle.sh", str(bundle), str(checksum), str(manifest)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "unsafe archive path" in result.stderr
    assert not (tmp_path.parent / "escaped.txt").exists()
