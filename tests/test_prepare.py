import json
import shutil
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sugarglider.offline_regions.models import canonical_json
from sugarglider.offline_regions.publication_records import digest

from sugarglider_publisher.prepare import PublicationInputs, base_url, prepare


def tree(path: Path) -> dict[str, bytes]:
    return {
        p.relative_to(path).as_posix(): p.read_bytes()
        for p in path.rglob("*")
        if p.is_file()
    }


def test_complete_v2_site_and_compatible_rollback(
    publications: tuple[PublicationInputs, PublicationInputs], tmp_path: Path
) -> None:
    first, second = publications
    old, new, rollback = tmp_path / "old", tmp_path / "new", tmp_path / "rollback"
    before = first.archive.read_bytes()
    first_inventory = prepare(first, old)
    first_tree = tree(old)
    second_inventory = prepare(second, new)
    rollback_inventory = prepare(first, rollback)
    assert (
        first_inventory == rollback_inventory
        and first_inventory.build_id != second_inventory.build_id
    )
    assert tree(rollback) == first_tree and tree(old) == first_tree
    assert first.archive.read_bytes() == before
    assert (old / "site/privacy/index.html").read_bytes() == first.policy.read_bytes()
    assert len(first_inventory.files) == 20
    assert all(
        row.path in {".nojekyll", "index.html", "privacy/index.html"}
        or row.path.startswith("v2/")
        for row in first_inventory.files
    )
    catalog = json.loads((new / "site/v2/catalog.json").read_bytes())
    assert (
        len(catalog["regions"]) == 1
        and catalog["regions"][0]["build_id"] == second_inventory.build_id
    )
    regions = list((new / "site/v2/regions").glob("*/*"))
    assert len(regions) == 1
    assert regions[0].name == second_inventory.build_id
    receipt = json.loads((old / "receipt.json").read_bytes())
    assert receipt["status"] == "prepared-not-published"
    assert receipt["inventory_sha256"] == digest((old / "inventory.json").read_bytes())


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/v2/",
        "https://example.test/",
        "https://example.test/v2",
        "https://user:pass@example.test/v2/",
        "https://example.test/v2/?x=1",
        "https://example.test/v2/#x",
        "https://example.test/a/../v2/",
    ],
)
def test_noncanonical_or_insecure_base_url(url: str) -> None:
    with pytest.raises(ValueError):
        base_url(url)


@pytest.mark.parametrize(
    "part",
    [
        "archive-digest",
        "approval-digest",
        "approval-content",
        "evidence-content",
        "policy-digest",
        "policy-content",
        "missing-policy",
        "older-evidence",
        "missing-basis",
        "wrong-host",
    ],
)
def test_exact_independent_inputs_fail_before_output(
    publications: tuple[PublicationInputs, PublicationInputs], tmp_path: Path, part: str
) -> None:
    inputs = publications[0]
    if part == "archive-digest":
        inputs = replace(inputs, archive_sha256="0" * 64)
    elif part == "approval-digest":
        inputs = replace(inputs, trusted_approval_sha256="0" * 64)
    elif part == "policy-digest":
        inputs = replace(inputs, policy_sha256="0" * 64)
    elif part == "wrong-host":
        inputs = replace(inputs, base_url="https://wrong.test/v2/")
    elif part in {"policy-content", "missing-policy"}:
        policy = tmp_path / "policy.html"
        if part == "policy-content":
            policy.write_bytes(inputs.policy.read_bytes() + b"changed")
        inputs = replace(inputs, policy=policy)
    else:
        original = inputs.approval if part == "approval-content" else inputs.evidence
        path = tmp_path / original.name
        value: dict[str, Any] = json.loads(original.read_bytes())
        if part == "approval-content":
            value["operator"] = "tampered"
        elif part == "older-evidence":
            value["schema_version"] = 2
        elif part == "missing-basis":
            del value["basis"]["supplementary_notices"]
        else:
            value["basis"]["supplementary_notices"]["notice_text_sha256"] = "0" * 64
        path.write_text(json.dumps(value))
        inputs = (
            replace(inputs, approval=path)
            if part == "approval-content"
            else replace(inputs, evidence=path)
        )
    with pytest.raises((ValueError, OSError)):
        prepare(inputs, tmp_path / "forbidden")
    assert not (tmp_path / "forbidden").exists()
    assert not list(tmp_path.glob(".v2-prepare-*"))


