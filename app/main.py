from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import load_config, Config
from .db import Database
from .reminders import scan_and_send, send_subscription_now, send_renewal_confirm
from .templates import render_template
from .mailer import send_html_email

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# --- State machine (管理员单人后台，按钮+文字录入) ---
STATE_NONE = ""
STATE_ADD_CUST_NAME = "ADD_CUST_NAME"
STATE_ADD_CUST_EMAIL = "ADD_CUST_EMAIL"

STATE_ADD_CATALOG_NAME = "ADD_CATALOG_NAME"
STATE_ADD_CATALOG_CONTENT = "ADD_CATALOG_CONTENT"

STATE_ADD_SUB_PICK_CUST = "ADD_SUB_PICK_CUST"
STATE_ADD_SUB_PICK_PROD = "ADD_SUB_PICK_PROD"
STATE_ADD_SUB_EXPIRES = "ADD_SUB_EXPIRES"
STATE_ADD_SUB_NOTE = "ADD_SUB_NOTE"

STATE_EDIT_SUB_EXPIRES = "EDIT_SUB_EXPIRES"

STATE_SET_RULES = "SET_RULES"
STATE_SET_TPL_SUBJECT = "SET_TPL_SUBJECT"
STATE_SET_TPL_HTML = "SET_TPL_HTML"

STATE_EXP_CUSTOM_DAYS = "EXP_CUSTOM_DAYS"
STATE_SCAN_CUSTOM_DAYS = "SCAN_CUSTOM_DAYS"

def _is_admin(cfg: Config, update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    return uid is not None and uid in cfg.admin_ids

def _kb(rows):
    return InlineKeyboardMarkup(rows)

def _main_menu_kb():
    return _kb([
        [InlineKeyboardButton("👥 客户管理", callback_data="menu:customers")],
        [InlineKeyboardButton("📦 产品/订阅管理", callback_data="menu:subs")],
        [InlineKeyboardButton("✉️ 邮件模板", callback_data="menu:template")],
        [InlineKeyboardButton("⏰ 提醒规则", callback_data="menu:rules")],
        [InlineKeyboardButton("🚀 立即扫描发送", callback_data="action:scan_now")],
        [InlineKeyboardButton("❓ 帮助", callback_data="menu:help")],
    ])

def _back_kb(target="menu:home"):
    return _kb([[InlineKeyboardButton("🔙 返回", callback_data=target)]])

def _set_state(context: ContextTypes.DEFAULT_TYPE, state: str, **kwargs):
    context.user_data["state"] = state
    for k, v in kwargs.items():
        context.user_data[k] = v

def _get_state(context: ContextTypes.DEFAULT_TYPE) -> str:
    return str(context.user_data.get("state") or STATE_NONE)

def _clear_state(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

async def _send_or_edit(update: Update, text: str, reply_markup=None):
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
        except Exception:
            await update.effective_chat.send_message(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

# ---------- Scheduler hooks ----------
async def scheduled_scan(app: Application):
    db: Database = app.bot_data["db"]
    cfg: Config = app.bot_data["cfg"]
    stats = await scan_and_send(db, cfg)
    print("[scan]", stats)

async def post_init(app: Application) -> None:
    cfg: Config = app.bot_data["cfg"]
    scheduler = AsyncIOScheduler(timezone=cfg.tz)
    scheduler.add_job(
        lambda: asyncio.create_task(scheduled_scan(app)),
        "interval",
        minutes=cfg.scan_interval_minutes,
        id="scan_job",
        replace_existing=True,
    )
    scheduler.start()
    app.bot_data["scheduler"] = scheduler
    print("Scheduler started.")

async def post_shutdown(app: Application) -> None:
    scheduler = app.bot_data.get("scheduler")
    if scheduler:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass

# ---------- /start & /cancel ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg: Config = context.application.bot_data["cfg"]
    if not _is_admin(cfg, update):
        await update.message.reply_text("无权限：此机器人仅管理员可用。")
        return
    _clear_state(context)
    await update.message.reply_text("管理面板：", reply_markup=_main_menu_kb())

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg: Config = context.application.bot_data["cfg"]
    if not _is_admin(cfg, update):
        return
    _clear_state(context)
    await update.message.reply_text("已取消。", reply_markup=_main_menu_kb())

# ---------- UI: customers ----------
async def show_customer_list(update: Update, context: ContextTypes.DEFAULT_TYPE, offset: int):
    db: Database = context.application.bot_data["db"]
    total = db.count_customers()
    items = db.list_customers(offset=offset, limit=10)

    rows = []
    for c in items:
        label = f"#{c['id']} {c.get('name') or ''} <{c['email']}>".strip()
        rows.append([InlineKeyboardButton(label, callback_data=f"cust:view:{c['id']}")])

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"cust:list:{max(0, offset-10)}"))
    if offset + 10 < total:
        nav.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"cust:list:{offset+10}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("🔙 返回", callback_data="menu:customers")])
    await _send_or_edit(update, f"客户列表（{offset+1}-{min(offset+10,total)}/{total}）：", reply_markup=_kb(rows))

async def show_customer_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, customer_id: int):
    db: Database = context.application.bot_data["db"]
    c = db.get_customer(customer_id)
    if not c:
        await _send_or_edit(update, "客户不存在。", reply_markup=_back_kb("menu:customers"))
        return
    msg = f"客户详情\n\nID: {c['id']}\nName: {c.get('name') or '-'}\nEmail: {c['email']}"
    kb = _kb([
        [InlineKeyboardButton("➕ 添加订阅", callback_data=f"sub:add:from_cust:{customer_id}")],
        [InlineKeyboardButton("📦 查看订阅", callback_data=f"sub:list:{customer_id}")],
        [InlineKeyboardButton("🗑️ 删除客户", callback_data=f"cust:del:confirm:{customer_id}")],
        [InlineKeyboardButton("🔙 返回", callback_data="cust:list:0")],
    ])
    await _send_or_edit(update, msg, reply_markup=kb)

