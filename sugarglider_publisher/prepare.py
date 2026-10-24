"""Prepare one exact approved V2 snapshot and policy; never mutate a live host."""

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator
from sugarglider.offline_regions.distribution import distribution_base_url
from sugarglider.offline_regions.ign_publication import (
    IGNCompletePublicationBasis,
    IGNPublicationEvidence,
)
from sugarglider.offline_regions.models import (
    Sha256,
    StrictModel,
    canonical_json,
    safe_path,
)
from sugarglider.offline_regions.publication import verify_approval
from sugarglider.offline_regions.publication_records import (
    bounded_record,
    digest,
    unique_object,
)
from sugarglider.offline_regions.release_catalog import ReleaseCatalog
from sugarglider.offline_regions.unpack_distribution import (
    MAX_BYTES,
    MAX_FILES,
    unpack_distribution,
)

from sugarglider_publisher.policy import read_policy


def base_url(value: str) -> str:
    result = distribution_base_url(value)
    if not result.endswith("/v2/"):
        raise ValueError("V2 publication requires the canonical /v2/ base URL")
    return result


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class PublishedFile(StrictModel):
    path: Annotated[str, Field(min_length=1, max_length=256)]
    byte_size: Annotated[int, Field(ge=0, le=MAX_BYTES)]
    sha256: Sha256

    @model_validator(mode="after")
    def confined_path(self) -> Self:
        if self.path not in {".nojekyll", "v2/.nojekyll"}:
            safe_path(self.path)
        if not (
            self.path in {".nojekyll", "index.html", "privacy/index.html"}
            or self.path.startswith("v2/")
        ):
            raise ValueError("file is outside the V2 publication layout")
        return self


class PublicationInventory(StrictModel):
    schema_version: Literal[1] = 1
    base_url: str
    build_id: Sha256
    files: Annotated[
        tuple[PublishedFile, ...], Field(min_length=1, max_length=MAX_FILES)
    ]

    @model_validator(mode="after")
    def exact_layout(self) -> Self:
        base_url(self.base_url)
        paths = tuple(f.path for f in self.files)
        if (
            paths != tuple(sorted(set(paths)))
            or sum(f.byte_size for f in self.files) > MAX_BYTES
        ):
            raise ValueError("duplicate, unordered or oversized publication inventory")
        if not {
            ".nojekyll",
            "index.html",
            "privacy/index.html",
            "v2/catalog.json",
            "v2/SOURCE_NOTICES.txt",
        } <= set(paths):
            raise ValueError("incomplete V2 publication inventory")
        return self


@dataclass(frozen=True)
class PublicationInputs:
    archive: Path
    archive_sha256: str
    evidence: Path
    approval: Path
    trusted_approval_sha256: str
    policy: Path
    policy_sha256: str
    base_url: str


def prepare(inputs: PublicationInputs, output: Path) -> PublicationInventory:
    """Verify before claiming a fresh output; never replace an existing path."""
    base = base_url(inputs.base_url)
    evidence_bytes = bounded_record(inputs.evidence)
    raw = json.loads(evidence_bytes, object_pairs_hook=unique_object)
    if (
        not isinstance(raw, dict)
        or type(raw.get("schema_version")) is not int
        or raw["schema_version"] != 3
    ):
        raise ValueError(
            "only current complete-notice V2 evidence schema 3 is supported"
        )
    evidence = IGNPublicationEvidence.model_validate_json(evidence_bytes)
    if not isinstance(evidence.basis, IGNCompletePublicationBasis):
        raise ValueError("V2 requires the complete bound notice basis")
    approval_bytes = bounded_record(inputs.approval, 4096)
    verify_approval(evidence, approval_bytes, inputs.trusted_approval_sha256)
    policy = read_policy(inputs.policy, inputs.policy_sha256)
    output = output.absolute()
    if output.exists() or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("publication output must be a fresh path without symlinks")
    if not output.parent.is_dir():
        raise ValueError("publication output requires an existing parent")
    if inputs.archive.is_symlink() or any(
        p.is_symlink() for p in inputs.archive.absolute().parents
    ):
        raise ValueError("symbolic-link input archive")
    if (
        shutil.disk_usage(output.parent).free
        < inputs.archive.stat().st_size * 2 + 1_048_576
    ):
        raise ValueError("insufficient space for V2 verification")
    owned_output = False
    with tempfile.TemporaryDirectory(
        prefix=".v2-prepare-", dir=output.parent
    ) as temporary:
        stage = Path(temporary)
        site = stage / "site"
        site.mkdir()
        try:
            unpack_distribution(
                inputs.archive,
                site / "v2",
                inputs.archive_sha256,
                publication=True,
                evidence_path=inputs.evidence,
                approval_path=inputs.approval,
                trusted_approval_sha256=inputs.trusted_approval_sha256,
            )
            # The shared unpacker already verifies every component and notice.
            # Bind its verified catalog to the intended host without rescanning data.
            catalog = ReleaseCatalog.model_validate_json(
                bounded_record(site / "v2/catalog.json", 65_536)
            )
            if len(catalog.regions) != 1:
                raise ValueError("one current V2 regional snapshot is required")
            row = catalog.regions[0]
            if row.manifest_url != (
                f"{base}regions/{row.region_id}/{row.build_id}/manifest.json"
            ):
                raise ValueError("catalog URL differs from the intended V2 host")
            (site / "privacy").mkdir()
            (site / "privacy/index.html").write_bytes(policy)
            (site / ".nojekyll").write_bytes(b"")
            (site / "index.html").write_text(
                '<!doctype html><html lang="en"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                "<title>Sugarglider V2 regional data</title>"
                "<h1>Sugarglider V2 regional data</h1>"
                '<p><a href="v2/">Complete regional data and source notices</a></p>'
                '<p><a href="privacy/">Privacy policy</a></p></html>\n',
                encoding="utf-8",
            )
            inventory = PublicationInventory(
                base_url=base,
                build_id=catalog.regions[0].build_id,
                files=tuple(
                    PublishedFile(
                        path=p.relative_to(site).as_posix(),
                        byte_size=p.stat().st_size,
                        sha256=file_sha256(p),
                    )
                    for p in sorted(site.rglob("*"))
                    if p.is_file()
                ),
            )
            encoded = canonical_json(inventory.model_dump(mode="json"))
            (stage / "inventory.json").write_bytes(encoded)
            (stage / "receipt.json").write_bytes(
                canonical_json(
                    {
                        "status": "prepared-not-published",
                        "build_id": inventory.build_id,
                        "archive_sha256": inputs.archive_sha256,
                        "evidence_sha256": digest(
                            canonical_json(evidence.model_dump(mode="json"))
                        ),
                        "approval_sha256": inputs.trusted_approval_sha256,
                        "policy_sha256": inputs.policy_sha256,
                        "inventory_sha256": digest(encoded),
                        "base_url": base,
                    }
                )
            )
            # mkdir is the exclusive claim: even a concurrent creator is never replaced.
            output.mkdir()
            owned_output = True
            for name in ("site", "inventory.json", "receipt.json"):
                (stage / name).rename(output / name)
            return inventory
        except BaseException:
            if owned_output:
                shutil.rmtree(output)
            raise