@pytest.mark.parametrize(
    "part",
    [
        "SOURCE_NOTICES.txt",
        "README.txt",
        "index.html",
        "missing-notices",
        "four-components",
        "map",
        "routing",
        "pois",
        "nature",
        "elevation",
        "unknown-path",
        "duplicate-path",
        "symlink",
        "oversized-file",
    ],
)
def test_v2_bundle_corruption_and_missing_files(
    publications: tuple[PublicationInputs, PublicationInputs], tmp_path: Path, part: str
) -> None:
    inputs = publications[0]
    archive = tmp_path / "changed.zip"
    with (
        zipfile.ZipFile(inputs.archive) as source,
        zipfile.ZipFile(archive, "w") as target,
    ):
        names = source.namelist()
        for entry in source.infolist():
            if (
                part in {"symlink", "oversized-file"}
                and entry.filename == "SOURCE_NOTICES.txt"
            ):
                continue
            if part == "missing-notices" and entry.filename == "SOURCE_NOTICES.txt":
                continue
            value = source.read(entry)
            if entry.filename == part:
                value += b"UNREVIEWED claim"
            if (
                part == "four-components"
                and entry.filename.endswith("/manifest.json")
                and entry.filename.count("/") == 3
            ):
                parsed = json.loads(value)
                parsed["components"].pop("elevation")
                value = json.dumps(parsed).encode()
            if (
                part in {"map", "routing", "pois", "nature", "elevation"}
                and f"/{part}/" in entry.filename
            ):
                value = bytes([value[0] ^ 1]) + value[1:]
            target.writestr(entry, value)
        if part in {"unknown-path", "duplicate-path", "symlink", "oversized-file"}:
            name = (
                names[0]
                if part == "duplicate-path"
                else "../outside"
                if part == "unknown-path"
                else "SOURCE_NOTICES.txt"
            )
            entry = zipfile.ZipInfo(name)
            entry.external_attr = (0o120777 if part == "symlink" else 0o100644) << 16
            value = b"x" * 131073 if part == "oversized-file" else b"bad"
            if part == "duplicate-path":
                with pytest.warns(UserWarning, match="Duplicate name"):
                    target.writestr(entry, value)
            else:
                target.writestr(entry, value)
    changed = replace(
        inputs, archive=archive, archive_sha256=digest(archive.read_bytes())
    )
    with pytest.raises((ValueError, OSError)):
        prepare(changed, tmp_path / "forbidden")
    assert not (tmp_path / "forbidden").exists()
    assert not list(tmp_path.glob(".v2-prepare-*"))


def test_existing_output_symlinks_and_interrupted_verification(
    publications: tuple[PublicationInputs, PublicationInputs], tmp_path: Path
) -> None:
    inputs = publications[0]
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "sentinel").write_bytes(b"keep")
    for output in [existing, tmp_path / "link"]:
        if output.name == "link":
            output.symlink_to(existing, target_is_directory=True)
        with pytest.raises(ValueError):
            prepare(inputs, output)
    assert tree(existing) == {"sentinel": b"keep"}
    with patch(
        "sugarglider_publisher.prepare.unpack_distribution",
        side_effect=OSError("synthetic interruption"),
    ):
        with pytest.raises(OSError):
            prepare(inputs, tmp_path / "interrupted")
    assert not (tmp_path / "interrupted").exists()
    assert not list(tmp_path.glob(".v2-prepare-*"))
    with patch(
        "sugarglider_publisher.prepare.shutil.disk_usage",
        return_value=shutil.disk_usage(tmp_path)._replace(free=0),
    ):
        with pytest.raises(ValueError, match="space"):
            prepare(inputs, tmp_path / "full")


def test_interrupted_output_transfer_removes_only_owned_output(
    publications: tuple[PublicationInputs, PublicationInputs],
    tmp_path: Path,
) -> None:
    original = Path.rename

    def interrupted(path: Path, target: Path) -> Path:
        if path.name == "inventory.json":
            raise OSError("synthetic disk error after site transfer")
        return original(path, target)

    sentinel = tmp_path / "keep"
    sentinel.write_bytes(b"unchanged")
    with patch.object(Path, "rename", interrupted):
        with pytest.raises(OSError, match="synthetic disk error"):
            prepare(publications[0], tmp_path / "failed")
    assert sentinel.read_bytes() == b"unchanged"
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".v2-prepare-*"))


@pytest.mark.parametrize(
    "fault", ["decision", "terms", "stale-build", "pending-lineage", "notice-registry"]
)
def test_even_independently_pinned_invalid_decisions_are_rejected(
    publications: tuple[PublicationInputs, PublicationInputs],
    tmp_path: Path,
    fault: str,
) -> None:
    inputs = publications[0]
    evidence = json.loads(inputs.evidence.read_bytes())
    approval = json.loads(inputs.approval.read_bytes())
    if fault == "decision":
        approval["decision"] = "reject-publication"
    elif fault == "terms":
        approval["license_terms_accepted"] = False
    elif fault == "stale-build":
        approval["release_build_id"] = json.loads(
            publications[1].approval.read_bytes()
        )["release_build_id"]
    else:
        notices = evidence["basis"]["supplementary_notices"]
        if fault == "pending-lineage":
            notices["lineage_review"] = "pending"
        else:
            notices["registry_sha256"] = "0" * 64
        approval["evidence_sha256"] = digest(canonical_json(evidence))
    evidence_path, approval_path = (
        tmp_path / "evidence.json",
        tmp_path / "approval.json",
    )
    evidence_path.write_bytes(canonical_json(evidence))
    approval_path.write_bytes(canonical_json(approval))
    changed = replace(
        inputs,
        evidence=evidence_path,
        approval=approval_path,
        trusted_approval_sha256=digest(approval_path.read_bytes()),
    )
    with pytest.raises(ValueError):
        prepare(changed, tmp_path / "forbidden")
    assert not (tmp_path / "forbidden").exists()