# ---------- UI: catalog (products) ----------
async def show_catalog_list(update: Update, context: ContextTypes.DEFAULT_TYPE, offset: int):
    db: Database = context.application.bot_data["db"]
    total = db.count_products()
    items = db.list_products(offset=offset, limit=10)

    rows = []
    for p in items:
        label = f"#{p['id']} {p['name']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"cat:view:{p['id']}")])

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"cat:list:{max(0, offset-10)}"))
    if offset + 10 < total:
        nav.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"cat:list:{offset+10}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("🔙 返回", callback_data="menu:catalog")])
    await _send_or_edit(update, f"产品库列表（{offset+1}-{min(offset+10,total)}/{total}）：", reply_markup=_kb(rows))

async def show_catalog_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
    db: Database = context.application.bot_data["db"]
    p = db.get_product(product_id)
    if not p:
        await _send_or_edit(update, "产品不存在。", reply_markup=_back_kb("menu:catalog"))
        return
    used = db.count_subscriptions_for_product(product_id)
    msg = (
        f"产品详情（产品库）\n\n"
        f"ID: {p['id']}\n"
        f"名称: {p['name']}\n"
        f"内容: {p.get('content') or '-'}\n"
        f"订阅数量: {used}"
    )
    kb_rows = [
        [InlineKeyboardButton("🗑️ 删除产品", callback_data=f"cat:del:confirm:{product_id}")],
        [InlineKeyboardButton("🔙 返回", callback_data="cat:list:0")],
    ]
    await _send_or_edit(update, msg, reply_markup=_kb(kb_rows))

async def show_product_picker(update: Update, context: ContextTypes.DEFAULT_TYPE, offset: int):
    db: Database = context.application.bot_data["db"]
    total = db.count_products()
    items = db.list_products(offset=offset, limit=10)

    rows = []
    for p in items:
        label = f"#{p['id']} {p['name']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"sub:add:choose_prod:{p['id']}")])

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"sub:add:pick_prod:{max(0, offset-10)}"))
    if offset + 10 < total:
        nav.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"sub:add:pick_prod:{offset+10}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("取消", callback_data="menu:subs")])
    await _send_or_edit(update, "请选择产品（产品库）：", reply_markup=_kb(rows))

# ---------- UI: subscriptions ----------
async def show_customer_picker(update: Update, context: ContextTypes.DEFAULT_TYPE, offset: int, cb_choose_prefix: str, cb_pick_prefix: str, cancel_cb: str):
    db: Database = context.application.bot_data["db"]
    total = db.count_customers()
    items = db.list_customers(offset=offset, limit=10)

    rows = []
    for c in items:
        label = f"#{c['id']} {c.get('name') or ''} <{c['email']}>".strip()
        rows.append([InlineKeyboardButton(label, callback_data=f"{cb_choose_prefix}{c['id']}")])

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"{cb_pick_prefix}{max(0, offset-10)}"))
    if offset + 10 < total:
        nav.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"{cb_pick_prefix}{offset+10}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("取消", callback_data=cancel_cb)])
    await _send_or_edit(update, "请选择客户：", reply_markup=_kb(rows))

