from collections.abc import Iterator

import pytest

from tests.support.database import (
    DatabaseHandles,
    close_database_handles,
    open_database_handles,
    truncate_platform_tables,
)


@pytest.fixture(scope="session")
def database_handles() -> Iterator[DatabaseHandles]:
    handles = open_database_handles()
    try:
        yield handles
    finally:
        close_database_handles(handles)


@pytest.fixture()
def db(database_handles: DatabaseHandles) -> Iterator[DatabaseHandles]:
    try:
        yield database_handles
    finally:
        truncate_platform_tables(database_handles.owner_engine)
