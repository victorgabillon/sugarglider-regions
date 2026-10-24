"""Prepare or check one current V2 publication. There is no deployment command."""

import argparse
import json
from dataclasses import asdict
from http.client import HTTPException
from pathlib import Path
from tarfile import TarError
from zipfile import BadZipFile

from sugarglider_publisher.delivery import verify_delivery
from sugarglider_publisher.prepare import PublicationInputs, prepare


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("prepare")
    for name in ("archive", "evidence", "approval", "policy", "output"):
        build.add_argument("--" + name, type=Path, required=True)
    for name in (
        "archive-sha256",
        "trusted-approval-sha256",
        "policy-sha256",
        "base-url",
    ):
        build.add_argument("--" + name, required=True)
    check = commands.add_parser("check-delivery")
    check.add_argument("--publication", type=Path, required=True)
    check.add_argument("--trusted-inventory-sha256", required=True)
    check.add_argument("--base-url", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(
                PublicationInputs(
                    archive=args.archive,
                    archive_sha256=args.archive_sha256,
                    evidence=args.evidence,
                    approval=args.approval,
                    trusted_approval_sha256=args.trusted_approval_sha256,
                    policy=args.policy,
                    policy_sha256=args.policy_sha256,
                    base_url=args.base_url,
                ),
                args.output,
            )
            print(
                json.dumps(
                    {
                        "status": "prepared-not-published",
                        "build_id": result.build_id,
                        "files": len(result.files),
                    }
                )
            )
        else:
            delivered = verify_delivery(
                args.publication, args.trusted_inventory_sha256, args.base_url
            )
            print(
                json.dumps(
                    {"status": "delivery-verified-not-published", **asdict(delivered)}
                )
            )
    except (ValueError, OSError, BadZipFile, TarError, HTTPException) as error:
        parser.exit(1, f"v2-publication: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
