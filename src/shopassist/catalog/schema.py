from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, Field

Category = Literal[
    "hiking_boots",
    "hiking_shoes",
    "trail_running_shoes",
    "running_shoes",
    "walking_shoes",
    "sneakers",
    "sandals",
    "rain_boots",
    "winter_boots",
    "work_boots",
    "boots",
    "slippers",
    "dress_shoes",
    "socks",
    "jackets",
    "other",
]
CATEGORIES: tuple[str, ...] = get_args(Category)

Attribute = Literal[
    "waterproof",
    "water_resistant",
    "wide_fit",
    "insulated",
    "slip_resistant",
    "vegan",
    "lightweight",
    "breathable",
    "steel_toe",
]
ATTRIBUTES: tuple[str, ...] = get_args(Attribute)

Gender = Literal["men", "women", "unisex", "kids"]


class Product(BaseModel):
    id: str = Field(description="Amazon parent ASIN (or a synthetic fixture id)")
    title: str
    brand: str | None = None
    category: Category = "other"
    gender: Gender | None = None
    price_eur: float = Field(gt=0)
    rating: float | None = None
    rating_count: int = 0
    attributes: list[Attribute] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    description: str = ""
    image_url: str | None = None
    sizes_eu: list[str] = Field(default_factory=list)
    in_stock: bool = True
    source: str = "amazon_reviews_2023"
    source_url: str | None = None
    flagged_text: bool = Field(False, description="Third-party text contained instruction-like content that was removed")

    @property
    def bayesian_rating(self) -> float:
        prior, weight = 4.0, 25
        if self.rating is None:
            return prior
        return (prior * weight + self.rating * self.rating_count) / (weight + self.rating_count)

    def search_text(self) -> str:
        parts = [
            self.title,
            self.brand or "",
            self.category.replace("_", " "),
            " ".join(a.replace("_", " ") for a in self.attributes),
            self.gender or "",
            " ".join(self.features),
        ]
        return "\n".join(p for p in parts if p)
