"""Tiny PMTiles/TAR, zero HGT and empty indexes, verified by the real contract."""

import gzip
import io
import json
import struct
import tarfile
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any
from unittest.mock import patch

from sugarglider.nature.models import NatureIndexDocument, NatureIndexMetadata
from sugarglider.offline_regions import source_notices
from sugarglider.offline_regions.distribution import prepare_distribution
from sugarglider.offline_regions.elevation_component import (
    ElevationManifest,
    ElevationTile,
    IGNDerivation,
)
from sugarglider.offline_regions.elevation_layout import elevation_paths
from sugarglider.offline_regions.ign_publication import (
    IGNCompletePublicationBasis,
    IGNPublicationEvidence,
)
from sugarglider.offline_regions.models import (
    RegionalManifest,
    build_identity,
    canonical_json,
)
from sugarglider.offline_regions.publication import OperatorApproval, release_manifests
from sugarglider.offline_regions.publication_records import digest
from sugarglider.offline_regions.validation import verify_region
from sugarglider.pois.models import (
    PoiBuildConfiguration,
    PoiIndexDocument,
    PoiIndexMetadata,
)

from sugarglider_publisher.prepare import PublicationInputs

BASE = "https://data.example.test/sugarglider-regions/v2/"
TEMPLATE = Path(__file__).parent / "fixtures/synthetic_basis.json"


@lru_cache(maxsize=1)
def hgt() -> bytes:
    return gzip.compress(bytes(25_934_402), mtime=0)


def _components(root: Path, template: dict[str, Any], nonce: int) -> None:
    private = template["basis"]["private_manifest"]
    bounds = tuple(private["bounds"])
    for name in ("map", "routing", "pois", "nature", "elevation"):
        (root / name).mkdir(parents=True)
    header = bytearray(127)
    header[:8] = b"PMTiles\x03"
    for position, offset, length in (
        (8, 127, 1),
        (24, 128, 2),
        (40, 130, 0),
        (56, 130, 1),
    ):
        struct.pack_into("<QQ", header, position, offset, length)
    for position in (72, 80, 88):
        struct.pack_into("<Q", header, position, 1)
    header[97:102] = bytes((1, 1, 1, 0, 15))
    struct.pack_into("<iiii", header, 102, *(round(v * 10_000_000) for v in bounds))
    archive = bytes(header) + b"\x00{}\x00"
    (root / "map/basemap.pmtiles").write_bytes(archive)
    (root / "map/manifest.json").write_bytes(
        canonical_json(
            dict(
                schema_version=1,
                pack_id=private["components"]["map"]["component_id"],
                display_name="SYNTHETIC map only",
                bounds=bounds,
                format="pmtiles-v3",
                tile_type="mvt",
                archive_filename="basemap.pmtiles",
                byte_size=len(archive),
                min_zoom=0,
                max_zoom=15,
                attribution="SYNTHETIC TEST ONLY",
                data_source="synthetic",
                build_id="synthetic-map",
            )
        )
    )
    (root / "routing/manifest.json").write_bytes(
        canonical_json(
            dict(
                schema_version=2,
                pack_id=private["components"]["routing"]["component_id"],
                engine="valhalla",
                engine_version="3.9.1",
                access_modes=["foot", "bicycle"],
                bounds=dict(
                    zip(("west", "south", "east", "north"), bounds, strict=True)
                ),
            )
        )
    )
    with tarfile.open(
        root / "routing/valhalla_tiles.tar", "w", format=tarfile.USTAR_FORMAT
    ) as tar:
        tile = (
            struct.pack("<Q", (1 << 62) | 2)
            + bytes(8)
            + b"3.9.1"
            + bytes(250)
            + bytes([nonce])
        )
        for name, value in [
            ("index.bin", struct.pack("<QLL", 1536, 2, 272)),
            ("2/000/000.gph", tile),
        ]:
            member = tarfile.TarInfo(name)
            member.size = len(value)
            tar.addfile(member, io.BytesIO(value))
    common = dict(
        source_basename=private["source"]["basename"],
        source_size_bytes=private["source"]["byte_size"],
        bounding_box=bounds,
        feature_count=0,
        category_counts={},
    )
    poi = PoiIndexDocument(
        metadata=PoiIndexMetadata(
            **common,
            potability_counts={},
            access_counts={},
            skipped_invalid_count=0,
            classifier_version="3",
            build_configuration=PoiBuildConfiguration(classifier_version="3"),
        ),
        features=(),
    )
    nature = NatureIndexDocument(
        metadata=NatureIndexMetadata(
            **common, reference_latitude=(bounds[1] + bounds[3]) / 2
        ),
        features=(),
    )
    for index_path, document in [
        ("pois/index.json.gz", poi),
        ("nature/index.json.gz", nature),
    ]:
        (root / index_path).write_bytes(
            gzip.compress(canonical_json(document.model_dump(mode="json")), mtime=0)
        )
    raw = hgt()
    tiles = []
    for relative in elevation_paths(bounds):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        tiles.append(
            ElevationTile(
                path=relative.removeprefix("elevation/"),
                byte_size=len(raw),
                sha256=digest(raw),
                source_url="https://example.test/synthetic/" + path.name,
                acquired_at="2026-10-24T00:00:00Z",
                pixels_valid=3601**2,
                pixels_void=0,
                minimum_height_m=0,
                maximum_height_m=0,
            )
        )
    derivation = IGNDerivation.model_validate_json(
        canonical_json(template["basis"]["derivation"])
    )
    elevation = ElevationManifest(
        component_id=private["components"]["elevation"]["component_id"],
        bounds=bounds,
        publication_allowed=False,
        provenance_status="documented",
        vertical_datum="IGN69",
        derivation=derivation,
        tiles=tuple(tiles),
    )
    (root / "elevation/manifest.json").write_bytes(
        canonical_json(elevation.model_dump(mode="json"))
    )


