"""Exact externally pinned, static V2 policy bytes; never approval by marker alone."""

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from sugarglider.offline_regions.publication_records import bounded_record, digest


class _Policy(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.approved_v2 = False
        self.roots = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {
            "html",
            "head",
            "title",
            "meta",
            "style",
            "body",
            "main",
            "section",
            "header",
            "footer",
            "h1",
            "h2",
            "h3",
            "p",
            "ul",
            "ol",
            "li",
            "a",
            "strong",
            "em",
            "code",
            "pre",
            "br",
            "hr",
            "dl",
            "dt",
            "dd",
            "blockquote",
            "time",
            "span",
            "div",
        }:
            raise ValueError("V2 policy must be static HTML")
        values = dict(attrs)
        if len(values) != len(attrs) or any(
            name.startswith("on") or name in {"src", "srcdoc", "style", "http-equiv"}
            for name, _ in attrs
        ):
            raise ValueError("active or duplicate policy attributes")
        if tag == "html":
            self.roots += 1
            self.approved_v2 = (
                values.get("data-policy-status") == "approved"
                and values.get("data-policy-contract") == "sugarglider-v2"
            )
        if "href" in values:
            href = values["href"] or ""
            parsed = urlsplit(href)
            if not (href.startswith("#") or parsed.scheme in {"https", "mailto"}):
                raise ValueError("unsupported policy link")
            if parsed.username or parsed.password:
                raise ValueError("credential-bearing policy link")


def read_policy(path: Path, trusted_sha256: str) -> bytes:
    value = bounded_record(path, 65_536)
    if digest(value) != trusted_sha256:
        raise ValueError("V2 policy lacks its independent reviewed digest")
    text = value.decode("utf-8")
    if re.search(r"\{\{|\}\}|@import|url\s*\(|expression\s*\(", text, re.I):
        raise ValueError("unfinished or externally active V2 policy")
    parser = _Policy()
    parser.feed(text)
    parser.close()
    if parser.roots != 1 or not parser.approved_v2:
        raise ValueError("an explicitly approved V2 policy is required")
    return value
