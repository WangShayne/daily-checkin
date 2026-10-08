<div align="center">

# Daily Check-in · 多站点每日自动签到

**简体中文** | [English](README.en.md)

自托管的每日签到工具：Docker 一键部署，通过网页管理论坛、门户、需要登录的网站的每日签到。

![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Jinja-009688?logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

<img src="docs/screenshots/dashboard.png" alt="站点总览" width="860">

</div>

## 目录

- [功能特性](#功能特性)
- [界面截图](#界面截图)
- [快速开始](#快速开始)
- [签到方式](#签到方式)
- [浏览器录制指南](#浏览器录制指南)
- [配置与环境变量](#配置与环境变量)
- [故障排查](#故障排查)
- [项目结构](#项目结构)
- [开发与测试](#开发与测试)
- [安全须知](#安全须知)
- [License](#license)

## 功能特性

- **可视化管理**：站点总览、添加 / 编辑站点、运行日志、设置，全中文界面，支持深色模式和手机浏览器
- **三种签到方式**：仅访问（visit）、点击 / 提交（click）、录制回放（recorded），详见[签到方式](#签到方式)
- **嵌入式浏览器录制**：Playwright Chromium + noVNC 嵌入页面，手动登录并签到一次，之后自动回放
- **定时执行**：APScheduler，每天固定时间或 Cron 表达式，时区 Asia/Shanghai
- **结果一目了然**：状态徽章（签到成功 / 今日已签 / 失败 / 已跳过）、今日统计、下次运行时间、按状态 / 站点筛选日志
- **登录保护**：会话登录（`CHECKIN_USER` / `CHECKIN_PASSWORD`），未登录无法访问任何管理页面或 noVNC
- **数据只在本机**：站点、Cookie、录制会话都保存在 Docker 数据卷 `/data`（SQLite）
- **离线可用**：前端无构建步骤、不依赖 CDN，CSS / 图标 / noVNC 全部打包在镜像内
- **不使用 GitHub Actions**：在你自己的机器上运行，避免滥用 GitHub 的风险
- **可扩展**：Adapter 插件模式（`forum` / `portal` / `http_form` / `browser`），可自行添加

## 界面截图

| 站点总览 | 深色模式 |
|---|---|
| ![站点总览](docs/screenshots/dashboard.png) | ![深色模式](docs/screenshots/dashboard-dark.png) |
| **添加站点（签到方式说明）** | **运行日志** |
| ![添加站点](docs/screenshots/site-form.png) | ![运行日志](docs/screenshots/logs.png) |
| **浏览器录制** | **录制会话（noVNC 远程浏览器）** |
| ![浏览器录制](docs/screenshots/record.png) | ![录制会话](docs/screenshots/record-session.png) |
| **登录** | **设置** |
| ![登录](docs/screenshots/login.png) | ![设置](docs/screenshots/settings.png) |

<details>
<summary>更多截图：空状态、操作提示、手机端</summary>

| 空状态 | 操作提示（Toast） | 手机端 |
|---|---|---|
| ![空状态](docs/screenshots/dashboard-empty.png) | ![提示](docs/screenshots/run-toast.png) | <img src="docs/screenshots/mobile-dashboard.png" width="240"> |

</details>

> 截图由 [`docs/capture_screenshots.py`](docs/capture_screenshots.py) 用 Playwright 对运行中的 Docker 实例自动生成。

## 快速开始

需要 Docker 和 Docker Compose v2。

```bash
git clone https://github.com/WangShayne/daily-checkin.git
cd daily-checkin

# 1. 修改登录账号（强烈建议）
cp .env.example .env
#   编辑 .env：CHECKIN_USER / CHECKIN_PASSWORD / CHECKIN_SESSION_SECRET

# 2. 构建并启动（首次会下载 Chromium，稍慢）
docker compose up -d --build
```

浏览器打开 **http://localhost:4567**，在局域网其他设备上用 **http://<服务器 IP>:4567** 访问（例如 `http://192.168.1.10:4567`）。

| 项目 | 值 |
|---|---|
| 端口 | `4567`（Web UI + noVNC WebSocket，同一端口） |
| 默认账号 | `admin` / `changeme`（**对外暴露前务必修改**，未修改时页面会显示警告） |
| 数据卷 | `checkin-data` → 容器内 `/data`（SQLite：站点、日志、录制会话） |
| 示例站 | `http://localhost:5001`（`demo` / `demo`，用于自测，可删除） |

修改账号后重启生效：

```bash
CHECKIN_USER=me CHECKIN_PASSWORD='a-strong-password' \
CHECKIN_SESSION_SECRET="$(openssl rand -hex 32)" docker compose up -d
```

常用命令：

```bash
docker compose logs -f checkin        # 查看日志
docker compose up -d --build          # 更新代码后重建（数据保留）
docker compose down                   # 停止（数据卷保留）
```

**上手流程**：登录 → **添加站点** → 选类型和签到方式 → 设置每天时间 → 保存 → 点 **签到** 试跑 → 在 **运行日志** 查看结果。

> 不需要示例站时，可以删除 `docker-compose.yml` 里的 `test-site` 服务和 `depends_on`。

## 签到方式

| 方式 | 适用场景 | 工作原理 | 需要填写 |
|---|---|---|---|
| **仅访问** `visit` | 每天打开网站 / 某页面就算签到 | 对 `基础 URL + 路径` 发 GET 请求，2xx / 3xx 或命中成功关键字即成功 | URL、通常需要 Cookie |
| **点击 / 提交** `click` | 页面上有「签到」按钮，或有签到 API | 按站点类型发 POST（可改方法），提交表单或 JSON | URL、Cookie、可选表单 / JSON / 成功关键字 |
| **录制回放** `recorded` | 登录流程复杂（验证码、跳转、前端加密） | 在嵌入浏览器里录制一次；回放时加载保存的 Cookie / localStorage，重放最近的导航与最后一次 POST | 只需起始 URL，在[录制页](#浏览器录制指南)完成 |

结果判定：响应中含「已签到 / 已经签到 / already / 重复签到」记为 **今日已签**（幂等成功）；请求异常或状态码不在成功列表内记为 **失败**；站点停用时记为 **已跳过**。

站点类型（决定 click 时如何提交）：

| 类型 | 适用场景 |
|---|---|
| `forum` 论坛 | 论坛签到插件，默认表单字段 `operation=qiandao`（可在高级设置修改） |
| `portal` 门户 / API | JSON API 签到（`body`）或表单 |
| `http_form` 通用 HTTP 表单 | 任意表单字段 + Cookie |
| `browser` 浏览器录制 | 配合 `recorded` 方式使用 |

## 浏览器录制指南

容器内运行 **Xvfb + x11vnc + Playwright Chromium**，通过 **noVNC** 嵌入网页，你可以直接在页面中操作远程浏览器。

1. 左侧导航 **浏览器录制**，选择已有站点，或在「新建并录制」中填写名称和起始 URL（例如登录页）
2. 进入录制会话后，状态栏显示 **远程桌面：已连接 ✅**
3. 在远程浏览器中**手动登录**，并**完成一次签到**（点击签到按钮或打开签到页）
4. 点击右上角 **完成录制**，保存 Cookie / localStorage（`storage_state`）和导航 / POST 步骤到 `/data`
5. 站点自动切换为 **录制回放**，之后的定时任务和「立即签到」都会无头回放

提示：

- 只需放行 4567 端口；x11vnc 只监听容器内 `127.0.0.1:5900`，应用在 `/ws/vnc` 把 WebSocket 桥接到 VNC
- noVNC 的 WebSocket 地址由浏览器当前地址生成（同主机、同端口），用局域网 IP 访问无需额外配置
- 同一时间只能有一个录制会话；登录态过期后重新录制即可（覆盖旧录制）
- Cookie 失效周期因站点而异，如果回放开始失败，请先重新录制

## 配置与环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CHECKIN_USER` | `admin` | Web UI 登录用户名 |
| `CHECKIN_PASSWORD` | `changeme` | Web UI 登录密码，**务必修改** |
| `CHECKIN_SESSION_SECRET` | compose：`please-change-this-session-secret` | 会话 Cookie 签名密钥，请换成随机长字符串 |
| `CHECKIN_DATA_DIR` | `/data`（Docker），本地默认 `./data` | SQLite 数据目录 |
| `TZ` | `Asia/Shanghai` | 容器时区（定时任务固定按 Asia/Shanghai） |
| `CHECKIN_DISPLAY` | `:99` | 录制用 Xvfb 显示号 |
| `CHECKIN_VNC_PORT` | `5900` | 容器内 x11vnc 端口（仅 127.0.0.1） |
| `CHECKIN_VNC_READY_TIMEOUT` | `15` | 等待 x11vnc 就绪的秒数 |
| `CHECKIN_XVFB_PID_FILE` | `/tmp/checkin-xvfb.pid` | Xvfb PID 文件，用于清理残留进程 |
| `PLAYWRIGHT_BROWSERS_PATH` | `/ms-playwright` | Playwright 浏览器目录（镜像内置） |
| `TEST_SITE_URL` | `http://host.docker.internal:5001` | 仅 E2E 脚本使用 |
| `CHECKIN_CONFIG` | `config.yaml` | 仅 CLI 模式使用的 YAML 配置 |

网页 **设置** 页可修改：请求超时、站点间隔、失败后是否继续、Webhook 通知（Slack / Discord / 自定义）。

## 故障排查

**录制页「无法连接到服务器」/ 一直连接中**

链路：`Xvfb :99` → `x11vnc 127.0.0.1:5900` → 应用 `/ws/vnc`（4567 端口）→ 浏览器中的 noVNC。

1. 先更新并重建镜像：`git pull && docker compose up -d --build`
2. 展开录制页下方 **连接诊断**，或访问 `http://<服务器IP>:4567/api/record/diag`（需登录）：
   - `processes` 中 Xvfb / x11vnc 应为 `running: true`
   - `vnc_rfb_handshake: true` 表示 x11vnc 正常
   - `page_ws_url` 应为 `ws://<你访问的地址>:4567/ws/vnc`
3. 查看日志：`docker compose logs -f checkin | grep -E "x11vnc|VNC WebSocket|录制"`，正常会出现 `x11vnc 已就绪` 和 `VNC WebSocket 已连接`
4. 常见原因：
   - 浏览器缓存了旧 noVNC 设置：换无痕窗口或清除该站点 localStorage
   - 会话已结束 / 登录过期：WebSocket 以 4404 / 4401 关闭，回到录制页重新开始
   - HTTPS 反向代理：需转发 WebSocket（`Upgrade` / `Connection` 头），https 页面会自动使用 `wss://`

**其他问题**

| 现象 | 处理 |
|---|---|
| 页面样式错乱 | 强制刷新（Ctrl+F5）；样式文件带版本号，升级后会自动失效 |
| 签到失败「Name or service not known」 | 容器无法解析该域名，检查 DNS / 网络 |
| 一直「今日已签」 | 说明站点判定今天已签过，属于正常的幂等成功 |
| 录制回放失败 | Cookie 可能已过期，重新录制该站点 |
| Chromium 崩溃 | 确认 compose 中 `shm_size: 256mb` 未被删除 |
| 定时没有执行 | 确认站点已启用，在总览页查看「下次运行」时间；容器需保持运行 |

## 项目结构

```
daily-checkin/
├── Dockerfile               # python:3.12-slim + Chromium + Xvfb + x11vnc + noVNC
├── docker-compose.yml       # 端口 4567、数据卷、登录环境变量、示例站
├── .env.example             # 环境变量模板（复制为 .env，勿提交）
├── config.example.yaml      # CLI 模式示例配置
├── src/checkin/
│   ├── web/
│   │   ├── app.py           # FastAPI 路由、登录中间件、Flash 提示
│   │   ├── record_routes.py # 录制页面、/ws/vnc WebSocket 桥接、诊断接口
│   │   ├── templates/       # Jinja2 模板（base / 总览 / 表单 / 日志 / 设置 / 录制）
│   │   └── static/style.css # 设计系统（浅色 / 深色，无外部依赖）
│   ├── adapters/            # forum / portal / http_form / recorded
│   ├── browser/recorder.py  # Xvfb + x11vnc + Playwright 录制会话
│   ├── db.py                # SQLite 持久化
│   ├── scheduler.py         # APScheduler（Asia/Shanghai）
│   ├── core.py              # 签到编排
│   └── models.py            # 数据模型、签到方式 / 类型标签
├── examples/
│   ├── test-site/           # Flask 自测签到站（demo / demo）
│   ├── e2e_against_test_site.py
│   └── e2e_record_ui.py     # noVNC 录制 UI 端到端测试
├── docs/
│   ├── screenshots/         # README 截图
│   └── capture_screenshots.py
└── tests/                   # pytest 单元测试
```

## 开发与测试

本地运行（不使用 Docker，录制功能需要 Xvfb / x11vnc / noVNC）：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e ".[dev]"
playwright install chromium
export CHECKIN_DATA_DIR=./data CHECKIN_USER=admin CHECKIN_PASSWORD=changeme
uvicorn checkin.web.app:app --host 0.0.0.0 --port 4567 --reload
```

单元测试：

```bash
pytest -q
```

端到端测试（Docker 运行中）：

```bash
# visit / click / recorded 三种方式对示例站
docker exec -e TEST_SITE_URL=http://host.docker.internal:5001 \
  daily-checkin python /app/examples/e2e_against_test_site.py

# 录制 UI：登录 → noVNC 连接 → 远程浏览器登录签到 → 完成录制
# UI_URL 可换成局域网 IP，验证非 localhost 访问
docker run --rm --network host --shm-size 256m -v "$PWD/examples:/ex" \
  -e UI_URL=http://127.0.0.1:4567 -e TARGET_URL=http://host.docker.internal:5001 \
  daily-checkin-checkin python /ex/e2e_record_ui.py
```

重新生成截图见 [`docs/capture_screenshots.py`](docs/capture_screenshots.py) 顶部说明（建议使用全新数据卷的演示容器）。

前端说明：服务端渲染（FastAPI + Jinja2），没有 npm / 构建步骤；样式在 `static/style.css`，图标是 `_macros.html` 中的内联 SVG。

CLI 模式（YAML 配置，适合调试）：`cp config.example.yaml config.yaml && checkin -c config.yaml -v`

## 安全须知

- **修改默认密码**：`admin / changeme` 仅供首次试用；同时设置随机的 `CHECKIN_SESSION_SECRET`
- **不要直接暴露到公网**：建议只在局域网使用；如需外网访问，请放在 HTTPS 反向代理 / VPN 之后
- **Cookie 等同于账号**：站点 Cookie、录制的 `storage_state` 存在 `/data` 数据卷，切勿提交或分享数据卷 / `checkin.db`
- **切勿提交**：`.env`、`config.yaml`、数据库文件、导出的 Cookie（已在 `.gitignore` 中排除）
- **不使用 GitHub Actions**：本项目设计为自托管运行，不要把签到放到 CI 中
- noVNC 和 `/ws/vnc` 同样需要登录；x11vnc 只监听容器内回环地址

## 免责声明

本项目仅供个人自动化学习与自用。请遵守目标网站的服务条款和当地法律，不得用于未授权访问。仓库示例只使用占位 URL 和本地示例站。

## License

MIT
