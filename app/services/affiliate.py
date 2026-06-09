"""
Affiliate product resolver.

Supported networks:
  amazon    — PA API 5.0 (requires access_key, secret_key, partner_tag)
  clickbank — no API needed; constructs hoplink from affiliate_id + vendor_id
  cj        — CJ Affiliate Product Search API (requires api_key + website_id)
  awin      — Awin Product Feed API (requires api_key + publisher_id)
  manual    — caller supplies all fields directly

Returns ProductInfo consumed by the script generator and caption builder.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote, urlencode, urlparse

import requests
from loguru import logger

from app.config import config


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ProductInfo:
    title: str
    affiliate_url: str
    network: str = "manual"
    description: str = ""
    price: str = ""
    category: str = ""
    brand: str = ""
    image_url: str = ""
    product_id: str = ""


# ---------------------------------------------------------------------------
# Amazon PA API 5.0 (SigV4)
# ---------------------------------------------------------------------------

def _amz_sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _amz_signature_key(secret: str, date_stamp: str, region: str, service: str) -> bytes:
    k = _amz_sign(("AWS4" + secret).encode("utf-8"), date_stamp)
    k = _amz_sign(k, region)
    k = _amz_sign(k, service)
    return _amz_sign(k, "aws4_request")


def _amz_request(access_key: str, secret_key: str, partner_tag: str,
                 region: str, host: str, payload: dict) -> dict:
    service = "ProductAdvertisingAPI"
    path = "/paapi5/getitems"
    method = "POST"

    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    body = json.dumps(payload)
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

    headers_to_sign = {
        "content-type": "application/json; charset=utf-8",
        "host": host,
        "x-amz-date": amz_date,
        "x-amz-target": "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.GetItems",
    }
    canonical_headers = "".join(f"{k}:{v}\n" for k, v in sorted(headers_to_sign.items()))
    signed_headers = ";".join(sorted(headers_to_sign.keys()))

    canonical_request = "\n".join([
        method, path, "",
        canonical_headers, signed_headers, body_hash,
    ])

    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    sig_key = _amz_signature_key(secret_key, date_stamp, region, service)
    signature = hmac.new(sig_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    auth = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    resp = requests.post(
        f"https://{host}{path}",
        headers={
            "Authorization": auth,
            "Content-Type": "application/json; charset=utf-8",
            "Host": host,
            "X-Amz-Date": amz_date,
            "X-Amz-Target": "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.GetItems",
        },
        data=body,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _resolve_amazon(product_id: str, affiliate_tag: str) -> ProductInfo:
    access_key = config.app.get("amazon_access_key", "")
    secret_key = config.app.get("amazon_secret_key", "")
    partner_tag = affiliate_tag or config.app.get("amazon_partner_tag", "")
    region = config.app.get("amazon_region", "us-east-1")
    host = config.app.get("amazon_host", "webservices.amazon.com")

    if not all([access_key, secret_key, partner_tag]):
        raise ValueError("Amazon PA API requires amazon_access_key, amazon_secret_key, amazon_partner_tag in config")

    # extract ASIN from URL if full URL was passed
    asin = product_id
    m = re.search(r"/dp/([A-Z0-9]{10})", product_id)
    if m:
        asin = m.group(1)

    payload = {
        "ItemIds": [asin],
        "PartnerTag": partner_tag,
        "PartnerType": "Associates",
        "Resources": [
            "ItemInfo.Title",
            "ItemInfo.Features",
            "ItemInfo.ByLineInfo",
            "ItemInfo.ProductInfo",
            "Offers.Listings.Price",
            "BrowseNodeInfo.BrowseNodes",
        ],
    }

    data = _amz_request(access_key, secret_key, partner_tag, region, host, payload)
    items = (data.get("ItemsResult") or {}).get("Items", [])
    if not items:
        raise ValueError(f"Amazon returned no results for ASIN {asin}")

    item = items[0]
    info = item.get("ItemInfo", {})
    title = info.get("Title", {}).get("DisplayValue", "")
    features = info.get("Features", {}).get("DisplayValues", [])
    brand = info.get("ByLineInfo", {}).get("Brand", {}).get("DisplayValue", "")
    price = ""
    listings = item.get("Offers", {}).get("Listings", [])
    if listings:
        price = listings[0].get("Price", {}).get("DisplayAmount", "")
    category = ""
    nodes = item.get("BrowseNodeInfo", {}).get("BrowseNodes", [])
    if nodes:
        category = nodes[0].get("DisplayName", "")

    affiliate_url = f"https://www.amazon.com/dp/{asin}?tag={partner_tag}"

    return ProductInfo(
        title=title,
        affiliate_url=affiliate_url,
        network="amazon",
        description=" ".join(features[:5]),
        price=price,
        category=category,
        brand=brand,
        product_id=asin,
    )


# ---------------------------------------------------------------------------
# ClickBank (no API — construct hoplink)
# ---------------------------------------------------------------------------

def _resolve_clickbank(product_id: str, affiliate_tag: str) -> ProductInfo:
    """
    product_id format: "vendor_id" or "vendor_id/product_id"
    affiliate_tag: your ClickBank account nickname
    """
    affiliate_id = affiliate_tag or config.app.get("clickbank_affiliate_id", "")
    if not affiliate_id:
        raise ValueError("ClickBank requires clickbank_affiliate_id in config or affiliate_tag")

    vendor = product_id.split("/")[0].strip()
    hop_url = f"https://{affiliate_id}.{vendor}.hop.clickbank.net"

    return ProductInfo(
        title=vendor.replace("-", " ").title(),
        affiliate_url=hop_url,
        network="clickbank",
        product_id=vendor,
        description="",
    )


# ---------------------------------------------------------------------------
# CJ Affiliate Product Search
# ---------------------------------------------------------------------------

def _resolve_cj(product_id: str, affiliate_tag: str) -> ProductInfo:
    api_key = config.app.get("cj_api_key", "")
    website_id = affiliate_tag or config.app.get("cj_website_id", "")
    if not api_key or not website_id:
        raise ValueError("CJ requires cj_api_key and cj_website_id in config")

    params = {
        "website-id": website_id,
        "records-per-page": 1,
        "keywords": product_id,
    }
    resp = requests.get(
        "https://product-search.api.cj.com/v2/product-search",
        headers={"Authorization": f"Bearer {api_key}"},
        params=params,
        timeout=15,
    )
    resp.raise_for_status()

    from defusedxml import ElementTree as _ET

    def _extract(root, tag: str) -> str:
        elem = root.find(f".//{tag}")
        return (elem.text or "").strip() if elem is not None else ""

    try:
        root = _ET.fromstring(resp.text)
    except Exception as exc:
        raise ValueError(f"CJ returned unparseable XML: {exc}") from exc

    title = _extract(root, "name")
    description = _extract(root, "description")
    price = _extract(root, "sale-price") or _extract(root, "price")
    buy_url = _extract(root, "buy-url")
    category = _extract(root, "category")

    if not title:
        raise ValueError(f"CJ returned no results for '{product_id}'")

    return ProductInfo(
        title=title,
        affiliate_url=buy_url,
        network="cj",
        description=description[:500],
        price=price,
        category=category,
        product_id=product_id,
    )


# ---------------------------------------------------------------------------
# Awin Product Feed
# ---------------------------------------------------------------------------

def _resolve_awin(product_id: str, affiliate_tag: str) -> ProductInfo:
    api_key = config.app.get("awin_api_key", "")
    publisher_id = affiliate_tag or config.app.get("awin_publisher_id", "")
    if not api_key or not publisher_id:
        raise ValueError("Awin requires awin_api_key and awin_publisher_id in config")

    resp = requests.get(
        f"https://api.awin.com/publishers/{publisher_id}/product-search",
        headers={"Authorization": f"Bearer {api_key}"},
        params={"keyword": product_id, "pageSize": 1},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    products = data if isinstance(data, list) else data.get("products", [])
    if not products:
        raise ValueError(f"Awin returned no results for '{product_id}'")

    p = products[0]
    return ProductInfo(
        title=p.get("productName", ""),
        affiliate_url=p.get("awTrackingUrl", p.get("productUrl", "")),
        network="awin",
        description=p.get("description", "")[:500],
        price=str(p.get("displayPrice", p.get("salePrice", ""))),
        category=p.get("primaryCategory", {}).get("name", ""),
        brand=p.get("brandName", ""),
        image_url=p.get("imageUrl", ""),
        product_id=str(p.get("productId", product_id)),
    )


# ---------------------------------------------------------------------------
# Manual (caller supplies everything)
# ---------------------------------------------------------------------------

def _resolve_manual(product_id: str, affiliate_tag: str,
                    title: str = "", description: str = "",
                    price: str = "", category: str = "",
                    affiliate_url: str = "") -> ProductInfo:
    resolved_url = affiliate_url or (
        product_id if product_id.startswith("http") else affiliate_tag
    )
    return ProductInfo(
        title=title or product_id,
        affiliate_url=resolved_url,
        network="manual",
        description=description,
        price=price,
        category=category,
        product_id=product_id,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

SUPPORTED_NETWORKS = ["amazon", "clickbank", "cj", "awin", "manual"]


def resolve_product(
    network: str,
    product_id: str,
    affiliate_tag: str = "",
    **kwargs,
) -> ProductInfo:
    """
    Resolve a product to a ProductInfo.

    Args:
        network:       One of SUPPORTED_NETWORKS.
        product_id:    ASIN, vendor ID, keyword, or affiliate URL depending on network.
        affiliate_tag: Associate tag / affiliate ID / publisher ID.
        **kwargs:      Extra fields for 'manual' resolver (title, description, price, category).
    """
    network = network.lower().strip()
    try:
        if network == "amazon":
            return _resolve_amazon(product_id, affiliate_tag)
        if network == "clickbank":
            return _resolve_clickbank(product_id, affiliate_tag)
        if network == "cj":
            return _resolve_cj(product_id, affiliate_tag)
        if network == "awin":
            return _resolve_awin(product_id, affiliate_tag)
        if network == "manual":
            return _resolve_manual(product_id, affiliate_tag, **kwargs)
        raise ValueError(f"Unsupported network '{network}'. Choose from: {SUPPORTED_NETWORKS}")
    except Exception as e:
        logger.error(f"affiliate resolver [{network}] failed: {e}")
        raise


def build_caption(base_title: str, affiliate_url: str, include_disclosure: bool = True) -> str:
    """Build a cross-post caption with affiliate link and FTC disclosure."""
    parts = [base_title]
    if include_disclosure:
        parts.append("#ad")
    if affiliate_url:
        parts.append(affiliate_url)
    parts += ["#shorts", "#review", "#recommended"]
    return " ".join(parts)[:2200]
