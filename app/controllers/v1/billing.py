"""Billing endpoints: Stripe checkout, webhook, and key management."""
from typing import Annotated
from urllib.parse import urlparse

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel

from app.controllers import base
from app.config import config
from app.services.billing import TIERS, deactivate_by_subscription, get_key_by_subscription, get_key_info, issue_key
from app.services.usage import usage_tracker

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


def _require_stripe() -> None:
    """Raise HTTP 503 if Stripe is not configured, otherwise set the API key."""
    if not config.app.get("stripe_secret_key", ""):
        raise HTTPException(status_code=503, detail="Stripe not configured — set stripe_secret_key in config.toml")
    stripe.api_key = config.app["stripe_secret_key"]


def _validate_redirect_url(url: str) -> None:
    """Raise HTTP 400 if url is not an absolute http/https URL."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid redirect URL — must be absolute http/https")


class CheckoutRequest(BaseModel):
    """Request body for POST /billing/checkout."""

    tier: str
    success_url: str
    cancel_url: str
    customer_email: str = ""


@router.post("/checkout")
def create_checkout(req: CheckoutRequest):
    """Create a Stripe Checkout session for a paid tier. Returns {checkout_url}."""
    _require_stripe()
    _validate_redirect_url(req.success_url)
    _validate_redirect_url(req.cancel_url)
    paid_tiers = [t for t in TIERS if t != "free"]
    if req.tier not in paid_tiers:
        raise HTTPException(status_code=400, detail=f"tier must be one of: {paid_tiers}")
    price_id = config.app.get(f"stripe_price_{req.tier}", "")
    if not price_id:
        raise HTTPException(status_code=503, detail=f"stripe_price_{req.tier} not set in config.toml")
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=req.success_url,
        cancel_url=req.cancel_url,
        customer_email=req.customer_email or None,
        metadata={"tier": req.tier},
    )
    return {"checkout_url": session.url, "session_id": session.id}


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: Annotated[str, Header(alias="stripe-signature")] = "",
):
    """Stripe webhook receiver. Point your Stripe dashboard to POST /api/v1/billing/webhook."""
    secret = config.app.get("stripe_webhook_secret", "")
    if not secret:
        raise HTTPException(status_code=503, detail="stripe_webhook_secret not configured")
    _require_stripe()
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload, stripe_signature, secret)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")

    etype = event["type"]
    logger.info(f"Stripe event: {etype}")

    if etype == "checkout.session.completed":
        session = event["data"]["object"]
        tier = session.get("metadata", {}).get("tier", "starter")
        customer_id = session.get("customer", "")
        subscription_id = session.get("subscription", "")
        customer_email = session.get("customer_details", {}).get("email", "")
        # Idempotency: Stripe may retry events; avoid issuing duplicate keys
        if subscription_id and get_key_by_subscription(subscription_id):
            logger.info(f"Duplicate event — key already issued for subscription {subscription_id}")
        else:
            key = issue_key(tier, customer_id, subscription_id, customer_email)
            # TODO: email the key to customer_email via your transactional mailer
            logger.info(f"New {tier} key issued — …{key[-8:]} → {customer_email}")

    elif etype in ("customer.subscription.deleted", "customer.subscription.paused"):
        sub = event["data"]["object"]
        deactivate_by_subscription(sub["id"])

    return JSONResponse({"status": "ok"})


@router.post("/keys/free")
def issue_free_key():
    """Issue a free-tier key immediately — only when enable_free_keys = true in config."""
    if not config.app.get("enable_free_keys", False):
        raise HTTPException(status_code=403, detail="Free key issuance is disabled — set enable_free_keys = true in config.toml")
    key = issue_key("free")
    return {
        "api_key": key,
        "tier": "free",
        "daily_quota": TIERS["free"],
        "note": "Include this as the X-Api-Key header on all requests.",
    }


@router.get("/keys/me")
def my_key_info(request: Request, _: None = Depends(base.verify_token)):
    """Return tier, quota, and today's usage for the calling key."""
    api_key = base.get_api_key(request)
    info = get_key_info(api_key) if api_key else None
    used = usage_tracker.get_usage(api_key or "")
    if info:
        return {
            "tier": info["tier"],
            "daily_quota": info["daily_quota"],
            "used_today": used,
            "customer_email": info.get("customer_email", ""),
            "created_at": info["created_at"],
        }
    # Key is from config (not billing DB) — report as unlimited/config-managed
    return {"tier": "config", "daily_quota": -1, "used_today": used}
