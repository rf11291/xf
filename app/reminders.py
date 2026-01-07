from __future__ import annotations

import asyncio
import datetime as dt
import json
from zoneinfo import ZoneInfo

from .db import Database
from .templates import render_template
from .mailer import send_html_email
from .config import Config


def _pick_rule_for_display(rules: list[int], days_left: int) -> int:
    """Choose the closest threshold (days_before) to display in templates.

    Example rules: [30, 7, 1, 0]
    - days_left=20 -> 30
    - days_left=5  -> 7
    - days_left=0/-1 -> 0
    """
    if not rules:
        return max(days_left, 0)
    dl = max(days_left, 0)
    candidates = [r for r in rules if r >= dl]
    return min(candidates) if candidates else max(rules)


async def _send_one(
    cfg: Config,
    template_json: str,
    s: dict,
    now: dt.datetime,
    today: dt.date,
    days_before_display: int,
    threshold_days: int,
) -> bool:
    """Send one reminder mail for a subscription detail row dict (join result)."""
    to_email = str(s.get("customer_email") or "").strip()
    if not to_email:
        return False

    expires = dt.date.fromisoformat(str(s["expires_at"]))
    days_left = (expires - today).days

    customer = {
        "id": int(s["customer_id"]),
        "email": s.get("customer_email"),
        "name": s.get("customer_name"),
    }
    product_def = {
        "id": int(s["product_id"]),
        "name": s.get("product_name"),
        "content": s.get("product_content"),
    }
    subscription = {
        "id": int(s["id"]),
        "customer_id": int(s["customer_id"]),
        "product_id": int(s["product_id"]),
        "expires_at": str(s["expires_at"]),
        "note": s.get("note"),
    }

    # Backward-compatible "product" for templates
    merged_product = dict(product_def)
    merged_product["expires_at"] = subscription["expires_at"]
    merged_product["content"] = subscription["note"] or product_def.get("content")

    context = {
        "customer": customer,
        "product": merged_product,
        "product_def": product_def,
        "subscription": subscription,
        "days_before": int(days_before_display),
        "days_left": int(days_left),
        "threshold": int(threshold_days),
        "now": now.replace(microsecond=0).isoformat(),
        "company": cfg.company_name,
        "contact_name": cfg.contact_name,
        "contact_url": cfg.contact_url,
    }

    subject, html = render_template(template_json, context)
    await asyncio.to_thread(
        send_html_email,
        cfg.smtp_host,
        cfg.smtp_port,
        cfg.smtp_user,
        cfg.smtp_pass,
        cfg.smtp_from,
        to_email,
        subject,
        html,
        cfg.smtp_timeout,
    )
    return True


async def scan_and_send(db: Database, cfg: Config, threshold_days: int | None = None) -> dict:
    """Scan subscriptions and send reminder emails.

    - Automatic scheduler (threshold_days=None):
        Start sending **daily** when days_left <= max(reminder_rules),
        stop when expired more than 1 day (days_left < -1),
        and never send more than once per subscription per day.
    - Manual scan (threshold_days provided):
        Send immediately for subscriptions with days_left <= threshold_days
        (still respecting once-per-day guard).
    """
    tz = ZoneInfo(cfg.tz)
    now = dt.datetime.now(tz=tz)
    today = now.date()
    today_iso = today.isoformat()

    rules_raw = db.get_setting("reminder_rules")
    rules = json.loads(rules_raw) if rules_raw else [30, 7, 1, 0]
    rules = sorted({int(x) for x in rules}, reverse=True)
    auto_threshold = max(rules) if rules else 0

    if threshold_days is None:
        threshold_days = auto_threshold
        mode = "auto"
    else:
        threshold_days = int(threshold_days)
        mode = "manual"

    template_json = db.get_setting("email_template") or "{}"
    subs = db.list_all_subscription_details(limit=5000)

    stats = {
        "mode": mode,
        "threshold_days": int(threshold_days),
        "auto_threshold": int(auto_threshold),
        "checked_subscriptions": 0,
        "eligible": 0,
        "skipped_already_sent_today": 0,
        "sent": 0,
        "errors": 0,
    }

    for s in subs:
        stats["checked_subscriptions"] += 1
        try:
            expires = dt.date.fromisoformat(str(s["expires_at"]))
        except Exception:
            continue

        days_left = (expires - today).days

        # stop when expired more than 1 day
        if days_left < -1:
            continue

        # threshold filter
        if days_left > threshold_days:
            continue

        stats["eligible"] += 1
        sub_id = int(s["id"])

        # once per day guard
        if db.was_sent_on(sub_id, today_iso):
            stats["skipped_already_sent_today"] += 1
            continue

        days_before_display = _pick_rule_for_display(rules, days_left)

        try:
            ok = await _send_one(
                cfg=cfg,
                template_json=template_json,
                s=s,
                now=now,
                today=today,
                days_before_display=days_before_display,
                threshold_days=threshold_days,
            )
            if ok:
                db.mark_sent_on(sub_id, today_iso)
                stats["sent"] += 1
            else:
                stats["errors"] += 1
        except Exception:
            stats["errors"] += 1

    return stats