async def show_subscription_list(update: Update, context: ContextTypes.DEFAULT_TYPE, customer_id: int):
    db: Database = context.application.bot_data["db"]
    c = db.get_customer(customer_id)
    if not c:
        await _send_or_edit(update, "客户不存在。", reply_markup=_back_kb("menu:subs"))
        return

    items = db.list_subscriptions_by_customer(customer_id, offset=0, limit=100)
    rows = []
    for s in items:
        label = f"#{s['id']} {s.get('product_name')} | 到期 {s['expires_at']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"sub:view:{s['id']}")])

    rows.append([InlineKeyboardButton("➕ 添加订阅", callback_data=f"sub:add:from_cust:{customer_id}")])
    rows.append([InlineKeyboardButton("🔙 返回", callback_data="menu:subs")])
    await _send_or_edit(update, f"客户 #{customer_id} 订阅列表：\n{c.get('name') or ''} <{c['email']}>", reply_markup=_kb(rows))

async def show_subscription_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, subscription_id: int):
    db: Database = context.application.bot_data["db"]
    s = db.get_subscription_detail(subscription_id)
    if not s:
        await _send_or_edit(update, "订阅不存在。", reply_markup=_back_kb("menu:subs"))
        return

    msg = (
        f"订阅详情\n\n"
        f"订阅ID: {s['id']}\n"
        f"客户: {s.get('customer_name') or ''} <{s.get('customer_email')}>\n"
        f"产品: {s.get('product_name')}\n"
        f"到期: {s.get('expires_at')}\n"
        f"产品内容: {s.get('product_content') or '-'}\n"
        f"客户备注: {s.get('note') or '-'}"
    )
    kb = _kb([
    [InlineKeyboardButton("✏️ 修改到期日", callback_data=f"sub:edit_exp:{subscription_id}")],
    [InlineKeyboardButton("🔁 续费 +30 天", callback_data=f"sub:renew:30:{subscription_id}"),
     InlineKeyboardButton("🔁 续费 +90 天", callback_data=f"sub:renew:90:{subscription_id}")],
    [InlineKeyboardButton("🔁 续费 +365 天", callback_data=f"sub:renew:365:{subscription_id}")],
    [InlineKeyboardButton("✉️ 立即发送邮件", callback_data=f"action:send_now_sub:{subscription_id}")],
    [InlineKeyboardButton("🗑️ 删除订阅", callback_data=f"sub:del:confirm:{subscription_id}")],
    [InlineKeyboardButton("🔙 返回", callback_data=f"sub:list:{s['customer_id']}")],
])
    await _send_or_edit(update, msg, reply_markup=kb)

async def show_expiring_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = _kb([
        [InlineKeyboardButton("7 天内", callback_data="exp:days:7:0"), InlineKeyboardButton("14 天内", callback_data="exp:days:14:0")],
        [InlineKeyboardButton("30 天内", callback_data="exp:days:30:0"), InlineKeyboardButton("60 天内", callback_data="exp:days:60:0")],
        [InlineKeyboardButton("🧮 自定义天数", callback_data="exp:custom")],
        [InlineKeyboardButton("🔙 返回", callback_data="menu:subs")],
    ])
    await _send_or_edit(update, "查看即将到期订阅：", reply_markup=kb)

async def show_expiring_list(update: Update, context: ContextTypes.DEFAULT_TYPE, days: int, offset: int):
    db: Database = context.application.bot_data["db"]
    all_items = db.list_subscriptions_expiring_within(days=days, offset=0, limit=5000)
    total = len(all_items)
    items = all_items[offset:offset+20]

    if total == 0:
        await _send_or_edit(update, f"暂无 {days} 天内到期订阅。", reply_markup=_back_kb("exp:menu"))
        return

    rows = []
    for s in items:
        cust = f"{(s.get('customer_name') or '')} <{s.get('customer_email')}>"
        label = f"#{s['id']} {s.get('product_name')} | {s.get('expires_at')} | {cust}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"sub:view:{s['id']}")])

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"exp:days:{days}:{max(0, offset-20)}"))
    if offset + 20 < total:
        nav.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"exp:days:{days}:{offset+20}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("🔙 返回", callback_data="exp:menu")])
    await _send_or_edit(update, f"即将到期（{days}天内）（{offset+1}-{min(offset+20,total)}/{total}）：", reply_markup=_kb(rows))

# ---------- actions ----------
async def show_scan_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = _kb([
        [InlineKeyboardButton("≤7 天", callback_data="scan:do:7"), InlineKeyboardButton("≤14 天", callback_data="scan:do:14")],
        [InlineKeyboardButton("≤30 天", callback_data="scan:do:30"), InlineKeyboardButton("≤60 天", callback_data="scan:do:60")],
        [InlineKeyboardButton("🧮 自定义阈值", callback_data="scan:custom")],
        [InlineKeyboardButton("🔙 返回", callback_data="menu:home")],
    ])
    await _send_or_edit(update, "立即扫描发送：请选择『剩余天数 ≤ 阈值』的订阅进行发送。", reply_markup=kb)

