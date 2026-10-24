import io
import json
import ssl
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request

import pytest
from sugarglider.offline_regions.publication_records import digest

from sugarglider_publisher.delivery import ANDROID_ORIGIN, Headers, verify_delivery
from tests.synthetic import BASE


class MemoryResponse:
    def __init__(self, data: bytes, status: int, headers: Message) -> None:
        self.status = status
        self.headers: Headers = headers
        self.body = io.BytesIO(data)
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self.body.read(size)

    def close(self) -> None:
        self.closed = True


class MemoryHost:
    def __init__(self, publication: Path, fault: str = "") -> None:
        self.publication = publication
        self.fault = fault
        self.responses: list[MemoryResponse] = []
        self.calls: list[Request] = []

    def __call__(self, request: Request) -> MemoryResponse:
        self.calls.append(request)
        assert request.full_url.startswith(BASE.removesuffix("v2/"))
        assert request.get_header("Origin") == ANDROID_ORIGIN
        assert request.get_header("Accept-encoding") == "identity"
        relative = urlsplit(request.full_url).path.removeprefix("/sugarglider-regions/")
        data = (self.publication / "site" / relative).read_bytes()
        length, status = len(data), 200
        headers = Message()
        headers["Access-Control-Allow-Origin"] = ANDROID_ORIGIN
        ranged = request.get_header("Range") is not None
        if ranged:
            assert request.get_header("Range") == "bytes=0-126"
            status = 206
            headers["Content-Range"] = f"bytes 0-126/{length}"
            data = data[:127]
        headers["Content-Length"] = str(len(data))
        if relative.startswith("v2/"):
            if self.fault == "status":
                status = 503
            elif self.fault == "redirect":
                status = 302
            elif self.fault == "length":
                headers.replace_header("Content-Length", "999")
            elif self.fault == "duplicate-header":
                headers["Content-Length"] = str(len(data))
            elif self.fault == "encoding":
                headers["Content-Encoding"] = "gzip"
            elif self.fault in {"cors", "wrong-cors"}:
                del headers["Access-Control-Allow-Origin"]
                if self.fault == "wrong-cors":
                    headers["Access-Control-Allow-Origin"] = "https://wrong.test"
            elif self.fault == "short" and data:
                data = data[:-1]
            elif self.fault == "excess":
                data += b"extra"
            elif self.fault == "hash" and data:
                data = bytes([data[0] ^ 1]) + data[1:]
            elif self.fault == "transport":
                raise URLError("synthetic disconnected transport")
            elif self.fault == "tls":
                raise ssl.SSLCertVerificationError("synthetic untrusted certificate")
            if ranged and self.fault == "range-status":
                status = 200
            elif ranged and self.fault == "range-header":
                headers.replace_header("Content-Range", f"bytes 1-127/{length}")
            elif ranged and self.fault == "range-bytes":
                data = bytes([data[0] ^ 1]) + data[1:]
        response = MemoryResponse(data, status, headers)
        self.responses.append(response)
        return response


def test_all_installation_files_hashes_and_range(prepared: Path) -> None:
    host = MemoryHost(prepared)
    result = verify_delivery(
        prepared, digest((prepared / "inventory.json").read_bytes()), BASE, opener=host
    )
    inventory = json.loads((prepared / "inventory.json").read_bytes())
    assert result.files == len(inventory["files"])
    assert result.bytes == sum(f["byte_size"] for f in inventory["files"])
    assert result.range_checks == 1
    assert len(host.calls) == result.files + 1
    assert all(response.closed for response in host.responses)


@pytest.mark.parametrize(
    "fault",
    [
        "status",
        "redirect",
        "length",
        "duplicate-header",
        "encoding",
        "cors",
        "wrong-cors",
        "short",
        "excess",
        "hash",
        "transport",
        "tls",
        "range-status",
        "range-header",
        "range-bytes",
    ],
)
def test_bad_delivery_fails_closed(prepared: Path, fault: str) -> None:
    host = MemoryHost(prepared, fault)
    with pytest.raises((ValueError, OSError)):
        verify_delivery(
            prepared,
            digest((prepared / "inventory.json").read_bytes()),
            BASE,
            opener=host,
        )
    assert all(response.closed for response in host.responses)
    assert len(host.calls) <= 21  # No retries after any failure.


def test_untrusted_inventory_and_wrong_origin_do_not_call_host(prepared: Path) -> None:
    host = MemoryHost(prepared)
    with pytest.raises(ValueError, match="digest"):
        verify_delivery(prepared, "0" * 64, BASE, opener=host)
    with pytest.raises(ValueError, match="origin"):
        verify_delivery(
            prepared,
            digest((prepared / "inventory.json").read_bytes()),
            "https://wrong.test/v2/",
            opener=host,
        )
    assert not host.calls


def test_delivery_has_global_deadline(prepared: Path) -> None:
    host = MemoryHost(prepared)
    with patch("sugarglider_publisher.delivery.time.monotonic", side_effect=[0, 601]):
        with pytest.raises(ValueError, match="timed out"):
            verify_delivery(
                prepared,
                digest((prepared / "inventory.json").read_bytes()),
                BASE,
                opener=host,
            )
    assert len(host.calls) == 1
    assert host.responses[0].closed
