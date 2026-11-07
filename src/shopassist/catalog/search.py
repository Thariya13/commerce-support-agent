from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Protocol

from shopassist.catalog.enrich import detect_category
from shopassist.catalog.repository import ProductCatalog
from shopassist.catalog.schema import ATTRIBUTES, CATEGORIES, Product

logger = logging.getLogger(__name__)

SORTS = ("relevance", "price_asc", "price_desc", "rating")
RELATED_CATEGORIES: dict[str, tuple[str, ...]] = {
    "hiking_boots": ("hiking_shoes", "boots", "winter_boots"),
    "hiking_shoes": ("hiking_boots", "trail_running_shoes"),
    "trail_running_shoes": ("running_shoes", "hiking_shoes"),
    "running_shoes": ("trail_running_shoes", "sneakers"),
    "walking_shoes": ("sneakers", "running_shoes"),
    "rain_boots": ("boots", "winter_boots"),
    "winter_boots": ("boots", "hiking_boots"),
    "sneakers": ("walking_shoes", "running_shoes"),
    "boots": ("hiking_boots", "winter_boots", "work_boots"),
    "work_boots": ("boots",),
    "sandals": ("slippers",),
}


def brand_key(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


@dataclass(frozen=True)
class SearchFilters:
    max_price_eur: float | None = None
    min_price_eur: float | None = None
    categories: tuple[str, ...] = ()
    gender: str | None = None
    attributes: tuple[str, ...] = ()
    brand: str | None = None
    min_rating: float | None = None
    size_eu: str | None = None
    in_stock_only: bool = True

    def matches(self, p: Product) -> bool:
        if self.max_price_eur is not None and p.price_eur > self.max_price_eur + 1e-9:
            return False
        if self.min_price_eur is not None and p.price_eur < self.min_price_eur - 1e-9:
            return False
        if self.categories and p.category not in self.categories:
            return False
        if self.gender and p.gender not in (self.gender, "unisex"):
            return False
        if self.attributes and not set(self.attributes).issubset(p.attributes):
            return False
        if self.brand and brand_key(p.brand) != brand_key(self.brand):
            return False
        if self.min_rating is not None and (p.rating or 0.0) < self.min_rating:
            return False
        if self.size_eu and self.size_eu not in p.sizes_eu:
            return False
        return not (self.in_stock_only and not p.in_stock)

    def relaxation_steps(self) -> list[tuple[str, SearchFilters]]:
        steps: list[tuple[str, SearchFilters]] = []
        f = self
        if f.min_rating is not None:
            f = replace(f, min_rating=None)
            steps.append(("minimum rating", f))
        if f.size_eu:
            f = replace(f, size_eu=None)
            steps.append((f"availability in size {self.size_eu}", f))
        if f.brand:
            f = replace(f, brand=None)
            steps.append((f"brand {self.brand}", f))
        for attr in reversed(f.attributes[1:]):
            f = replace(f, attributes=tuple(a for a in f.attributes if a != attr))
            steps.append((attr.replace("_", " "), f))
        if f.categories:
            related = tuple(c for cat in f.categories for c in RELATED_CATEGORIES.get(cat, ()))
            if related:
                f = replace(f, categories=tuple(dict.fromkeys(f.categories + related)))
                steps.append(("exact product type (similar types included)", f))
        return steps

    def describe(self) -> dict:
        out = {}
        for key, value in self.__dict__.items():
            if value in (None, (), False) or key == "in_stock_only":
                continue
            out[key] = list(value) if isinstance(value, tuple) else value
        return out


_NUM = r"(\d{1,5}(?:[.,]\d{1,2})?)"
_CUR = r"(?:€|eur(?:o|os)?\b)"
PRICE_MAX = re.compile(
    rf"(?:under|below|less than|cheaper than|max(?:imum)?|up to|no more than|at most|<=?|budget(?: of| is)?|unter|bis|h(?:ö|oe)chstens|maximal)\s*{_CUR}?\s*{_NUM}\s*{_CUR}?",
    re.I,
)
PRICE_MAX_SUFFIX = re.compile(rf"(?:{_CUR}\s*{_NUM}|{_NUM}\s*{_CUR})\s*(?:or less|or under|max(?:imum)?|tops|at most)", re.I)
PRICE_MIN = re.compile(rf"(?:over|above|more than|at least|min(?:imum)?)\s*{_CUR}?\s*{_NUM}\s*{_CUR}?", re.I)
PRICE_RANGE = re.compile(rf"between\s*{_CUR}?\s*{_NUM}\s*{_CUR}?\s*(?:and|-|to)\s*{_CUR}?\s*{_NUM}", re.I)
SIZE = re.compile(r"\b(?:size|eu|gr(?:ö|oe)(?:ss|ß)e)\s*(\d{2}(?:[.,]5)?)\b", re.I)
ATTRIBUTE_HINTS: dict[str, re.Pattern] = {
    "waterproof": re.compile(r"\bwater[\s-]?proof\b|\bgore[\s-]?tex\b|\bwasserdicht\b", re.I),
    "water_resistant": re.compile(r"\bwater[\s-]?resistant\b", re.I),
    "wide_fit": re.compile(r"\bwide(?:[\s-]?(?:feet|foot|fit|fitting|width|toe[\s-]?box))\b|\bfor wide\b|\bbreite?n? f(?:ü|ue)(?:ss|ß)e\b", re.I),
    "insulated": re.compile(r"\binsulated\b|\bwarm[\s-]?lined\b", re.I),
    "vegan": re.compile(r"\bvegan\b", re.I),
    "lightweight": re.compile(r"\blight[\s-]?weight\b|\bultra[\s-]?light\b", re.I),
    "slip_resistant": re.compile(r"\b(?:non|anti)[\s-]?slip\b|\bslip[\s-]?resistant\b", re.I),
    "breathable": re.compile(r"\bbreathable\b", re.I),
    "steel_toe": re.compile(r"\bsteel[\s-]?toe\b", re.I),
}


def _num(value: str) -> float:
    return float(value.replace(",", "."))


def normalize_size(value: str | float | None) -> str | None:
    if value is None:
        return None
    match = re.search(r"\b(\d{2})(?:[.,](5))?\b", str(value))
    if not match:
        return None
    return match.group(1) + (".5" if match.group(2) else "")


def parse_query_constraints(query: str) -> SearchFilters:
    q = query or ""
    max_price = min_price = None
    if m := PRICE_RANGE.search(q):
        min_price, max_price = sorted((_num(m.group(1)), _num(m.group(2))))
    else:
        if m := PRICE_MAX.search(q) or PRICE_MAX_SUFFIX.search(q):
            max_price = _num(next(g for g in m.groups() if g))
        if m := PRICE_MIN.search(q):
            min_price = _num(m.group(1))
    attributes = tuple(a for a, pattern in ATTRIBUTE_HINTS.items() if pattern.search(q))
    if "waterproof" in attributes and "water_resistant" in attributes:
        attributes = tuple(a for a in attributes if a != "water_resistant")
    gender = None
    lowered = q.lower()
    if re.search(r"\b(?:kids?|children'?s?|boys?|girls?|toddlers?)\b", lowered):
        gender = "kids"
    elif re.search(r"\b(?:women'?s?|ladies|female|damen)\b", lowered):
        gender = "women"
    elif re.search(r"\b(?:men'?s?|male|herren)\b", lowered):
        gender = "men"
    category = detect_category(q, [])
    size = SIZE.search(q)
    return SearchFilters(
        max_price_eur=max_price,
        min_price_eur=min_price,
        categories=(category,) if category != "other" else (),
        gender=gender,
        attributes=attributes,
        size_eu=normalize_size(size.group(1)) if size else None,
    )


def merge_filters(explicit: SearchFilters, parsed: SearchFilters) -> SearchFilters:
    return SearchFilters(
        max_price_eur=explicit.max_price_eur if explicit.max_price_eur is not None else parsed.max_price_eur,
        min_price_eur=explicit.min_price_eur if explicit.min_price_eur is not None else parsed.min_price_eur,
        categories=explicit.categories or parsed.categories,
        gender=explicit.gender or parsed.gender,
        attributes=tuple(dict.fromkeys(explicit.attributes + parsed.attributes)),
        brand=explicit.brand,
        min_rating=explicit.min_rating,
        size_eu=explicit.size_eu or parsed.size_eu,
        in_stock_only=explicit.in_stock_only,
    )


@dataclass
class SearchResult:
    products: list[Product]
    filters: SearchFilters
    relaxed: list[str] = field(default_factory=list)
    total_matches: int = 0


class ProductSearch(Protocol):
    catalog: ProductCatalog

    def search(self, query: str, filters: SearchFilters, limit: int = 5, sort: str = "relevance") -> SearchResult: ...


STOPWORDS = frozenset(
    "a an and are as at be by for from i in is it me my of on or show some that the this to with want need "
    "looking find please any under below over above than less more euro eur price cheap good best".split()
)


def stem(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("es") and token[-3] in "sxz":
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    return [stem(t) for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS and len(t) > 1]


class BM25Index:
    def __init__(self, documents: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(doc) for doc in documents]
        self.lengths = [len(doc) for doc in documents]
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if documents else 1.0
        df = Counter(term for doc in documents for term in set(doc))
        n = len(documents)
        self.idf = {term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()}

    def score(self, query: list[str], index: int) -> float:
        tf, length, total = self.tf[index], self.lengths[index], 0.0
        for term in query:
            freq = tf.get(term)
            if not freq:
                continue
            norm = freq * (self.k1 + 1) / (freq + self.k1 * (1 - self.b + self.b * length / self.avg_len))
            total += self.idf.get(term, 0.0) * norm
        return total


def rank(products: list[Product], scores: dict[str, float], sort: str) -> list[Product]:
    if sort == "price_asc":
        return sorted(products, key=lambda p: (p.price_eur, -scores.get(p.id, 0.0)))
    if sort == "price_desc":
        return sorted(products, key=lambda p: (-p.price_eur, -scores.get(p.id, 0.0)))
    if sort == "rating":
        return sorted(products, key=lambda p: (-p.bayesian_rating, -p.rating_count))
    return sorted(products, key=lambda p: -scores.get(p.id, 0.0))


class LocalProductSearch:
    def __init__(self, catalog: ProductCatalog):
        self.catalog = catalog
        self._products = catalog.all()
        docs = []
        for p in self._products:
            text = p.search_text()
            docs.append(tokenize(p.title) + tokenize(text))
        self._index = BM25Index(docs)
        self._position = {p.id: i for i, p in enumerate(self._products)}

    def _score(self, query: list[str], product: Product) -> float:
        relevance = self._index.score(query, self._position[product.id]) if query else 0.0
        return relevance + 0.35 * (product.bayesian_rating - 4.0)

    def search(self, query: str, filters: SearchFilters, limit: int = 5, sort: str = "relevance") -> SearchResult:
        tokens = tokenize(query)
        relaxed: list[str] = []
        attempts = [("", filters), *filters.relaxation_steps()]
        candidates: list[Product] = []
        applied = filters
        for label, f in attempts:
            candidates = [p for p in self._products if f.matches(p)]
            if label:
                relaxed.append(label)
            applied = f
            if candidates:
                break
        if not candidates:
            return SearchResult([], filters, relaxed, 0)
        scores = {p.id: self._score(tokens, p) for p in candidates}
        if tokens:
            relevant = [p for p in candidates if self._index.score(tokens, self._position[p.id]) > 0]
            candidates = relevant or candidates
        ranked = rank(candidates, scores, sort if sort in SORTS else "relevance")
        return SearchResult(ranked[:limit], applied, relaxed, len(candidates))


class QdrantProductSearch:

    PAYLOAD_INDEXES = {
        "metadata.price_eur": "float",
        "metadata.rating": "float",
        "metadata.category": "keyword",
        "metadata.gender": "keyword",
        "metadata.attributes": "keyword",
        "metadata.brand_key": "keyword",
        "metadata.sizes_eu": "keyword",
        "metadata.in_stock": "bool",
    }

    def __init__(self, catalog: ProductCatalog, vector_store, fetch_k: int = 40):
        self.catalog = catalog
        self.store = vector_store
        self.fetch_k = fetch_k

    @staticmethod
    def to_documents(catalog: ProductCatalog) -> list:
        from langchain_core.documents import Document

        docs = []
        for p in catalog.all():
            docs.append(
                Document(
                    page_content=p.search_text(),
                    metadata={
                        "id": p.id,
                        "price_eur": p.price_eur,
                        "rating": p.rating or 0.0,
                        "category": p.category,
                        "gender": p.gender or "unknown",
                        "attributes": list(p.attributes),
                        "brand_key": brand_key(p.brand),
                        "sizes_eu": list(p.sizes_eu),
                        "in_stock": p.in_stock,
                    },
                )
            )
        return docs

    @staticmethod
    def to_qdrant_filter(f: SearchFilters):
        from qdrant_client import models

        must = []
        if f.max_price_eur is not None or f.min_price_eur is not None:
            must.append(
                models.FieldCondition(
                    key="metadata.price_eur", range=models.Range(gte=f.min_price_eur, lte=f.max_price_eur)
                )
            )
        if f.categories:
            must.append(models.FieldCondition(key="metadata.category", match=models.MatchAny(any=list(f.categories))))
        if f.gender:
            must.append(models.FieldCondition(key="metadata.gender", match=models.MatchAny(any=[f.gender, "unisex"])))
        for attribute in f.attributes:
            must.append(models.FieldCondition(key="metadata.attributes", match=models.MatchValue(value=attribute)))
        if f.brand:
            must.append(models.FieldCondition(key="metadata.brand_key", match=models.MatchValue(value=brand_key(f.brand))))
        if f.min_rating is not None:
            must.append(models.FieldCondition(key="metadata.rating", range=models.Range(gte=f.min_rating)))
        if f.size_eu:
            must.append(models.FieldCondition(key="metadata.sizes_eu", match=models.MatchValue(value=f.size_eu)))
        if f.in_stock_only:
            must.append(models.FieldCondition(key="metadata.in_stock", match=models.MatchValue(value=True)))
        return models.Filter(must=must) if must else None

    @classmethod
    def build_index(cls, catalog: ProductCatalog, *, url: str, api_key: str | None, collection: str, embeddings, sparse_embeddings):
        from langchain_qdrant import QdrantVectorStore, RetrievalMode
        from qdrant_client import QdrantClient, models

        docs = cls.to_documents(catalog)
        QdrantVectorStore.from_documents(
            docs,
            embedding=embeddings,
            sparse_embedding=sparse_embeddings,
            url=url,
            api_key=api_key,
            collection_name=collection,
            retrieval_mode=RetrievalMode.HYBRID,
            ids=[_point_id(d.metadata["id"]) for d in docs],
            force_recreate=True,
            batch_size=64,
        )
        client = QdrantClient(url=url, api_key=api_key)
        schema = {
            "float": models.PayloadSchemaType.FLOAT,
            "keyword": models.PayloadSchemaType.KEYWORD,
            "bool": models.PayloadSchemaType.BOOL,
        }
        for key, kind in cls.PAYLOAD_INDEXES.items():
            client.create_payload_index(collection, field_name=key, field_schema=schema[kind])
        return len(docs)

    @classmethod
    def connect(cls, catalog: ProductCatalog, *, url: str, api_key: str | None, collection: str, embeddings, sparse_embeddings):
        from langchain_qdrant import QdrantVectorStore, RetrievalMode
        from qdrant_client import QdrantClient

        store = QdrantVectorStore(
            client=QdrantClient(url=url, api_key=api_key, timeout=10),
            collection_name=collection,
            embedding=embeddings,
            sparse_embedding=sparse_embeddings,
            retrieval_mode=RetrievalMode.HYBRID,
        )
        return cls(catalog, store)

    def search(self, query: str, filters: SearchFilters, limit: int = 5, sort: str = "relevance") -> SearchResult:
        relaxed: list[str] = []
        for label, f in [("", filters), *filters.relaxation_steps()]:
            if label:
                relaxed.append(label)
            hits = self.store.similarity_search_with_score(query or "shoes", k=self.fetch_k, filter=self.to_qdrant_filter(f))
            scores: dict[str, float] = {}
            products: list[Product] = []
            for doc, score in hits:
                product = self.catalog.get(doc.metadata["id"])
                if product is not None and f.matches(product) and product.id not in scores:
                    scores[product.id] = float(score)
                    products.append(product)
            if products:
                ranked = rank(products, scores, sort if sort in SORTS else "relevance")
                return SearchResult(ranked[:limit], f, relaxed, len(products))
        return SearchResult([], filters, relaxed, 0)


def _point_id(product_id: str) -> str:
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"product:{product_id}"))


def validate_filter_values(categories: list[str] | None, attributes: list[str] | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    cats = tuple(c for c in (categories or []) if c in CATEGORIES and c != "other")
    attrs = tuple(a for a in (attributes or []) if a in ATTRIBUTES)
    return cats, attrs