@contextmanager
def synthetic_publications(
    directory: Path, base: str = BASE
) -> Iterator[tuple[PublicationInputs, PublicationInputs]]:
    """Only this test context recognizes synthetic PMTiles as its notice source."""
    template: dict[str, Any] = json.loads(TEMPLATE.read_bytes())
    values = []
    registry = source_notices.notice_registry()
    for nonce in (1, 2):
        work = directory / str(nonce)
        work.mkdir(parents=True)
        region = work / "region"
        _components(region, template, nonce)
        private_data = json.loads(canonical_json(template["basis"]["private_manifest"]))
        private_data["display_name"] = "SYNTHETIC regional fixture"
        for component in private_data["components"].values():
            for file in component["files"]:
                body = (region / file["path"]).read_bytes()
                file.update(byte_size=len(body), sha256=digest(body))
        private_data["build_id"] = build_identity(
            {k: v for k, v in private_data.items() if k != "build_id"}
        )
        private = RegionalManifest.model_validate_json(canonical_json(private_data))
        (region / "manifest.json").write_bytes(
            canonical_json(private.model_dump(mode="json"))
        )
        registry = registry.model_copy(
            update={"map_payload": private.components.map.files[1]}
        )
        with patch.object(source_notices, "notice_registry", return_value=registry):
            notices = source_notices.propose_supplementary_notices().model_copy(
                update={"lineage_review": "accept-documented-lineage-limit"}
            )
            basis_data = {
                **template["basis"],
                "private_manifest": private.model_dump(mode="json"),
                "graph_build_record_sha256": digest(
                    f"SYNTHETIC graph receipt {nonce}".encode()
                ),
                "supplementary_notices": notices.model_dump(mode="json"),
            }
            basis = IGNCompletePublicationBasis.model_validate_json(
                canonical_json(basis_data)
            )
            basis_sha = digest(canonical_json(basis.model_dump(mode="json")))
            public, elevation = release_manifests(
                region, private, ign_basis_sha256=basis_sha
            )
            evidence = IGNPublicationEvidence(
                schema_version=3,
                private_build_id=private.build_id,
                release_build_id=public.build_id,
                graph_sha256=private.components.routing.files[1].sha256,
                derived_tiles=private.components.elevation.files[1:],
                basis=basis,
            )
            (region / "elevation/manifest.json").write_bytes(elevation)
            (region / "manifest.json").write_bytes(
                canonical_json(public.model_dump(mode="json"))
            )
            verify_region(region)
            approval = OperatorApproval(
                decision="approve-publication",
                operator="SYNTHETIC TEST ONLY — NOT A HUMAN APPROVAL",
                approved_at="2026-10-24T00:00:00Z",
                evidence_sha256=digest(
                    canonical_json(evidence.model_dump(mode="json"))
                ),
                release_build_id=public.build_id,
                scope="exact-regional-build-and-derived-DEM-enriched-graph",
                license_terms_accepted=True,
            )
            evidence_path, approval_path = (
                work / "evidence.json",
                work / "approval.json",
            )
            evidence_path.write_bytes(canonical_json(evidence.model_dump(mode="json")))
            approval_path.write_bytes(canonical_json(approval.model_dump(mode="json")))
            prepare_distribution(
                region,
                work / "archive",
                base_url=base,
                description="SYNTHETIC TEST ONLY",
                evidence_path=evidence_path,
                approval_path=approval_path,
                trusted_approval_sha256=digest(approval_path.read_bytes()),
            )
        policy = work / "policy.html"
        policy.write_text(
            '<!doctype html><html lang="en" data-policy-status="approved" '
            'data-policy-contract="sugarglider-v2"><title>SYNTHETIC policy</title>'
            "<body>SYNTHETIC TEST ONLY</body></html>\n"
        )
        archive = work / "archive/sugarglider-regions-static.zip"
        values.append(
            PublicationInputs(
                archive=archive,
                archive_sha256=digest(archive.read_bytes()),
                evidence=evidence_path,
                approval=approval_path,
                trusted_approval_sha256=digest(approval_path.read_bytes()),
                policy=policy,
                policy_sha256=digest(policy.read_bytes()),
                base_url=base,
            )
        )
    with patch.object(source_notices, "notice_registry", return_value=registry):
        yield values[0], values[1]
