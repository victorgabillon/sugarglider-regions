"""Separate loopback-only acceptance; never part of network-free unit tests."""

import os
import ssl
import subprocess
import tempfile
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from urllib.error import URLError

from sugarglider.offline_regions.publication_records import digest

from sugarglider_publisher.delivery import ANDROID_ORIGIN, verify_delivery
from sugarglider_publisher.prepare import prepare
from tests.synthetic import synthetic_publications


@dataclass
class HostState:
    site: Path
    fault: str = ""
    requests: int = 0


def handler(state: HostState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            state.requests += 1
            assert self.headers["Origin"] == ANDROID_ORIGIN
            path = self.path.removeprefix("/synthetic/")
            assert self.path.startswith("/synthetic/") and ".." not in path
            data = (state.site / path).read_bytes()
            length, status, ranged = len(data), 200, self.headers["Range"] is not None
            if state.fault == "redirect":
                self.send_response(302)
                self.send_header("Location", "/synthetic/v2/catalog.json")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if ranged:
                assert self.headers["Range"] == "bytes=0-126"
                status, data = 206, data[:127]
                if state.fault == "range-status":
                    status = 200
                elif state.fault == "range-bytes":
                    data = bytes([data[0] ^ 1]) + data[1:]
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            if state.fault != "cors":
                self.send_header("Access-Control-Allow-Origin", ANDROID_ORIGIN)
            if ranged:
                self.send_header("Content-Range", f"bytes 0-126/{length}")
            self.end_headers()
            self.wfile.write(data[:-1] if state.fault == "short" else data)

    return Handler


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="synthetic-v2-https-") as temporary:
        root = Path(temporary)
        cert, key = root / "certificate.pem", root / "key.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=SYNTHETIC loopback only",
                "-addext",
                "subjectAltName=IP:127.0.0.1",
                "-addext",
                "basicConstraints=critical,CA:TRUE",
                "-keyout",
                str(key),
                "-out",
                str(cert),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        state = HostState(root / "prepared/site")
        server = HTTPServer(("127.0.0.1", 0), handler(state))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        try:
            base = f"https://127.0.0.1:{server.server_port}/synthetic/v2/"
            with synthetic_publications(root / "fixtures", base) as inputs:
                output = root / "prepared"
                prepare(inputs[0], output)
                pin = digest((output / "inventory.json").read_bytes())
                # The production opener keeps certificate verification enabled. Only
                # this test process trusts its ephemeral, loopback-only certificate.
                environment = {
                    "SSL_CERT_FILE": str(cert),
                    "SSL_CERT_DIR": str(root / "empty"),
                    "NO_PROXY": "127.0.0.1",
                    "HTTPS_PROXY": "",
                    "HTTP_PROXY": "",
                    "ALL_PROXY": "",
                }
                with patch.dict(os.environ, environment):
                    result = verify_delivery(output, pin, base)
                    assert result.files == 20 and result.range_checks == 1
                    for fault in (
                        "redirect",
                        "cors",
                        "short",
                        "range-status",
                        "range-bytes",
                    ):
                        state.fault, state.requests = fault, 0
                        try:
                            verify_delivery(output, pin, base)
                        except (ValueError, OSError):
                            if fault == "redirect":
                                assert state.requests == 1  # No followed redirect.
                        else:
                            raise AssertionError(f"HTTPS accepted {fault}")
                    state.fault = ""
                    with patch.dict(
                        os.environ, {"SSL_CERT_FILE": str(root / "missing")}
                    ):
                        try:
                            verify_delivery(output, pin, base)
                        except URLError as error:
                            assert isinstance(
                                error.reason, ssl.SSLCertVerificationError
                            )
                        else:
                            raise AssertionError(
                                "HTTPS accepted an untrusted certificate"
                            )
                print(
                    f"HTTPS acceptance passed: {result.files} files, exact hashes, "
                    "CORS, PMTiles range, five delivery failures, untrusted TLS"
                )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
            assert not thread.is_alive()


if __name__ == "__main__":
    main()
