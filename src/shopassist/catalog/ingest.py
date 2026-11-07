from __future__ import annotations

import gzip
import json
import logging
import math
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path

from shopassist.catalog.enrich import record_to_product
from shopassist.catalog.schema import CATEGORIES, Product

logger = logging.getLogger(__name__)

HF_META_URL = (
    "https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023/resolve/main/"
    "raw/meta_categories/meta_{category}.jsonl"
)
DEFAULT_KEEP = frozenset(c for c in CATEGORIES if c != "other")


def iter_http_jsonl(url: str, timeout: float = 60.0) -> Iterator[dict]:
    import httpx

    with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def iter_file_jsonl(path: str | Path) -> Iterator[dict]:
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def build_catalog(
    records: Iterable[dict],
    *,
    target: int = 3000,
    keep_categories: Iterable[str] = DEFAULT_KEEP,
    usd_to_eur: float = 0.92,
    min_ratings: int = 20,
    scan_limit: int = 600_000,
    per_category_cap: int | None = None,
) -> tuple[list[Product], dict]:
    keep = set(keep_categories)
    cap = per_category_cap or math.ceil(target / max(1, len(keep)) * 2.5)
    products: list[Product] = []
    seen: set[str] = set()
    per_category: Counter = Counter()
    stats: Counter = Counter()
    for index, record in enumerate(records):
        if index >= scan_limit:
            stats["stopped_at_scan_limit"] = 1
            break
        stats["scanned"] += 1
        product = record_to_product(record, usd_to_eur=usd_to_eur, min_ratings=min_ratings)
        if product is None:
            stats["rejected_quality"] += 1
            continue
        if product.category not in keep:
            stats["rejected_category"] += 1
            continue
        if product.id in seen:
            stats["duplicates"] += 1
            continue
        if per_category[product.category] >= cap:
            stats["rejected_category_cap"] += 1
            continue
        seen.add(product.id)
        per_category[product.category] += 1
        stats["flagged_third_party_text"] += int(product.flagged_text)
        products.append(product)
        if len(products) >= target:
            break
        if stats["scanned"] % 50_000 == 0:
            logger.info("Scanned %d records, kept %d products", stats["scanned"], len(products))
    return products, {"counts": dict(stats), "per_category": dict(per_category.most_common())}
