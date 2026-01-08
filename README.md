# 续费通知管理面板（Go Web 版）

将原本的 Telegram 管理流程升级为现代化网页面板，集中管理客户、产品与订阅，自动发送续费提醒与续费确认邮件，支持更丰富的管理与运营场景。

## 功能概览
- **网页控制台**：统一管理客户、产品库、订阅与模板。
- **提醒规则可配置**：支持 30/7/1/0 等规则，也可自由设定阈值。
- **每日提醒策略**：订阅进入提醒窗口后，每天最多发送一次提醒。
- **续费确认邮件**：更新订阅到期日时可自动发送确认邮件。
- **即时扫描发送**：指定阈值并手动触发提醒。
- **数据持久化**：JSON 文件存储，部署轻量，零依赖。
- **基础认证**：HTTP Basic Auth 保护面板访问。

## 快速开始

### 1. 配置环境变量
复制 `.env.example` 为 `.env`，按需修改：

- `APP_ADDR`：服务监听地址（默认 `:8080`）
- `ADMIN_USER` / `ADMIN_PASS`：面板登录账号
- `TZ`：时区（默认 `Asia/Shanghai`）
- `DATABASE_PATH`：数据文件路径
- `SMTP_*`：邮件服务配置

### 2. Docker 启动
```bash
docker compose up -d --build
```

打开浏览器访问：`http://localhost:8080`，输入 `ADMIN_USER/ADMIN_PASS` 登录。

## 邮件模板变量说明
模板采用 Go Template 语法，可使用：

- `Customer`：`ID`, `Name`, `Email`
- `ProductDef`：`ID`, `Name`, `Content`, `ExpiresAt`
- `Subscription`：`ID`, `CustomerID`, `ProductID`, `ExpiresAt`, `Note`
- `Product`：等同于 `ProductDef`，但 `Content` 会优先取订阅备注
- `DaysBefore`, `DaysLeft`, `Now`, `Company`
- 续费确认模板额外提供：`OldExpiresAt`, `NewExpiresAt`

## 发送策略
- **定时扫描**：当订阅剩余天数 ≤ 提醒规则中的最大值时进入提醒窗口，每天最多发送一次。
- **停止条件**：剩余天数 < -1 时不再发送。
- **立即扫描**：支持手动输入阈值并即时发送。

## 本地运行（非 Docker）
```bash
go run ./cmd/server
```

## 目录结构
- `cmd/server`：入口程序
- `internal/web`：Web 面板与模板
- `internal/reminder`：提醒逻辑
- `internal/db`：JSON 存储与模型

---

如需继续扩展（多管理员、多渠道通知、Webhook、API 权限等），可以在此基础上按模块继续演进。
