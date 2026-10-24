import json
from pathlib import Path

import pytest
from sugarglider.offline_regions.publication_records import digest

from sugarglider_publisher.__main__ import main
from sugarglider_publisher.prepare import PublicationInputs


def arguments(inputs: PublicationInputs, output: Path) -> list[str]:
    return [
        "v2-publication",
        "prepare",
        "--archive",
        str(inputs.archive),
        "--archive-sha256",
        inputs.archive_sha256,
        "--evidence",
        str(inputs.evidence),
        "--approval",
        str(inputs.approval),
        "--trusted-approval-sha256",
        inputs.trusted_approval_sha256,
        "--policy",
        str(inputs.policy),
        "--policy-sha256",
        inputs.policy_sha256,
        "--base-url",
        inputs.base_url,
        "--output",
        str(output),
    ]


def test_prepare_cli(
    publications: tuple[PublicationInputs, PublicationInputs],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.argv", arguments(publications[0], tmp_path / "site"))
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "prepared-not-published" and result["files"] == 20


def test_bad_archive_cli_cleans_output_and_reports_failure(
    publications: tuple[PublicationInputs, PublicationInputs],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = tmp_path / "corrupt.zip"
    archive.write_bytes(b"not a ZIP")
    args = arguments(publications[0], tmp_path / "forbidden")
    args[args.index("--archive") + 1] = str(archive)
    args[args.index("--archive-sha256") + 1] = digest(archive.read_bytes())
    monkeypatch.setattr("sys.argv", args)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    assert "v2-publication:" in capsys.readouterr().err
    assert not (tmp_path / "forbidden").exists()
    assert not list(tmp_path.glob(".v2-prepare-*"))
