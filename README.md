# Daily Check-in — 多站点每日自动签到

忘记签到？用这个 Python 小工具，按计划（本机 cron 或 GitHub Actions）自动完成多个网站的每日签到。支持**门户、论坛**等不同类型，通过可插拔 **Adapter** 扩展。

> 请勿把真实 Cookie / Token 提交到 Git。凭证只放在 `.env` 或 GitHub Secrets。

## 功能概览

- 配置驱动：在 `config.yaml` 里声明站点即可
- 内置适配器：`forum` / `portal` / `http_form`
- 可扩展：实现 `Adapter.check_in()` 并注册到 `adapters` 即可
- 每日幂等：识别「已签到」视为成功，适合重复调度
- 可选 Webhook 汇总通知（Slack / Discord / 自定义）
- 清晰的按站点成功 / 失败日志

## 快速开始（本地）

### 1. 环境要求

- Python **3.11+**
- 建议使用虚拟环境

```bash
cd daily-checkin
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
```

### 2. 配置

```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

编辑 `.env`，填入各站点 Cookie / Token（对应配置里的 `${VAR}`）：

```env
FORUM_DEMO_COOKIE=session_id=你的会话
PORTAL_DEMO_TOKEN=你的token
```

编辑 `config.yaml`：把 `enabled: true` 的站点改成你的 URL、路径、关键字等。**示例里的域名都是占位符**，不会访问真实目标站点。

### 3. 运行

```bash
python -m checkin --help
python -m checkin --config config.yaml
# 或安装后的入口：
checkin -c config.yaml -v
```

退出码：有任一站点 `failed` 时为 `1`，便于 cron / CI 告警。

### 4. 本机定时（cron 示例）

每天早上 9:00（Asia/Shanghai）：

```cron
0 9 * * * cd /path/to/daily-checkin && . .venv/bin/activate && checkin -c config.yaml >> logs/checkin.log 2>&1
```

## 如何添加一个站点

### 方式 A：只用配置（推荐先试）

在 `config.yaml` 的 `sites` 下增加一项，选择已有 `type`：

| type        | 适用场景                         |
|-------------|----------------------------------|
| `forum`     | 论坛插件签到、需 Cookie 的 POST  |
| `portal`    | 门户 / JSON API 签到             |
| `http_form` | 通用表单字段 + Cookie            |

关键字段：

- `base_url` + `checkin_path`：拼出签到 URL
- `method`：默认 `POST`
- `cookies` / `headers`：登录态（用 `${ENV}` 引用密钥）
- `form_data` / `body`：表单或 JSON
- `success_keywords`：响应里出现即视为成功
- `success_status`：允许的 HTTP 状态码（默认 `[200]`）

响应中若包含「已签到 / already」等字样，会记为 `already`（幂等成功）。

### 方式 B：自定义 Adapter

1. 在 `src/checkin/adapters/` 新建模块，继承 `Adapter`，实现 `check_in(site, *, timeout)`。
2. 在 `adapters/__init__.py` 的 `ADAPTER_REGISTRY` 注册，例如 `"my_site": MySiteAdapter`。
3. 配置里写 `type: my_site`。

```python
from checkin.adapters.base import Adapter
from checkin.models import CheckInResult, CheckInStatus, SiteConfig

class MySiteAdapter(Adapter):
    def check_in(self, site: SiteConfig, *, timeout: int = 30) -> CheckInResult:
        # 使用 self.request / self.evaluate_response 等辅助方法
        ...
```

## 密钥与安全

| 位置 | 用途 |
|------|------|
| `.env`（本地，已 gitignore） | 开发时注入 `${VAR}` |
| GitHub Secrets | Actions 运行时注入 |
| `config.yaml` | 只放结构与非敏感参数；可整份放进 Secret `CHECKIN_CONFIG_YAML` |

**切勿**提交：`.env`、含真实 Cookie 的 `config.yaml`、导出的 cookie 文件。

抓包 / 浏览器里复制 Cookie 时注意：Cookie 等同登录凭证，泄露等于账号被接管。定期轮换，且本工具仅应在你自己的账号、你授权的站点上使用。

## GitHub Actions 每日运行

仓库已包含 `.github/workflows/daily-checkin.yml`：

- **定时**：`0 1 * * *` UTC（约北京时间上午 9 点）
- **手动**：Actions 页 `workflow_dispatch`

建议在仓库 Settings → Secrets 中配置：

1. 各站点变量（与 `.env.example` 同名），例如 `FORUM_DEMO_COOKIE`
2. 可选 `NOTIFY_WEBHOOK_URL`
3. 可选 `CHECKIN_CONFIG_YAML`：完整 `config.yaml` 内容（避免把私人 URL 结构公开在默认分支）

若未设置 `CHECKIN_CONFIG_YAML`，workflow 会复制 `config.example.yaml`（示例站点多为占位，真实签到请自备配置）。

## 项目结构

```
daily-checkin/
├── README.md
├── pyproject.toml
├── requirements.txt
├── config.example.yaml
├── .env.example
├── .gitignore
├── .github/workflows/daily-checkin.yml
├── src/checkin/
│   ├── __main__.py      # python -m checkin
│   ├── cli.py           # Click CLI
│   ├── core.py          # 编排：加载配置 → 跑适配器 → 汇总
│   ├── config.py        # YAML/JSON + ${ENV} 解析
│   ├── models.py        # SiteConfig / CheckInResult
│   ├── notifier.py      # Webhook 通知（邮件为预留）
│   └── adapters/
│       ├── base.py
│       ├── forum.py
│       ├── portal.py
│       └── http_form.py
└── tests/
```

## 开发与测试

```bash
pip install -e ".[dev]"
pytest -q
```

## 免责声明

本项目仅为个人自动化学习与自用脚手架。请遵守目标网站服务条款与当地法律；不得用于未授权访问或绕过安全机制。示例适配器使用占位 URL，不包含任何真实站点的登录破解流程。

## License

MIT
