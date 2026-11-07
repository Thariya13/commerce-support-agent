from __future__ import annotations

import hashlib
import html
import json
import random
import re
from typing import Any

from shopassist.catalog.schema import Product
from shopassist.guardrails.patterns import neutralize_instructions, normalize_text

TAG = re.compile(r"<[^>]+>")

CATEGORY_RULES: list[tuple[str, re.Pattern]] = [
    (name, re.compile(pattern, re.I))
    for name, pattern in [
        ("socks", r"\bsocks?\b"),
        ("slippers", r"\bslippers?\b"),
        ("jackets", r"\b(?:jacket|parka|anorak|rain\s?coat|raincoat|windbreaker|soft[\s-]?shell|hard[\s-]?shell|puffer)\b"),
        ("sandals", r"\b(?:sandals?|flip[\s-]?flops?|slides)\b"),
        ("rain_boots", r"\b(?:rain|rubber|wellington|muck|mud)\s?boots?\b|\bwellies\b"),
        ("winter_boots", r"\b(?:snow|winter|apres[\s-]?ski)\s?boots?\b"),
        ("work_boots", r"\b(?:work|safety|steel[\s-]?toe|composite[\s-]?toe|construction)\s?(?:boots?|shoes?)\b"),
        ("hiking_boots", r"\b(?:hiking|trekking|backpacking|mountaineering)\s?boots?\b|\bhiking\b.{0,40}\bboots?\b|\bboots?\b.{0,40}\bhiking\b"),
        ("hiking_shoes", r"\b(?:hiking|trekking|approach)\s?shoes?\b|\bhiking\b.{0,40}\bshoes?\b"),
        ("trail_running_shoes", r"\btrail[\s-]?(?:running|runners?|run)\b"),
        ("running_shoes", r"\brunning\s?shoes?\b|\brunners?\b"),
        ("walking_shoes", r"\bwalking\s?shoes?\b"),
        ("dress_shoes", r"\b(?:oxfords?|loafers?|derby|brogues?|dress\s?shoes?|monk\s?straps?)\b"),
        ("sneakers", r"\b(?:sneakers?|trainers?|athletic\s?shoes?|tennis\s?shoes?|court\s?shoes?|skate\s?shoes?)\b"),
        ("boots", r"\bboots?\b|\bbooties\b"),
    ]
]

ATTRIBUTE_RULES: dict[str, re.Pattern] = {
    "waterproof": re.compile(
        r"\bwater[\s-]?proof(?:ed)?\b|\bgore[\s-]?tex\b|\bgtx\b|\bh2no\b|\bomni[\s-]?tech\b|\bkeen\.?\s?dry\b|"
        r"\bm[\s-]?select\s?dry\b|\bb-dry\b|\boutdry\b",
        re.I,
    ),
    "water_resistant": re.compile(r"\bwater[\s-]?(?:resistant|repellent)\b|\bdwr\b", re.I),
    "insulated": re.compile(
        r"\binsulat(?:ed|ion)\b|\bthinsulate\b|\bprimaloft\b|\b(?:fleece|faux[\s-]?fur|shearling)[\s-]?lined\b|"
        r"\b-?\d{1,2}\s?°?\s?[fc]\s?(?:rated|comfort)\b",
        re.I,
    ),
    "slip_resistant": re.compile(r"\b(?:slip|skid)[\s-]?resistant\b|\b(?:non|anti)[\s-]?slip\b", re.I),
    "vegan": re.compile(r"\bvegan\b|\banimal[\s-]?free\b", re.I),
    "lightweight": re.compile(r"\blight[\s-]?weight\b|\bultra[\s-]?light\b|\bfeather[\s-]?light\b", re.I),
    "breathable": re.compile(r"\bbreathab(?:le|ility)\b|\bmesh\b|\bventilat(?:ed|ion)\b", re.I),
    "steel_toe": re.compile(r"\b(?:steel|composite|alloy|safety)[\s-]?toe\b", re.I),
}
NOT_WATERPROOF = re.compile(r"\b(?:not|non)[\s-]?water[\s-]?proof\b", re.I)
WIDE_FIT = [
    re.compile(r"\b(?:extra[\s-]?)?wide[\s-]?(?:width|widths|fit|feet|foot|sizes?|toe[\s-]?box)\b", re.I),
    re.compile(r"\b(?:2E|4E|6E|EE|EEE|EEEE)\b"),
    re.compile(r"[,\-–(]\s*(?:extra\s+)?wide\b", re.I),
]

SIZE_RUNS = {
    "men": [str(s) for s in range(40, 48)],
    "women": [str(s) for s in range(36, 43)],
    "kids": [str(s) for s in range(26, 36)],
    "unisex": [str(s) for s in range(37, 47)],
}
APPAREL_SIZES = ["XS", "S", "M", "L", "XL", "XXL"]
SOCK_SIZES = ["35-38", "39-42", "43-46"]


def clean_text(value: Any, max_chars: int) -> str:
    text = html.unescape(TAG.sub(" ", str(value or "")))
    return normalize_text(text, max_chars=max_chars)


