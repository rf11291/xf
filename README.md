# Telegram 管理员续费提醒机器人（按钮菜单版｜仅你使用）

特性（v5）：
1) **即将到期天数可自定义**：菜单提供 7/14/30/60 天 + 自定义输入。
2) **产品库 + 订阅**：一个产品可被多个客户使用；客户与产品通过“订阅（Subscription）”关联。
3) **到期时间可编辑**：订阅详情里可直接修改到期日（用于续费）。
4) **默认模板联系信息**：默认改为 Telegram 联系方式（见下方模板默认值）。

功能：
- 管理客户：新增、列表、详情、删除
- 管理产品库：新增、列表、详情、删除（被订阅使用时禁止删除）
- 管理订阅：给客户添加产品订阅、查看订阅详情、修改到期日、删除订阅
- 邮件：SMTP 发送 HTML 邮件（Jinja2 模板）
- 提醒规则：例如 30/7/1/0 天前自动提醒
- 定时扫描：默认每 15 分钟扫描；也可在菜单点“立即扫描发送”

---

## 环境变量
复制 `.env.example` 为 `.env` 并填写：
- `BOT_TOKEN`
- `ADMIN_IDS`（你的 TG user id，逗号分隔）
- SMTP 配置：`SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS/SMTP_FROM`

---

## 数据库
默认：`./data/bot.db`

---

## 使用方式
私聊 bot 输入 `/start` 即可打开管理面板（按钮）。

---

## 模板变量（Jinja2）
为了兼容旧模板，邮件渲染时会提供：

- `customer`: `id`, `email`, `name`
- `product_def`: 产品库信息 `id`, `name`, `content`
- `subscription`: 订阅信息 `id`, `customer_id`, `product_id`, `expires_at`, `note`
- `product`: **兼容字段**（等同于 `product_def` + `expires_at`，并且 `content` 会优先取 `subscription.note`，否则取 `product_def.content`）
- `days_before`, `days_left`, `now`, `company`

---

## 默认模板片段（已改为 Telegram 联系）
默认 HTML 内最后一段：

```html
<p>如需继续续费使用，请联系 <a href="https://t.me/Stance9_bot" target="_blank" rel="noopener noreferrer">Telegram</a>。</p>
```

---

## Docker 启动
```bash
docker compose up -d --build
docker compose logs -f
```

停止：
```bash
docker compose down
```


## 邮件送达率建议（重要）
代码层面 v5 已补齐常见“垃圾邮件拦截”触发项（Date、Message-ID、multipart/alternative 纯文本+HTML、稳定 EHLO），
但**最终是否进垃圾箱更多取决于你的域名/IP 信誉与 DNS 配置**。建议你同时完成：

1) **SPF**：给发送域名添加 SPF 记录，允许你的 SMTP 服务器发信  
2) **DKIM**：开启 DKIM 签名（大幅提升可信度）  
3) **DMARC**：配置 DMARC（至少 p=none 开始）  
4) **PTR/反向解析**：发信 IP 的 rDNS/PTR 与 HELO/域名尽量一致  
5) **From 显示名**：建议在 `.env` 里把 `SMTP_FROM` 写成 `公司名 <noreply@yourdomain.com>`  
6) **发信域名选择**：尽量使用信誉更好的域名/TLD；如果是营销/批量发信，建议用专业邮件服务商（SendGrid/Mailgun/Amazon SES 等）


## 发送策略（v6）
- **定时任务**：当订阅“剩余天数 ≤ 提醒规则中最大的天数”时开始 **每天发送一次**（同一订阅同一天最多发送一次）。
- **停止条件**：到期后超过 1 天（days_left < -1）自动停止；若你在订阅里把到期日改到未来（续费），则会按新到期日重新计算。
- **立即扫描发送**：会先让你选择一个“剩余天数 ≤ 阈值”，然后只对满足阈值的订阅立即发送（同样遵守同日仅一次）。
