"""Standalone, standard-library verifier for an explicitly approved site archive."""

import argparse
import hashlib
import json
import math
import re
import shutil
import stat
import zipfile
from pathlib import Path
from typing import NoReturn, cast
from urllib.parse import urlsplit

MAX_BYTES = 950_000_000  # Below the published 1 GB GitHub Pages site limit.
MAX_FILES = 128  # Bound archive metadata; not a special-case retained-version limit.
REGIONAL_FILES = {
    "manifest.json",
    "map/manifest.json",
    "map/basemap.pmtiles",
    "routing/manifest.json",
    "routing/valhalla_tiles.tar",
    "pois/index.json.gz",
    "nature/index.json.gz",
}
ROOT_FILES = {".nojekyll", "README.txt", "index.html", "catalog.json"}


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate publication JSON field")
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(f"invalid publication JSON constant: {value}")


def _read_object(bundle: zipfile.ZipFile, name: str) -> dict[str, object]:
    if bundle.getinfo(name).file_size > 65_536:
        raise ValueError("publication JSON exceeds its size budget")
    value = json.loads(
        bundle.read(name),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(value, dict):
        raise ValueError("publication JSON must be an object")
    return cast(dict[str, object], value)


def _text(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and value.strip() == value
        and all(ord(character) >= 32 and ord(character) != 127 for character in value)
    )


def _verify_identities(bundle: zipfile.ZipFile, regions: dict[str, set[str]]) -> None:
    """Check the one advertised row and every version's immutable path identity."""
    catalog = _read_object(bundle, "catalog.json")
    offerings = catalog.get("regions")
    if (
        set(catalog) != {"schema_version", "regions"}
        or type(catalog.get("schema_version")) is not int
        or catalog["schema_version"] != 1
        or not isinstance(offerings, list)
        or len(offerings) != 1
        or not isinstance(offerings[0], dict)
    ):
        raise ValueError("invalid publication catalog")
    offering = cast(dict[str, object], offerings[0])
    if set(offering) != {
        "region_id",
        "build_id",
        "display_name",
        "description",
        "bounds",
        "download_bytes",
        "manifest_url",
    }:
        raise ValueError("invalid publication catalog fields")
    current = f"regions/{offering['region_id']}/{offering['build_id']}"
    if current not in regions:
        raise ValueError("catalog build identity is missing from publication")
    url = offering["manifest_url"]
    if not isinstance(url, str) or not _text(url, 1_500):
        raise ValueError("invalid catalog manifest URL")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not re.fullmatch(r"[a-z0-9.-]+(?::[1-9][0-9]{0,4})?", parsed.netloc)
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith(f"/{current}/manifest.json")
        or any(
            not re.fullmatch(r"[A-Za-z0-9._~-]+", part) or ".." in part or part == "."
            for part in parsed.path[1:].split("/")
        )
        or (parsed.port is not None and not 1 <= parsed.port <= 65_535)
    ):
        raise ValueError("invalid catalog manifest URL/identity")
    bounds = offering["bounds"]
    if (
        not isinstance(bounds, list)
        or len(bounds) != 4
        or any(
            type(value) not in (int, float) or not math.isfinite(value)
            for value in bounds
        )
        or not (
            -180 <= bounds[0] < bounds[2] <= 180 and -90 <= bounds[1] < bounds[3] <= 90
        )
        or not _text(offering["display_name"], 120)
        or not _text(offering["description"], 500)
        or type(offering["download_bytes"]) is not int
    ):
        raise ValueError("invalid publication catalog values")
    for relative, files in regions.items():
        manifest = _read_object(bundle, f"{relative}/manifest.json")
        _, region_id, build_id = relative.split("/")
        if (
            region_id != offering["region_id"]
            or manifest.get("region_id") != region_id
            or manifest.get("build_id") != build_id
            or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1
        ):
            raise ValueError("regional manifest path/build identity mismatch")
        if relative == current and (
            manifest.get("bounds") != bounds
            or manifest.get("display_name") != offering["display_name"]
            or offering["download_bytes"]
            != sum(
                bundle.getinfo(f"{relative}/{name}").file_size
                for name in files - {"manifest.json"}
            )
        ):
            raise ValueError("catalog differs from advertised manifest/content")


def unpack_distribution(archive: Path, output: Path, expected_sha256: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise ValueError(
            "expected SHA-256 must contain 64 lowercase hexadecimal digits"
        )
    if not 0 < archive.stat().st_size <= MAX_BYTES:
        raise ValueError("publication archive exceeds the static host size budget")
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected_sha256:
            raise ValueError("publication archive checksum mismatch")
    if any(path.is_symlink() for path in (output, *output.absolute().parents)):
        raise ValueError("symbolic-link output path is not allowed")
    if output.exists():
        raise FileExistsError(output)
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        names: set[str] = set()
        regions: dict[str, set[str]] = {}
        total = 0
        if not 1 <= len(members) <= MAX_FILES:
            raise ValueError("invalid publication file count")
        for member in members:
            name = member.filename
            mode = member.external_attr >> 16
            if (
                name in names
                or member.compress_type != zipfile.ZIP_STORED
                or member.flag_bits & 1
                or stat.S_IFMT(mode) != stat.S_IFREG
                or member.is_dir()
            ):
                raise ValueError("invalid publication archive entry")
            names.add(name)
            if name not in ROOT_FILES:
                match = re.fullmatch(
                    r"(regions/[a-z0-9][a-z0-9_-]{0,63}/[a-f0-9]{64})/(.+)", name
                )
                if match is None or match[2] not in REGIONAL_FILES:
                    raise ValueError("unexpected publication path")
                regions.setdefault(match[1], set()).add(match[2])
            total += member.file_size
            if member.file_size < 0 or total > MAX_BYTES:
                raise ValueError(
                    "publication content exceeds the static host size budget"
                )
        if not ROOT_FILES <= names or not regions:
            raise ValueError("publication is missing its catalog or attribution")
        if any(files != REGIONAL_FILES for files in regions.values()):
            raise ValueError("publication has an incomplete regional version")
        _verify_identities(bundle, regions)
        output.mkdir()  # Exclusive output ownership; extraction never replaces data.
        try:
            for member in members:
                target = output / member.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as reader, target.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, length=1_048_576)
            return output
        except BaseException:
            shutil.rmtree(output)
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("expected_sha256")
    arguments = parser.parse_args()
    unpack_distribution(arguments.archive, arguments.output, arguments.expected_sha256)
    print("Approved archive checksum and confined static files verified.")


if __name__ == "__main__":
    main()
