# 续费通知管理系统（Web 面板版）

本项目基于原先 Telegram 机器人逻辑重新构建为**现代化 Web 管理面板**，保持原有功能的同时，增加仪表盘、日志、搜索、导出等能力，适合团队或个人在浏览器里集中管理续费提醒。

## 亮点功能

### 核心能力（复制原库能力）
- 客户管理：新增/编辑/删除客户
- 产品库管理：新增/编辑/删除产品（被订阅时自动阻止删除）
- 订阅管理：为客户添加订阅、编辑到期日/备注、删除订阅
- 邮件提醒：SMTP 发送 HTML 邮件（Jinja2 模板）
- 提醒规则：支持 30/7/1/0 等自定义阈值
- 定时扫描：按配置定期扫描并发送提醒
- 手动发送：支持单条订阅立即提醒
- 续费确认：续费后自动发送确认邮件

### 增强能力（更完善与多样化）
- **Web 仪表盘**：一眼看清客户、产品、订阅数量与即将到期列表
- **可配置到期天数**：仪表盘支持自定义展示到期范围
- **搜索与过滤**：支持按客户、邮箱、产品名称搜索
- **提醒日志**：查看每次实际发送记录
- **导出 CSV**：客户/产品/订阅数据一键导出
- **统一配置页**：提醒规则 & 两类邮件模板集中编辑
- **默认联系方式变量**：模板里可直接引用 CONTACT_NAME / CONTACT_URL

---

## 技术栈
- **Flask** + **Bootstrap 5**
- **SQLite** 本地数据库
- **APScheduler** 定时任务
- **SMTP** 发送提醒邮件

---

## 快速开始

### 1. 环境准备
复制 `.env.example` 为 `.env` 并填写：

```bash
cp .env.example .env
```

### 2. 运行（本地）
```bash
pip install -r requirements.txt
python -m app.web
```

打开浏览器访问：
```
http://localhost:8000
```

默认账号密码来自 `.env`：
- `ADMIN_USER`
- `ADMIN_PASSWORD`

### 3. Docker 运行
```bash
docker compose up -d --build
```

访问：
```
http://localhost:8000
```

停止：
```bash
docker compose down
```

---

## 环境变量说明（.env）

| 变量 | 说明 |
|------|------|
| ADMIN_USER | 管理员账号 |
| ADMIN_PASSWORD | 管理员密码 |
| SECRET_KEY | Flask Session 密钥 |
| TZ | 时区（例如 Asia/Shanghai） |
| DATABASE_PATH | 数据库路径（默认 ./data/bot.db） |
| COMPANY_NAME | 公司名称 |
| CONTACT_NAME | 模板中展示的联系名称 |
| CONTACT_URL | 模板中展示的联系链接 |
| SCAN_INTERVAL_MINUTES | 定时扫描间隔（分钟） |
| PORT | Web 服务端口 |
| SMTP_HOST | SMTP 服务器 |
| SMTP_PORT | SMTP 端口 |
| SMTP_USER | SMTP 用户 |
| SMTP_PASS | SMTP 密码 |
| SMTP_FROM | 发件人 |
| SMTP_TIMEOUT | SMTP 连接超时（秒） |

---

## 邮件模板变量（Jinja2）

兼容原逻辑，并新增联系方式变量：

| 变量 | 说明 |
|------|------|
| customer | 客户信息：id/email/name |
| product_def | 产品库信息：id/name/content |
| subscription | 订阅信息：id/customer_id/product_id/expires_at/note |
| product | 兼容字段（product_def + expires_at + note 优先） |
| days_before | 当前提醒阈值 |
| days_left | 剩余天数 |
| threshold | 当前扫描阈值 |
| now | 当前时间 |
| company | 公司名称 |
| contact_name | 联系人名称（来自 CONTACT_NAME） |
| contact_url | 联系链接（来自 CONTACT_URL） |
| old_expires_at | 续费前日期 |
| new_expires_at | 续费后日期 |
| renew_days | 本次续费天数 |

---

## 数据库

默认位置：`./data/bot.db`  
可通过 `DATABASE_PATH` 自定义。

---

## 常见使用流程
1. 进入「客户管理」添加客户
2. 进入「产品库」新增产品
3. 进入「订阅管理」新增订阅并设置到期日
4. 在「提醒规则 & 模板」设置提醒节奏与邮件内容
5. 系统会定时扫描，也可以手动触发

---

## 提醒送达率建议（重要）
为了提升邮件送达率，请务必配置好 SPF/DKIM/DMARC，并确保发送域名与 SMTP 服务器匹配。
此外建议使用专业邮件服务（如 SendGrid/Mailgun/Amazon SES）以提高成功率。

---

## 运行截图
项目为纯 Web 面板，欢迎根据实际需求继续扩展（权限、团队协作、多语言、API 接口等）。
