from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
import os
import re
from functools import wraps

from apscheduler.schedulers.background import BackgroundScheduler
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from .config import load_config
from .db import Database
from .reminders import scan_and_send, send_renewal_confirm, send_subscription_now


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def create_app() -> Flask:
    cfg = load_config()
    app = Flask(__name__, template_folder="web_templates", static_folder="web_static")
    app.secret_key = cfg.secret_key

    db = Database(cfg.database_path)
    db.init()

    scheduler = BackgroundScheduler(timezone=cfg.tz)

    def scheduled_scan() -> None:
        try:
            asyncio.run(scan_and_send(db, cfg))
        except Exception as exc:
            print("[scan] failed:", exc)

    scheduler.add_job(
        scheduled_scan,
        "interval",
        minutes=cfg.scan_interval_minutes,
        id="scan_job",
        replace_existing=True,
    )
    scheduler.start()

    def login_required(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not session.get("user"):
                return redirect(url_for("login", next=request.path))
            return func(*args, **kwargs)

        return wrapper

    @app.context_processor
    def inject_globals():
        return {
            "company_name": cfg.company_name,
        }

    @app.get("/login")
    def login():
        return render_template("login.html")

    @app.post("/login")
    def login_post():
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if username == cfg.admin_user and password == cfg.admin_password:
            session["user"] = username
            flash("登录成功", "success")
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("账号或密码错误", "danger")
        return redirect(url_for("login"))

    @app.get("/logout")
    def logout():
        session.clear()
        flash("已退出登录", "info")
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def dashboard():
        expiring_days = int(request.args.get("days", "30") or 30)
        expiring_days = min(max(expiring_days, 1), 365)
        total_customers = db.count_customers()
        total_products = db.count_products()
        total_subs = db.count_subscriptions()
        expiring_soon = db.list_subscriptions_expiring_within(expiring_days, limit=10)
        today = dt.date.today()
        for item in expiring_soon:
            exp = dt.date.fromisoformat(str(item["expires_at"]))
            item["days_left"] = (exp - today).days
        return render_template(
            "dashboard.html",
            total_customers=total_customers,
            total_products=total_products,
            total_subs=total_subs,
            expiring_soon=expiring_soon,
            expiring_days=expiring_days,
        )

    @app.get("/customers")
    @login_required
    def customers():
        q = request.args.get("q", "").strip().lower()
        page = int(request.args.get("page", "1") or 1)
        page = max(page, 1)
        per_page = 50
        total = db.count_customers(search=q or None)
        items = db.list_customers(search=q or None, offset=(page - 1) * per_page, limit=per_page)
        total_pages = max(1, (total + per_page - 1) // per_page)
        return render_template("customers.html", items=items, q=q, page=page, total_pages=total_pages)

    @app.post("/customers")
    @login_required
    def customers_create():
        email = request.form.get("email", "").strip().lower()
        name = request.form.get("name", "").strip() or None
        if not EMAIL_RE.match(email):
            flash("邮箱格式不正确", "danger")
            return redirect(url_for("customers"))
        db.upsert_customer(email=email, name=name)
        flash("客户已保存", "success")
        return redirect(url_for("customers"))

    @app.get("/customers/<int:customer_id>/edit")
    @login_required
    def customer_edit(customer_id: int):
        customer = db.get_customer(customer_id)
        if not customer:
            flash("客户不存在", "danger")
            return redirect(url_for("customers"))
        return render_template("customer_edit.html", customer=customer)

    @app.post("/customers/<int:customer_id>/edit")
    @login_required
    def customer_update(customer_id: int):
        email = request.form.get("email", "").strip().lower()
        name = request.form.get("name", "").strip() or None
        if not EMAIL_RE.match(email):
            flash("邮箱格式不正确", "danger")
            return redirect(url_for("customer_edit", customer_id=customer_id))
        ok = db.update_customer(customer_id=customer_id, email=email, name=name)
        if not ok:
            flash("邮箱已存在，请更换", "danger")
            return redirect(url_for("customer_edit", customer_id=customer_id))
        flash("客户信息已更新", "success")
        return redirect(url_for("customers"))

    @app.post("/customers/<int:customer_id>/delete")
    @login_required
    def customer_delete(customer_id: int):
        db.delete_customer(customer_id)
        flash("客户已删除", "info")
        return redirect(url_for("customers"))

    @app.get("/products")
    @login_required
    def products():
        q = request.args.get("q", "").strip().lower()
        page = int(request.args.get("page", "1") or 1)
        page = max(page, 1)
        per_page = 50
        total = db.count_products(search=q or None)
        items = db.list_products(search=q or None, offset=(page - 1) * per_page, limit=per_page)
        total_pages = max(1, (total + per_page - 1) // per_page)
        return render_template("products.html", items=items, q=q, page=page, total_pages=total_pages)

    @app.post("/products")
    @login_required
    def products_create():
        name = request.form.get("name", "").strip()
        content = request.form.get("content", "").strip() or None
        if not name:
            flash("产品名称不能为空", "danger")
            return redirect(url_for("products"))
        db.add_product(name=name, content=content)
        flash("产品已保存", "success")
        return redirect(url_for("products"))

    @app.get("/products/<int:product_id>/edit")
    @login_required
    def product_edit(product_id: int):
        product = db.get_product(product_id)
        if not product:
            flash("产品不存在", "danger")
            return redirect(url_for("products"))
        return render_template("product_edit.html", product=product)

    @app.post("/products/<int:product_id>/edit")
    @login_required
    def product_update(product_id: int):
        name = request.form.get("name", "").strip()
        content = request.form.get("content", "").strip() or None
        if not name:
            flash("产品名称不能为空", "danger")
            return redirect(url_for("product_edit", product_id=product_id))
        ok = db.update_product(product_id=product_id, name=name, content=content)
        if not ok:
            flash("产品名称已存在，请更换", "danger")
            return redirect(url_for("product_edit", product_id=product_id))
        flash("产品已更新", "success")
        return redirect(url_for("products"))

    @app.post("/products/<int:product_id>/delete")
    @login_required
    def product_delete(product_id: int):
        ok = db.delete_product(product_id)
        if not ok:
            flash("产品被订阅使用中，无法删除", "danger")
        else:
            flash("产品已删除", "info")
        return redirect(url_for("products"))

    @app.get("/subscriptions")
    @login_required
    def subscriptions():
        q = request.args.get("q", "").strip().lower()
        page = int(request.args.get("page", "1") or 1)
        page = max(page, 1)
        per_page = 50
        total = db.count_subscriptions(search=q or None)
        items = db.list_all_subscription_details(
            search=q or None,
            offset=(page - 1) * per_page,
            limit=per_page,
        )
        today = dt.date.today()
        for item in items:
            exp = dt.date.fromisoformat(str(item["expires_at"]))
            item["days_left"] = (exp - today).days
        total_pages = max(1, (total + per_page - 1) // per_page)
        return render_template("subscriptions.html", items=items, q=q, page=page, total_pages=total_pages)

    @app.get("/subscriptions/new")
    @login_required
    def subscription_new():
        customers = db.list_customers(offset=0, limit=500)
        products = db.list_products(offset=0, limit=500)
        return render_template("subscription_new.html", customers=customers, products=products)

    @app.post("/subscriptions/new")
    @login_required
    def subscription_create():
        customer_id = int(request.form.get("customer_id", "0"))
        product_id = int(request.form.get("product_id", "0"))
        expires_at = request.form.get("expires_at", "").strip()
        note = request.form.get("note", "").strip() or None
        if not expires_at:
            flash("到期日期不能为空", "danger")
            return redirect(url_for("subscription_new"))
        db.add_subscription(customer_id=customer_id, product_id=product_id, expires_at=expires_at, note=note)
        flash("订阅已创建", "success")
        return redirect(url_for("subscriptions"))

    @app.get("/subscriptions/<int:subscription_id>/edit")
    @login_required
    def subscription_edit(subscription_id: int):
        sub = db.get_subscription_detail(subscription_id)
        if not sub:
            flash("订阅不存在", "danger")
            return redirect(url_for("subscriptions"))
        return render_template("subscription_edit.html", sub=sub)

    @app.post("/subscriptions/<int:subscription_id>/edit")
    @login_required
    def subscription_update(subscription_id: int):
        expires_at = request.form.get("expires_at", "").strip()
        note = request.form.get("note", "").strip() or None
        if not expires_at:
            flash("到期日期不能为空", "danger")
            return redirect(url_for("subscription_edit", subscription_id=subscription_id))
        db.update_subscription(subscription_id=subscription_id, new_expires_at=expires_at, note=note)
        flash("订阅已更新", "success")
        return redirect(url_for("subscriptions"))

    @app.post("/subscriptions/<int:subscription_id>/delete")
    @login_required
    def subscription_delete(subscription_id: int):
        db.delete_subscription(subscription_id)
        flash("订阅已删除", "info")
        return redirect(url_for("subscriptions"))

    @app.post("/subscriptions/<int:subscription_id>/send")
    @login_required
    def subscription_send(subscription_id: int):
        try:
            result = asyncio.run(send_subscription_now(db, cfg, subscription_id))
        except TimeoutError:
            flash("发送超时，请检查 SMTP 连接或提高 SMTP_TIMEOUT", "danger")
            return redirect(url_for("subscriptions"))
        except Exception as exc:
            flash(f"发送失败：{exc}", "danger")
            return redirect(url_for("subscriptions"))
        if result.get("ok"):
            flash(f"已发送提醒到 {result.get('to')}", "success")
        else:
            flash("发送失败，请检查邮箱配置/客户邮箱", "danger")
        return redirect(url_for("subscriptions"))

    @app.post("/subscriptions/<int:subscription_id>/renew")
    @login_required
    def subscription_renew(subscription_id: int):
        renew_days = int(request.form.get("renew_days", "0"))
        sub = db.get_subscription_detail(subscription_id)
        if not sub:
            flash("订阅不存在", "danger")
            return redirect(url_for("subscriptions"))
        old_expires_at = str(sub["expires_at"])
        old_date = dt.date.fromisoformat(old_expires_at)
        new_date = old_date + dt.timedelta(days=renew_days)
        new_expires_at = new_date.isoformat()
        db.update_subscription_expires(subscription_id, new_expires_at)
        try:
            result = asyncio.run(
                send_renewal_confirm(
                    db=db,
                    cfg=cfg,
                    subscription_id=subscription_id,
                    old_expires_at=old_expires_at,
                    new_expires_at=new_expires_at,
                    renew_days=renew_days,
                )
            )
        except TimeoutError:
            flash("续费成功，但确认邮件发送超时，请检查 SMTP 连接或提高 SMTP_TIMEOUT", "warning")
            return redirect(url_for("subscriptions"))
        except Exception as exc:
            flash(f"续费成功，但发送确认邮件失败：{exc}", "warning")
            return redirect(url_for("subscriptions"))
        if result.get("ok"):
            flash("续费成功并发送确认邮件", "success")
        else:
            flash("续费成功，但发送确认邮件失败", "warning")
        return redirect(url_for("subscriptions"))

    @app.get("/settings")
    @login_required
    def settings():
        rules_raw = db.get_setting("reminder_rules") or "[]"
        email_tpl = db.get_setting("email_template") or "{}"
        renew_tpl = db.get_setting("renewal_confirm_template") or "{}"
        return render_template(
            "settings.html",
            rules_raw=rules_raw,
            email_tpl=email_tpl,
            renew_tpl=renew_tpl,
            contact_name=cfg.contact_name,
            contact_url=cfg.contact_url,
        )

    @app.post("/settings")
    @login_required
    def settings_save():
        rules = request.form.get("rules", "").strip()
        email_tpl = request.form.get("email_tpl", "").strip()
        renew_tpl = request.form.get("renew_tpl", "").strip()
        db.set_setting("reminder_rules", rules)
        db.set_setting("email_template", email_tpl)
        db.set_setting("renewal_confirm_template", renew_tpl)
        flash("设置已保存", "success")
        return redirect(url_for("settings"))

    @app.post("/settings/scan")
    @login_required
    def settings_scan():
        threshold_days = int(request.form.get("threshold_days", "0"))
        stats = asyncio.run(scan_and_send(db, cfg, threshold_days=threshold_days))
        flash(
            f"扫描完成：已检查 {stats['checked_subscriptions']} 条，发送 {stats['sent']} 条",
            "info",
        )
        return redirect(url_for("settings"))

    @app.get("/logs")
    @login_required
    def logs():
        page = int(request.args.get("page", "1") or 1)
        page = max(page, 1)
        per_page = 50
        total = db.count_reminder_daily_logs()
        items = db.list_reminder_daily_logs(offset=(page - 1) * per_page, limit=per_page)
        total_pages = max(1, (total + per_page - 1) // per_page)
        return render_template("logs.html", items=items, page=page, total_pages=total_pages)

    @app.get("/export/<kind>")
    @login_required
    def export(kind: str):
        if kind == "customers":
            rows = db.list_customers(offset=0, limit=5000)
            fields = ["id", "email", "name", "created_at"]
        elif kind == "products":
            rows = db.list_products(offset=0, limit=5000)
            fields = ["id", "name", "content", "created_at"]
        elif kind == "subscriptions":
            rows = db.list_all_subscription_details(limit=5000)
            fields = [
                "id",
                "customer_id",
                "customer_email",
                "customer_name",
                "product_id",
                "product_name",
                "expires_at",
                "note",
                "created_at",
            ]
        else:
            flash("未知导出类型", "danger")
            return redirect(url_for("dashboard"))

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fields})

        mem = io.BytesIO(output.getvalue().encode("utf-8"))
        mem.seek(0)
        return send_file(
            mem,
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"{kind}.csv",
        )

    return app


def main() -> None:
    app = create_app()
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