def parse_price(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if value > 0 else None
    text = str(value).strip().replace(",", "")
    if text.lower() in {"", "none", "null", "nan", "-"}:
        return None
    match = re.search(r"\d+(?:\.\d+)?", text)
    return float(match.group()) if match and float(match.group()) > 0 else None


def detect_category(title: str, categories: list[str]) -> str:
    path = " > ".join(categories)
    specific, generic = CATEGORY_RULES[:-1], CATEGORY_RULES[-1:]
    for rules in (specific, generic):
        for text in (title, path):
            for name, pattern in rules:
                if pattern.search(text):
                    return name
    return "other"


def detect_gender(title: str, categories: list[str], details: dict) -> str | None:
    text = " ".join([title, *categories, str(details.get("Department", ""))]).lower()
    if re.search(r"\b(?:boys?|girls?|kids?|toddlers?|little kid|big kid|youth|children'?s?|baby|infants?)\b", text):
        return "kids"
    if re.search(r"\bunisex\b", text):
        return "unisex"
    women = re.search(r"\bwom[ae]n'?s?\b|\bladies\b|\bfemale\b", text)
    men = re.search(r"\bmen'?s?\b|\bmale\b", text)
    if women and men:
        return "unisex"
    if women:
        return "women"
    if men:
        return "men"
    return None


def detect_attributes(title: str, features: list[str], description: str, details: dict) -> list[str]:
    text = " \n".join([title, *features, description])
    detail_text = " ".join(f"{k}: {v}" for k, v in details.items() if isinstance(v, str | int | float))
    found = [name for name, pattern in ATTRIBUTE_RULES.items() if pattern.search(text) or pattern.search(detail_text)]
    if "waterproof" in found and NOT_WATERPROOF.search(text):
        found.remove("waterproof")
    if "waterproof" in found and "water_resistant" in found:
        found.remove("water_resistant")
    width = " ".join(str(v) for k, v in details.items() if "width" in k.lower())
    if any(p.search(title) for p in WIDE_FIT) or any(p.search(text) for p in WIDE_FIT[:2]) or re.search(r"\bwide\b", width, re.I):
        found.append("wide_fit")
    return sorted(set(found))


def extract_image(images: Any) -> str | None:
    if isinstance(images, list):
        main = [i for i in images if isinstance(i, dict) and i.get("variant") == "MAIN"] or images
        for image in main:
            if isinstance(image, dict) and (image.get("large") or image.get("hi_res")):
                return image.get("large") or image.get("hi_res")
    if isinstance(images, dict):
        for key in ("large", "hi_res", "thumb"):
            urls = [u for u in images.get(key) or [] if u]
            if urls:
                return urls[0]
    return None


def synthetic_sizes(product_id: str, gender: str | None, category: str) -> list[str]:
    if category == "jackets":
        run = APPAREL_SIZES
    elif category == "socks":
        run = SOCK_SIZES
    else:
        run = SIZE_RUNS.get(gender or "unisex", SIZE_RUNS["unisex"])
    rng = random.Random(int(hashlib.sha256(product_id.encode()).hexdigest()[:12], 16))
    available = [size for size in run if rng.random() > 0.2]
    return available or run[: max(1, len(run) // 2)]


def _details(record: dict) -> dict:
    details = record.get("details") or {}
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            details = {}
    return details if isinstance(details, dict) else {}


def _brand(record: dict, details: dict) -> str | None:
    brand = record.get("store") or details.get("Brand") or details.get("Brand Name") or details.get("Manufacturer")
    if not brand:
        return None
    brand = re.sub(r"^Visit the (.+?) Store$", r"\1", str(brand).strip())
    return clean_text(brand, 60) or None


def record_to_product(record: dict, *, usd_to_eur: float = 0.92, min_ratings: int = 10) -> Product | None:
    asin = record.get("parent_asin") or record.get("asin")
    price_usd = parse_price(record.get("price"))
    title = clean_text(record.get("title"), 220)
    rating_count = int(record.get("rating_number") or 0)
    if not asin or price_usd is None or len(title) < 8 or rating_count < min_ratings:
        return None
    price_eur = round(price_usd * usd_to_eur, 2)
    if not 5 <= price_eur <= 1500:
        return None

    details = _details(record)
    categories = [clean_text(c, 80) for c in record.get("categories") or [] if c]
    flagged = False
    features: list[str] = []
    for raw in (record.get("features") or [])[:8]:
        text, bad = neutralize_instructions(clean_text(raw, 260))
        flagged |= bad
        if text:
            features.append(text[:220])
    description, bad = neutralize_instructions(clean_text(" ".join(record.get("description") or []), 1200))
    flagged |= bad
    title_clean, bad = neutralize_instructions(title)
    flagged |= bad
    title = title_clean or title

    category = detect_category(title, categories)
    gender = detect_gender(title, categories, details)
    sizes = synthetic_sizes(asin, gender, category)
    rating = record.get("average_rating")
    return Product(
        id=str(asin).strip().upper(),
        title=title,
        brand=_brand(record, details),
        category=category,
        gender=gender,
        price_eur=price_eur,
        rating=float(rating) if rating is not None else None,
        rating_count=rating_count,
        attributes=detect_attributes(title, features, description, details),
        features=features[:6],
        description=description[:600],
        image_url=extract_image(record.get("images")),
        sizes_eu=sizes,
        in_stock=bool(sizes),
        source="amazon_reviews_2023",
        source_url=f"https://www.amazon.com/dp/{str(asin).strip().upper()}",
        flagged_text=flagged,
    )