async def action_scan_do(update: Update, context: ContextTypes.DEFAULT_TYPE, threshold_days: int):
    db: Database = context.application.bot_data["db"]
    cfg: Config = context.application.bot_data["cfg"]
    await _send_or_edit(update, f"开始扫描并发送中…（阈值：≤{threshold_days}天）")
    stats = await scan_and_send(db, cfg, threshold_days=threshold_days)
    await update.effective_chat.send_message(
        f"完成 ✅\n{json.dumps(stats, ensure_ascii=False)}",
        reply_markup=_main_menu_kb(),
    )

async def action_send_now_sub(update: Update, context: ContextTypes.DEFAULT_TYPE, subscription_id: int):
    cfg: Config = context.application.bot_data["cfg"]
    db: Database = context.application.bot_data["db"]

    try:
        await update.callback_query.answer("发送中…")
    except Exception:
        pass

    progress = await update.effective_chat.send_message("✉️ 正在发送邮件…")
    try:
        s = db.get_subscription_detail(int(subscription_id))
        result = await send_subscription_now(db, cfg, subscription_id)
        if result.get("ok"):
            to_email = (s.get("customer_email") if s else "") or result.get("to") or ""
            await progress.edit_text(f"✅ 已发送邮件到：{to_email}\n订阅ID：{subscription_id}")
        else:
            await progress.edit_text(f"⚠️ 未发送：{result.get('reason')}")
    except Exception as e:
        await progress.edit_text(f"❌ 发送失败：{e}")

