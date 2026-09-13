"""Copy an explicitly reviewed privacy page into the verified publication site."""

import argparse
import re
from pathlib import Path

MAX_POLICY_BYTES = 65_536


def copy_public_privacy(source: Path, site: Path) -> Path:
    """Preserve approved HTML bytes; reject the unfinished template and replacement."""
    if source.is_symlink() or not source.is_file():
        raise ValueError("privacy source must be a regular file")
    if not 0 < source.stat().st_size <= MAX_POLICY_BYTES:
        raise ValueError("privacy page exceeds the publication size bound")
    content = source.read_bytes()
    if not 0 < len(content) <= MAX_POLICY_BYTES:
        raise ValueError("privacy page exceeds the publication size bound")
    text = content.decode("utf-8")
    if (
        "{{" in text
        or "}}" in text
        or 'data-policy-status="approved"' not in text
        or re.search(r"<(script|iframe|form|object|embed)\b", text, re.IGNORECASE)
    ):
        raise ValueError("privacy page requires completed publisher review")
    if not site.is_dir() or any(
        path.is_symlink() for path in (site, *site.absolute().parents)
    ):
        raise ValueError("privacy output requires the verified regular site directory")
    directory = site / "privacy"
    directory.mkdir()  # The verified regional ZIP cannot contain this directory.
    output = directory / "index.html"
    owned = False
    try:
        with output.open("xb") as stream:
            owned = True
            stream.write(content)
    except BaseException:
        if owned:
            output.unlink(missing_ok=True)
        if not any(directory.iterdir()):
            directory.rmdir()
        raise
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("site", type=Path)
    args = parser.parse_args()
    copy_public_privacy(args.source, args.site)
    print("Reviewed privacy page copied without changing regional files.")


if __name__ == "__main__":
    main()
