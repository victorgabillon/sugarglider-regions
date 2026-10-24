from collections.abc import Iterator
from pathlib import Path

import pytest

from sugarglider_publisher.prepare import PublicationInputs, prepare
from tests.synthetic import synthetic_publications


@pytest.fixture(scope="session")
def publications(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[PublicationInputs, PublicationInputs]]:
    with synthetic_publications(tmp_path_factory.mktemp("synthetic-v2")) as inputs:
        yield inputs


@pytest.fixture(scope="session")
def prepared(
    publications: tuple[PublicationInputs, PublicationInputs],
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    output = tmp_path_factory.mktemp("prepared") / "publication"
    prepare(publications[0], output)
    return output