# ---------- Callback router ----------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg: Config = context.application.bot_data["cfg"]
    if not _is_admin(cfg, update):
        await update.callback_query.answer("无权限", show_alert=True)
        return

    data = update.callback_query.data
    db: Database = context.application.bot_data["db"]

    # global
    if data == "menu:home":
        _clear_state(context)
        await _send_or_edit(update, "管理面板：", reply_markup=_main_menu_kb())
        return

    if data == "menu:help":
        msg = (
            "用法：\n"
            "• 通过按钮管理客户/产品库/订阅/模板/规则\n"
            "• 需要输入文字时，直接发送消息即可\n"
            "• 日期格式：YYYY-MM-DD\n"
            "• 输入 /cancel 可取消当前录入\n"
        )
        await _send_or_edit(update, msg, reply_markup=_back_kb())
        return

    # customers menu
    if data == "menu:customers":
        _clear_state(context)
        kb = _kb([
            [InlineKeyboardButton("➕ 新增客户", callback_data="cust:add")],
            [InlineKeyboardButton("📋 客户列表", callback_data="cust:list:0")],
            [InlineKeyboardButton("🔙 返回", callback_data="menu:home")],
        ])
        await _send_or_edit(update, "客户管理：", reply_markup=kb)
        return

    if data == "cust:add":
        _set_state(context, STATE_ADD_CUST_NAME)
        await _send_or_edit(update, "请输入客户名称（可留空，发送 - 表示空）：", reply_markup=_back_kb("menu:customers"))
        return

    if data.startswith("cust:list:"):
        _clear_state(context)
        offset = int(data.split(":")[-1])
        await show_customer_list(update, context, offset)
        return

    if data.startswith("cust:view:"):
        _clear_state(context)
        cid = int(data.split(":")[-1])
        await show_customer_detail(update, context, cid)
        return

    if data.startswith("cust:del:confirm:"):
        cid = int(data.split(":")[-1])
        kb = _kb([
            [InlineKeyboardButton("✅ 确认删除", callback_data=f"cust:del:do:{cid}")],
            [InlineKeyboardButton("取消", callback_data=f"cust:view:{cid}")],
        ])
        await _send_or_edit(update, f"确认删除客户 #{cid} 及其全部订阅？", reply_markup=kb)
        return

    if data.startswith("cust:del:do:"):
        cid = int(data.split(":")[-1])
        db.delete_customer(cid)
        await _send_or_edit(update, "已删除 ✅", reply_markup=_back_kb("menu:customers"))
        return

    # subs menu
    if data == "menu:subs":
        _clear_state(context)
        kb = _kb([
            [InlineKeyboardButton("📚 产品库", callback_data="menu:catalog")],
            [InlineKeyboardButton("➕ 添加客户订阅", callback_data="sub:add")],
            [InlineKeyboardButton("🔎 按客户查看订阅", callback_data="sub:by_customer:pick:0")],
            [InlineKeyboardButton("📅 查看即将到期", callback_data="exp:menu")],
            [InlineKeyboardButton("🔙 返回", callback_data="menu:home")],
        ])
        await _send_or_edit(update, "产品/订阅管理：", reply_markup=kb)
        return

    # catalog menu
    if data == "menu:catalog":
        _clear_state(context)
        kb = _kb([
            [InlineKeyboardButton("➕ 新增产品", callback_data="cat:add")],
            [InlineKeyboardButton("📋 产品列表", callback_data="cat:list:0")],
            [InlineKeyboardButton("🔙 返回", callback_data="menu:subs")],
        ])
        await _send_or_edit(update, "产品库：", reply_markup=kb)
        return

    if data == "cat:add":
        _set_state(context, STATE_ADD_CATALOG_NAME)
        await _send_or_edit(update, "请输入产品名称（产品库，唯一）：", reply_markup=_back_kb("menu:catalog"))
        return

    if data.startswith("cat:list:"):
        _clear_state(context)
        offset = int(data.split(":")[-1])
        await show_catalog_list(update, context, offset)
        return

    if data.startswith("cat:view:"):
        _clear_state(context)
        pid = int(data.split(":")[-1])
        await show_catalog_detail(update, context, pid)
        return

    if data.startswith("cat:del:confirm:"):
        pid = int(data.split(":")[-1])
        used = db.count_subscriptions_for_product(pid)
        if used > 0:
            await update.callback_query.answer(f"该产品已被 {used} 个订阅使用，不能删除。", show_alert=True)
            return
        kb = _kb([
            [InlineKeyboardButton("✅ 确认删除", callback_data=f"cat:del:do:{pid}")],
            [InlineKeyboardButton("取消", callback_data=f"cat:view:{pid}")],
        ])
        await _send_or_edit(update, f"确认删除产品 #{pid}？", reply_markup=kb)
        return

    if data.startswith("cat:del:do:"):
        pid = int(data.split(":")[-1])
        ok = db.delete_product(pid)
        if not ok:
            await update.callback_query.answer("该产品仍被订阅使用，无法删除。", show_alert=True)
            return
        await _send_or_edit(update, "已删除 ✅", reply_markup=_back_kb("menu:catalog"))
        return

    # add subscription
    if data == "sub:add":
        _set_state(context, STATE_ADD_SUB_PICK_CUST)
        await show_customer_picker(
            update, context, offset=0,
            cb_choose_prefix="sub:add:choose_cust:",
            cb_pick_prefix="sub:add:pick_cust:",
            cancel_cb="menu:subs",
        )
        return

    if data.startswith("sub:add:pick_cust:"):
        offset = int(data.split(":")[-1])
        await show_customer_picker(
            update, context, offset=offset,
            cb_choose_prefix="sub:add:choose_cust:",
            cb_pick_prefix="sub:add:pick_cust:",
            cancel_cb="menu:subs",
        )
        return

    if data.startswith("sub:add:choose_cust:"):
        cid = int(data.split(":")[-1])
        if not db.get_customer(cid):
            await update.callback_query.answer("客户不存在", show_alert=True)
            return
        _set_state(context, STATE_ADD_SUB_PICK_PROD, sub_customer_id=cid)
        await show_product_picker(update, context, offset=0)
        return

    if data.startswith("sub:add:from_cust:"):
        cid = int(data.split(":")[-1])
        if not db.get_customer(cid):
            await update.callback_query.answer("客户不存在", show_alert=True)
            return
        _set_state(context, STATE_ADD_SUB_PICK_PROD, sub_customer_id=cid)
        await show_product_picker(update, context, offset=0)
        return

    if data.startswith("sub:add:pick_prod:"):
        offset = int(data.split(":")[-1])
        await show_product_picker(update, context, offset=offset)
        return

    if data.startswith("sub:add:choose_prod:"):
        pid = int(data.split(":")[-1])
        if not db.get_product(pid):
            await update.callback_query.answer("产品不存在", show_alert=True)
            return
        _set_state(context, STATE_ADD_SUB_EXPIRES, sub_product_id=pid)
        await _send_or_edit(update, "请输入到期日期（YYYY-MM-DD，例如 2026-02-01）：", reply_markup=_back_kb("menu:subs"))
        return

    # list subscriptions by customer
    if data.startswith("sub:by_customer:pick:"):
        offset = int(data.split(":")[-1])
        _clear_state(context)
        await show_customer_picker(
            update, context, offset=offset,
            cb_choose_prefix="sub:list:",
            cb_pick_prefix="sub:by_customer:pick:",
            cancel_cb="menu:subs",
        )
        return

    if data.startswith("sub:list:"):
        _clear_state(context)
        cid = int(data.split(":")[-1])
        await show_subscription_list(update, context, cid)
        return

    if data.startswith("sub:view:"):
        _clear_state(context)
        sid = int(data.split(":")[-1])
        await show_subscription_detail(update, context, sid)
        return

    if data.startswith("sub:del:confirm:"):
        sid = int(data.split(":")[-1])
        kb = _kb([
            [InlineKeyboardButton("✅ 确认删除", callback_data=f"sub:del:do:{sid}")],
            [InlineKeyboardButton("取消", callback_data=f"sub:view:{sid}")],
        ])
        await _send_or_edit(update, f"确认删除订阅 #{sid}？", reply_markup=kb)
        return

    if data.startswith("sub:del:do:"):
        sid = int(data.split(":")[-1])
        db.delete_subscription(sid)
        await _send_or_edit(update, "已删除 ✅", reply_markup=_back_kb("menu:subs"))
        return

    if data.startswith("sub:edit_exp:"):
        sid = int(data.split(":")[-1])
        if not db.get_subscription_detail(sid):
            await update.callback_query.answer("订阅不存在", show_alert=True)
            return
        _set_state(context, STATE_EDIT_SUB_EXPIRES, edit_sub_id=sid)
        await _send_or_edit(update, "请输入新的到期日期（YYYY-MM-DD）：", reply_markup=_back_kb("menu:subs"))
        return

    # expiring
    if data == "exp:menu":
        _clear_state(context)
        await show_expiring_menu(update, context)
        return

    if data.startswith("exp:days:"):
        _clear_state(context)
        _, _, days, offset = data.split(":")
        await show_expiring_list(update, context, int(days), int(offset))
        return

    if data == "exp:custom":
        _set_state(context, STATE_EXP_CUSTOM_DAYS)
        await _send_or_edit(update, "请输入要查看的天数（整数，例如 45）：", reply_markup=_back_kb("exp:menu"))
        return

    # template
    if data == "menu:template":
        _clear_state(context)
        tpl_raw = db.get_setting("email_template") or "{}"
        tpl = json.loads(tpl_raw)
        subject = tpl.get("subject", "")
        kb = _kb([
            [InlineKeyboardButton("✏️ 修改主题", callback_data="tpl:set_subject")],
            [InlineKeyboardButton("🧩 修改HTML正文", callback_data="tpl:set_html")],
            [InlineKeyboardButton("🔙 返回", callback_data="menu:home")],
        ])
        await _send_or_edit(update, f"当前邮件主题：\n{subject}", reply_markup=kb)
        return

    if data == "tpl:set_subject":
        _set_state(context, STATE_SET_TPL_SUBJECT)
        await _send_or_edit(update, "请输入新的邮件主题模板（Jinja2）：", reply_markup=_back_kb("menu:template"))
        return

    if data == "tpl:set_html":
        _set_state(context, STATE_SET_TPL_HTML)
        await _send_or_edit(update, "请输入新的 HTML 正文模板（Jinja2，多行直接粘贴）：", reply_markup=_back_kb("menu:template"))
        return

    # rules
    if data == "menu:rules":
        _clear_state(context)
        rules_raw = db.get_setting("reminder_rules") or "[]"
        kb = _kb([
            [InlineKeyboardButton("✏️ 修改规则", callback_data="rules:set")],
            [InlineKeyboardButton("🔙 返回", callback_data="menu:home")],
        ])
        await _send_or_edit(update, f"提醒规则：{rules_raw}", reply_markup=kb)
        return

    if data == "rules:set":
        rules_raw = db.get_setting("reminder_rules") or "[]"
        _set_state(context, STATE_SET_RULES)
        await _send_or_edit(update, f"当前规则：{rules_raw}\n\n请输入新规则（逗号分隔，例如：30,7,1,0）：", reply_markup=_back_kb("menu:rules"))
        return

    # actions
    if data == "action:scan_now":
        _clear_state(context)
        await show_scan_menu(update, context)
        return

    if data.startswith("action:send_now_sub:"):
        sid = int(data.split(":")[-1])
        await action_send_now_sub(update, context, sid)
        return

    
    # scan (manual threshold)
    if data.startswith("scan:do:"):
        days = int(data.split(":")[-1])
        _clear_state(context)
        try:
            await update.callback_query.answer(f"开始扫描（≤{days}天）…")
        except Exception:
            pass
        progress = await update.effective_chat.send_message(f"🚀 正在扫描并发送（剩余天数 ≤ {days}）…")
        try:
            stats = await scan_and_send(db, cfg, threshold_days=days)
            await progress.edit_text(f"完成 ✅\n{json.dumps(stats, ensure_ascii=False)}")
        except Exception as e:
            await progress.edit_text(f"❌ 扫描发送失败：{e}")
        return

    if data == "scan:custom":
        _set_state(context, STATE_SCAN_CUSTOM_DAYS)
        await _send_or_edit(update, "请输入阈值天数（整数，例如 45）：", reply_markup=_back_kb("action:scan_now"))
        return

    # renew buttons
    if data.startswith("sub:renew:"):
        parts = data.split(":")
        add_days = int(parts[2])
        sid = int(parts[3])

        s = db.get_subscription_detail(sid)
        if not s:
            await update.callback_query.answer("订阅不存在", show_alert=True)
            return

        try:
            await update.callback_query.answer("续费处理中…")
        except Exception:
            pass

        progress = await update.effective_chat.send_message("🔁 正在续费并发送确认邮件…")
        try:
            today = dt.date.today()
            old_exp = dt.date.fromisoformat(str(s["expires_at"]))
            base = old_exp if old_exp >= today else today
            new_exp = base + dt.timedelta(days=add_days)

            db.update_subscription_expires(sid, new_exp.isoformat())

            result = await send_renewal_confirm(
                db=db,
                cfg=cfg,
                subscription_id=sid,
                old_expires_at=old_exp.isoformat(),
                new_expires_at=new_exp.isoformat(),
                renew_days=add_days,
            )

            if result.get("ok"):
                await progress.edit_text(
                    f"✅ 续费成功\n订阅ID：{sid}\n原到期：{old_exp.isoformat()}\n新到期：{new_exp.isoformat()}\n确认邮件已发送至：{result.get('to')}"
                )
            else:
                await progress.edit_text(
                    f"✅ 到期日已更新\n订阅ID：{sid}\n原到期：{old_exp.isoformat()}\n新到期：{new_exp.isoformat()}\n⚠️ 未发送确认邮件：{result.get('reason')}"
                )

            await show_subscription_detail(update, context, sid)
        except Exception as e:
            await progress.edit_text(f"❌ 续费失败：{e}")
        return

    await update.callback_query.answer("未识别操作", show_alert=True)

