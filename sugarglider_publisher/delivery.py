"""Read-only HTTPS byte delivery checks for an independently pinned V2 inventory."""

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from urllib.request import HTTPRedirectHandler, Request, build_opener

from sugarglider.offline_regions.publication_records import (
    bounded_record,
    digest,
    unique_object,
)

from sugarglider_publisher.prepare import PublicationInventory, PublishedFile, base_url

ANDROID_ORIGIN = "https://appassets.androidplatform.net"


class Headers(Protocol):
    def get_all(self, name: str) -> list[str] | None: ...


class Response(Protocol):
    status: int
    headers: Headers

    def read(self, size: int = -1) -> bytes: ...
    def close(self) -> None: ...


type OpenResponse = Callable[[Request], Response]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        raise ValueError("publication delivery must not redirect")


def _open(request: Request) -> Response:
    # Normal platform trust; no insecure mode, cookies, auth, TLS override or retry.
    return cast(Response, build_opener(_NoRedirect()).open(request, timeout=30))


def _header(response: Response, name: str) -> str | None:
    values = response.headers.get_all(name) or []
    if len(values) > 1:
        raise ValueError("ambiguous delivery header")
    return values[0] if values else None


def _fetch(
    request: Request,
    file: PublishedFile,
    opener: OpenResponse,
    deadline: float,
    *,
    prefix: bytes | None = None,
) -> bytes:
    response = opener(request)
    try:
        ranged = prefix is not None
        length = 127 if ranged else file.byte_size
        if response.status != (206 if ranged else 200):
            raise ValueError("unexpected publication HTTP status")
        if _header(response, "Content-Length") != str(length):
            raise ValueError("incorrect delivery Content-Length")
        if _header(response, "Content-Encoding") not in {None, "identity"}:
            raise ValueError("publication bytes must not be transformed")
        if file.path.startswith("v2/") and _header(
            response, "Access-Control-Allow-Origin"
        ) not in {"*", ANDROID_ORIGIN}:
            raise ValueError("publication lacks Android-origin CORS")
        if (
            ranged
            and _header(response, "Content-Range") != f"bytes 0-126/{file.byte_size}"
        ):
            raise ValueError("incorrect PMTiles Content-Range")
        hasher, received, first = hashlib.sha256(), 0, b""
        while True:
            if time.monotonic() > deadline:
                raise ValueError("publication delivery check timed out")
            chunk = response.read(min(1_048_576, length - received + 1))
            if not chunk:
                break
            received += len(chunk)
            if received > length:
                raise ValueError("publication response exceeds approved size")
            hasher.update(chunk)
            if len(first) < 127:
                first += chunk[: 127 - len(first)]
        if received != length or (not ranged and hasher.hexdigest() != file.sha256):
            raise ValueError("truncated or corrupt publication response")
        if ranged and first != prefix:
            raise ValueError("PMTiles range differs from its complete file")
        return first
    finally:
        response.close()


@dataclass(frozen=True)
class DeliveryResult:
    files: int
    bytes: int
    range_checks: int


def verify_delivery(
    publication: Path,
    trusted_inventory_sha256: str,
    expected_base_url: str,
    *,
    opener: OpenResponse = _open,
) -> DeliveryResult:
    """A transport result never authorizes a build or writes to a host."""
    encoded = bounded_record(publication / "inventory.json", 65_536)
    if digest(encoded) != trusted_inventory_sha256:
        raise ValueError("publication inventory lacks its independent digest")
    json.loads(encoded, object_pairs_hook=unique_object)
    inventory = PublicationInventory.model_validate_json(encoded)
    if inventory.base_url != base_url(expected_base_url):
        raise ValueError("delivery origin differs from the reviewed publication")
    site_base = inventory.base_url.removesuffix("v2/")
    deadline, ranges = time.monotonic() + 600, 0
    for file in inventory.files:
        url = site_base + file.path
        headers = {"Origin": ANDROID_ORIGIN, "Accept-Encoding": "identity"}
        first = _fetch(Request(url, headers=headers), file, opener, deadline)
        if file.path.endswith("/map/basemap.pmtiles"):
            _fetch(
                Request(url, headers={**headers, "Range": "bytes=0-126"}),
                file,
                opener,
                deadline,
                prefix=first,
            )
            ranges += 1
    return DeliveryResult(
        len(inventory.files), sum(f.byte_size for f in inventory.files), ranges
    )
