from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from shopassist.catalog.schema import Product

RESOURCES = Path(__file__).resolve().parent.parent / "resources"
SAMPLE_CATALOG = RESOURCES / "sample_catalog.jsonl"


class ProductCatalog:
    def __init__(self, products: Iterable[Product]):
        self._products: dict[str, Product] = {}
        for product in products:
            self._products[product.id] = product

    @classmethod
    def from_jsonl(cls, path: str | Path | None = None) -> ProductCatalog:
        path = Path(path) if path else SAMPLE_CATALOG
        if not path.exists():
            raise FileNotFoundError(f"Catalog file {path} not found (run the ingest command or use the sample)")
        products = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    products.append(Product.model_validate(json.loads(line)))
        return cls(products)

    def __len__(self) -> int:
        return len(self._products)

    def all(self) -> list[Product]:
        return list(self._products.values())

    def get(self, product_id: str) -> Product | None:
        return self._products.get(product_id.strip().upper()) or self._products.get(product_id.strip())

    def get_many(self, product_ids: Iterable[str]) -> list[Product]:
        out = []
        for pid in product_ids:
            product = self.get(pid)
            if product is not None and product not in out:
                out.append(product)
        return out


def write_jsonl(products: Iterable[Product], path: str | Path) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for product in products:
            fh.write(product.model_dump_json(exclude_none=False) + "\n")
            count += 1
    return count