# ---------- Text router ----------
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg: Config = context.application.bot_data["cfg"]
    if not _is_admin(cfg, update):
        return

    db: Database = context.application.bot_data["db"]
    state = _get_state(context)
    text = (update.message.text or "").strip()

    if state == STATE_ADD_CUST_NAME:
        name = "" if text == "-" else text
        _set_state(context, STATE_ADD_CUST_EMAIL, cust_name=(name or None))
        await update.message.reply_text("请输入客户邮箱（必填）：")
        return

    if state == STATE_ADD_CUST_EMAIL:
        if not EMAIL_RE.match(text):
            await update.message.reply_text("邮箱格式不正确，请重新输入：")
            return
        cid = db.upsert_customer(email=text, name=context.user_data.get("cust_name"))
        _clear_state(context)
        await update.message.reply_text(f"已保存客户 ✅ ID={cid}", reply_markup=_main_menu_kb())
        return

    if state == STATE_ADD_CATALOG_NAME:
        if not text:
            await update.message.reply_text("产品名称不能为空，请重新输入：")
            return
        _set_state(context, STATE_ADD_CATALOG_CONTENT, catalog_name=text)
        await update.message.reply_text("请输入产品内容/说明（可留空，发送 - 表示空）：")
        return

    if state == STATE_ADD_CATALOG_CONTENT:
        content = "" if text == "-" else text
        name = str(context.user_data.get("catalog_name") or "").strip()
        if not name:
            _clear_state(context)
            await update.message.reply_text("状态异常，已重置。", reply_markup=_main_menu_kb())
            return
        pid = db.add_product(name=name, content=(content or None))
        _clear_state(context)
        await update.message.reply_text(f"产品已保存 ✅ product_id={pid}", reply_markup=_main_menu_kb())
        return

    if state == STATE_ADD_SUB_EXPIRES:
        try:
            d = dt.date.fromisoformat(text)
        except Exception:
            await update.message.reply_text("日期格式错误，请输入 YYYY-MM-DD：")
            return
        _set_state(context, STATE_ADD_SUB_NOTE, sub_expires_at=d.isoformat())
        await update.message.reply_text("请输入客户备注（可留空，发送 - 表示空）：")
        return

    if state == STATE_ADD_SUB_NOTE:
        note = "" if text == "-" else text
        cid = context.user_data.get("sub_customer_id")
        pid = context.user_data.get("sub_product_id")
        exp = context.user_data.get("sub_expires_at")
        if not cid or not pid or not exp:
            _clear_state(context)
            await update.message.reply_text("状态异常，已重置。请重新操作。", reply_markup=_main_menu_kb())
            return
        sid = db.add_subscription(customer_id=int(cid), product_id=int(pid), expires_at=str(exp), note=(note or None))
        _clear_state(context)
        await update.message.reply_text(f"订阅已保存 ✅ subscription_id={sid}", reply_markup=_main_menu_kb())
        return

    if state == STATE_EDIT_SUB_EXPIRES:
        try:
            d = dt.date.fromisoformat(text)
        except Exception:
            await update.message.reply_text("日期格式错误，请输入 YYYY-MM-DD：")
            return
        sid = context.user_data.get("edit_sub_id")
        if not sid:
            _clear_state(context)
            await update.message.reply_text("状态异常，已重置。", reply_markup=_main_menu_kb())
            return
        db.update_subscription_expires(int(sid), d.isoformat())
        _clear_state(context)
        await update.message.reply_text("到期日已更新 ✅", reply_markup=_main_menu_kb())
        return

    if state == STATE_SET_RULES:
        raw = text.replace("，", ",")
        try:
            rules = [int(x.strip()) for x in raw.split(",") if x.strip() != ""]
            rules = sorted(set(rules), reverse=True)
        except Exception:
            await update.message.reply_text("格式错误，请重新输入（例如：30,7,1,0）：")
            return
        db.set_setting("reminder_rules", json.dumps(rules, ensure_ascii=False))
        _clear_state(context)
        await update.message.reply_text(f"已保存 ✅ {rules}", reply_markup=_main_menu_kb())
        return

    if state == STATE_SET_TPL_SUBJECT:
        if not text:
            await update.message.reply_text("主题不能为空，请重新输入：")
            return
        tpl_raw = db.get_setting("email_template") or "{}"
        tpl = json.loads(tpl_raw)
        tpl["subject"] = text
        db.set_setting("email_template", json.dumps(tpl, ensure_ascii=False))
        _clear_state(context)
        await update.message.reply_text("主题已保存 ✅", reply_markup=_main_menu_kb())
        return

    if state == STATE_SET_TPL_HTML:
        if not text:
            await update.message.reply_text("HTML 正文不能为空，请重新输入：")
            return
        tpl_raw = db.get_setting("email_template") or "{}"
        tpl = json.loads(tpl_raw)
        tpl["html"] = text
        db.set_setting("email_template", json.dumps(tpl, ensure_ascii=False))
        _clear_state(context)
        await update.message.reply_text("HTML 正文已保存 ✅", reply_markup=_main_menu_kb())
        return

    if state == STATE_EXP_CUSTOM_DAYS:
        try:
            days = int(text)
            if days <= 0 or days > 3650:
                raise ValueError()
        except Exception:
            await update.message.reply_text("请输入合法整数天数（1-3650）：")
            return
        _clear_state(context)
        await update.message.reply_text("正在查询…")
        await show_expiring_list(update, context, days=days, offset=0)
        return

    _clear_state(context)
    await update.message.reply_text(f"开始扫描并发送中…（阈值：≤{days}天）")
    stats = await scan_and_send(db, cfg, threshold_days=days)
    await update.message.reply_text(f"完成 ✅\n{json.dumps(stats, ensure_ascii=False)}", reply_markup=_main_menu_kb())
    return


    _clear_state(context)
    await update.message.reply_text(f"开始扫描并发送中…（阈值：≤{days}天）")
    stats = await scan_and_send(db, cfg, threshold_days=days)
    await update.message.reply_text(f"完成 ✅\n{json.dumps(stats, ensure_ascii=False)}", reply_markup=_main_menu_kb())
    return
    if state == STATE_SCAN_CUSTOM_DAYS:
        try:
            days = int(text)
            if days <= 0 or days > 3650:
                raise ValueError()
        except Exception:
            await update.message.reply_text("请输入合法整数天数（1-3650）：")
            return
        _clear_state(context)
        await update.message.reply_text(f"🚀 正在扫描并发送（剩余天数 ≤ {days}）…")
        try:
            stats = await scan_and_send(db, cfg, threshold_days=days)
            await update.message.reply_text(f"完成 ✅\\n{json.dumps(stats, ensure_ascii=False)}", reply_markup=_main_menu_kb())
        except Exception as e:
            await update.message.reply_text(f"❌ 扫描发送失败：{e}", reply_markup=_main_menu_kb())
        return



    await update.message.reply_text("当前没有进行中的录入流程。请使用 /start 打开菜单，或输入 /cancel 取消。")

def main():
    cfg = load_config()
    db = Database(cfg.database_path)
    db.init()

    app = (
        Application.builder()
        .token(cfg.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.bot_data["cfg"] = cfg
    app.bot_data["db"] = db

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    print("Bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
