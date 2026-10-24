from pathlib import Path

import pytest
from sugarglider.offline_regions.publication_records import digest

from sugarglider_publisher.policy import read_policy

MARKER = 'data-policy-status="approved" data-policy-contract="sugarglider-v2"'


@pytest.mark.parametrize(
    "body",
    [
        '<html data-policy-status="approved">Old contract</html>',
        f"<!-- {MARKER} --><html>Comment</html>",
        '<html data-policy-status="draft" '
        'data-policy-contract="sugarglider-v2">Draft</html>',
        f"<html {MARKER}><script>alert(1)</script></html>",
        f'<html {MARKER} onload="alert(1)"></html>',
        f"<html {MARKER}>" + "{{CONTACT}}</html>",
        f'<html {MARKER}><a href="javascript:alert(1)">click</a></html>',
        f'<html {MARKER}><style>@import "https://example.test/x";</style></html>',
    ],
)
def test_unapproved_or_active_policy_rejected(tmp_path: Path, body: str) -> None:
    path = tmp_path / "policy.html"
    path.write_text(body)
    with pytest.raises(ValueError):
        read_policy(path, digest(path.read_bytes()))
