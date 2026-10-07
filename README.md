# Daily Check-in — 多站点每日自动签到

忘记签到？用 Docker 一键部署本工具，通过**可视化网页**管理多个网站的每日签到。支持门户、论坛等类型，以及两种签到方式：

| 方式 | 说明 |
|------|------|
| **仅访问（visit）** | 每天打开 / GET 目标 URL 即算完成，适合「进站就算签到」 |
| **点击/提交（click）** | 需要 POST 表单、点签到按钮或调用签到 API |

内置可插拔 Adapter：`forum` / `portal` / `http_form`。容器内用 APScheduler 按 Asia/Shanghai 定时执行，配置与日志持久化到 Docker Volume（SQLite）。

> **请勿**把真实 Cookie / Token 提交到 Git。凭证只保存在容器数据卷或本机环境变量中。

## 推荐：Docker 部署

### 1. 启动

```bash
git clone https://github.com/WangShayne/daily-checkin.git
cd daily-checkin
docker compose up -d --build
```

浏览器打开：**http://localhost:8080**

数据目录挂载为命名卷 `checkin-data`（容器内 `/data`），重启不丢站点与日志。

### 2. 常用命令

```bash
# 查看日志
docker compose logs -f checkin

# 停止
docker compose down

# 保留数据重新构建
docker compose up -d --build
```

### 3. 在网页里做什么

1. 打开首页 → **添加站点**
2. 填写名称、基础 URL、路径
3. 选择 **站点类型**（论坛 / 门户 / 通用表单）和 **签到方式**（仅访问 / 点击提交）
4. 需要登录态时填写 Cookies / Headers
5. 设置计划：每天固定时间（如 `09:00`）或 Cron（如 `0 9 * * *`），时区 **Asia/Shanghai**
6. 保存后可点 **签到** 立即试跑，在 **日志** 页查看结果

## 签到方式详解

### 仅访问（visit）

适合：每天进入网站首页或某个页面就算签到成功。

- 工具会对 `base_url + checkin_path` 发 **GET** 请求
- 默认接受常见 2xx / 3xx；也可配置成功关键字
- 一般仍需带上 Cookie（若站点要求登录后访问）

示例：路径留空或填 `/`，方式选「仅访问」。

### 点击/提交（click）

适合：页面上有「签到」按钮，或存在签到 API。

- 按站点类型发送 POST（或你指定的方法）
- `forum`：默认表单字段 `operation=qiandao`（可改）
- `portal`：可发 JSON `body` 或表单
- `http_form`：提交 `form_data`

响应中含「已签到 / already」等字样会记为 `already`（幂等成功）。

## 站点类型

| type | 适用场景 |
|------|----------|
| `forum` | 论坛插件签到、需 Cookie 的 POST |
| `portal` | 门户 / JSON API 签到 |
| `http_form` | 通用表单字段 + Cookie |

也可自行扩展 Adapter：继承 `Adapter`，实现 `do_click()`（`do_visit()` 已在基类提供），注册到 `ADAPTER_REGISTRY`。

## 密钥与安全

| 位置 | 用途 |
|------|------|
| 网页表单中的 Cookies / Headers | 写入 SQLite（Volume），勿把卷内容提交到公开仓库 |
| `.env`（本地 CLI，已 gitignore） | YAML 配置里的 `${VAR}` 占位 |
| Webhook URL（设置页） | 可选，签到汇总推送 |

**切勿**提交：`.env`、含真实 Cookie 的配置、导出的 cookie 文件、数据库文件。Cookie 等同登录凭证，泄露等于账号被接管。

## 本地开发（可选，不用 Docker）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
export CHECKIN_DATA_DIR=./data
uvicorn checkin.web.app:app --host 0.0.0.0 --port 8080 --reload
```

CLI（YAML 配置，适合脚本调试）：

```bash
cp config.example.yaml config.yaml
cp .env.example .env
checkin -c config.yaml -v
```

测试：

```bash
pip install -e ".[dev]"
pytest -q
```

## 项目结构

```
daily-checkin/
├── Dockerfile
├── docker-compose.yml
├── README.md
├── config.example.yaml
├── src/checkin/
│   ├── web/           # FastAPI + Jinja 可视化界面
│   ├── adapters/      # forum / portal / http_form
│   ├── db.py          # SQLite 持久化
│   ├── scheduler.py   # APScheduler（Asia/Shanghai）
│   ├── core.py        # 签到编排
│   └── models.py      # visit / click 等模型
└── tests/
```

## 免责声明

本项目仅为个人自动化学习与自用工具。请遵守目标网站服务条款与当地法律；不得用于未授权访问。示例使用占位 URL，不包含任何真实站点的登录破解流程。

## License

MIT
