from __future__ import annotations

import pytest

from shopassist.catalog.repository import ProductCatalog


@pytest.fixture(scope="session")
def catalog() -> ProductCatalog:
    return ProductCatalog.from_jsonl()
