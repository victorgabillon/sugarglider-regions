"""Standalone, standard-library verifier for an explicitly approved site archive."""

import argparse
import hashlib
import re
import shutil
import stat
import zipfile
from pathlib import Path

MAX_BYTES = 950_000_000  # Below the published 1 GB GitHub Pages site limit.
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
        if not 1 <= len(members) <= 128:
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
