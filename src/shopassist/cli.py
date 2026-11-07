from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True, help="Cairnly shopping assistant")
catalog_app = typer.Typer(no_args_is_help=True, help="Build and index the product catalog")
app.add_typer(catalog_app, name="catalog")


@catalog_app.command("download")
def catalog_download(
    category: str = typer.Option("Clothing_Shoes_and_Jewelry", help="Amazon Reviews 2023 metadata category"),
    source: Path | None = typer.Option(None, help="Local .jsonl/.jsonl.gz file instead of streaming from Hugging Face"),
    target: int = typer.Option(3000, help="Products to keep"),
    scan_limit: int = typer.Option(600_000, help="Stop after this many raw records"),
    min_ratings: int = typer.Option(20, help="Minimum number of ratings per product"),
    usd_to_eur: float = typer.Option(0.92, help="Conversion rate applied to dataset prices"),
    out: Path = typer.Option(Path("data/catalog.jsonl")),
):
    from shopassist.catalog.ingest import HF_META_URL, build_catalog, iter_file_jsonl, iter_http_jsonl
    from shopassist.catalog.repository import write_jsonl

    logging.basicConfig(level=logging.INFO)
    records = iter_file_jsonl(source) if source else iter_http_jsonl(HF_META_URL.format(category=category))
    products, stats = build_catalog(
        records, target=target, scan_limit=scan_limit, min_ratings=min_ratings, usd_to_eur=usd_to_eur
    )
    write_jsonl(products, out)
    typer.echo(json.dumps(stats, indent=2))
    typer.echo(f"Wrote {len(products)} products to {out}. Use it with CATALOG_PATH={out}")


@catalog_app.command("sample")
def catalog_sample():
    from shopassist.catalog.repository import SAMPLE_CATALOG, write_jsonl
    from shopassist.catalog.sample import build_sample_catalog

    typer.echo(f"Wrote {write_jsonl(build_sample_catalog(), SAMPLE_CATALOG)} products to {SAMPLE_CATALOG}")


if __name__ == "__main__":
    app()