async def send_subscription_now(db: Database, cfg: Config, subscription_id: int) -> dict:
    """Send email immediately for a single subscription and mark as sent today."""
    tz = ZoneInfo(cfg.tz)
    now = dt.datetime.now(tz=tz)
    today = now.date()
    today_iso = today.isoformat()

    rules_raw = db.get_setting("reminder_rules")
    rules = json.loads(rules_raw) if rules_raw else [30, 7, 1, 0]
    rules = sorted({int(x) for x in rules}, reverse=True)
    auto_threshold = max(rules) if rules else 0

    s = db.get_subscription_detail(int(subscription_id))
    if not s:
        return {"ok": False, "reason": "subscription_not_found"}

    expires = dt.date.fromisoformat(str(s["expires_at"]))
    days_left = (expires - today).days
    days_before_display = _pick_rule_for_display(rules, days_left)

    template_json = db.get_setting("email_template") or "{}"
    ok = await _send_one(
        cfg=cfg,
        template_json=template_json,
        s=s,
        now=now,
        today=today,
        days_before_display=days_before_display,
        threshold_days=auto_threshold,
    )
    if not ok:
        return {"ok": False, "reason": "customer_email_empty"}

    db.mark_sent_on(int(subscription_id), today_iso)
    return {"ok": True, "to": str(s.get("customer_email") or ""), "sent_date": today_iso, "days_left": days_left}

async def send_renewal_confirm(
    db: Database,
    cfg: Config,
    subscription_id: int,
    old_expires_at: str,
    new_expires_at: str,
    renew_days: int,
) -> dict:
    """Send a renewal confirmation email and mark as sent today (to avoid same-day duplicate reminders)."""
    tz = ZoneInfo(cfg.tz)
    now = dt.datetime.now(tz=tz)
    today = now.date()
    today_iso = today.isoformat()

    s = db.get_subscription_detail(int(subscription_id))
    if not s:
        return {"ok": False, "reason": "subscription_not_found"}

    to_email = str(s.get("customer_email") or "").strip()
    if not to_email:
        return {"ok": False, "reason": "customer_email_empty"}

    customer = {"id": int(s["customer_id"]), "email": s.get("customer_email"), "name": s.get("customer_name")}
    product_def = {"id": int(s["product_id"]), "name": s.get("product_name"), "content": s.get("product_content")}
    subscription = {
        "id": int(s["id"]),
        "customer_id": int(s["customer_id"]),
        "product_id": int(s["product_id"]),
        "expires_at": str(s["expires_at"]),
        "note": s.get("note"),
    }
    merged_product = dict(product_def)
    merged_product["expires_at"] = subscription["expires_at"]
    merged_product["content"] = subscription["note"] or product_def.get("content")

    template_json = db.get_setting("renewal_confirm_template") or "{}"
    subject, html = render_template(
        template_json,
        {
            "customer": customer,
            "product": merged_product,
            "product_def": product_def,
            "subscription": subscription,
            "days_before": 0,
            "days_left": (dt.date.fromisoformat(new_expires_at) - today).days,
            "now": now.replace(microsecond=0).isoformat(),
            "company": cfg.company_name,
            "contact_name": cfg.contact_name,
            "contact_url": cfg.contact_url,
            "old_expires_at": old_expires_at,
            "new_expires_at": new_expires_at,
            "renew_days": int(renew_days),
        },
    )

    await asyncio.to_thread(
        send_html_email,
        cfg.smtp_host,
        cfg.smtp_port,
        cfg.smtp_user,
        cfg.smtp_pass,
        cfg.smtp_from,
        to_email,
        subject,
        html,
        cfg.smtp_timeout,
    )

    db.mark_sent_on(int(subscription_id), today_iso)
    return {"ok": True, "to": to_email, "sent_date": today_iso, "old_expires_at": old_expires_at, "new_expires_at": new_expires_at}
