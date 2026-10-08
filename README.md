# Daily Check-in — 多站点每日自动签到

忘记签到？用 Docker 一键部署本工具，通过**可视化网页**管理多个网站的每日签到。支持门户、论坛等类型，以及两种签到方式：

| 方式 | 说明 |
|------|------|
| **仅访问（visit）** | 每天打开 / GET 目标 URL 即算完成，适合「进站就算签到」 |
| **点击/提交（click）** | 需要 POST 表单、点签到按钮或调用签到 API |
| **录制回放（recorded）** | 嵌入浏览器登录并录制一次，之后自动回放 |

内置可插拔 Adapter：`forum` / `portal` / `http_form`。容器内用 APScheduler 按 Asia/Shanghai 定时执行，配置与日志持久化到 Docker Volume（SQLite）。

> **请勿**把真实 Cookie / Token 提交到 Git。凭证只保存在容器数据卷或本机环境变量中。

## 推荐：Docker 部署

### 1. 启动

```bash
git clone https://github.com/WangShayne/daily-checkin.git
cd daily-checkin
docker compose up -d --build
```

浏览器打开：**http://localhost:4567**，先登录管理界面。

数据目录挂载为命名卷 `checkin-data`（容器内 `/data`），重启不丢站点与日志。

### 2. 登录账号（务必修改）

Web UI **必须登录**后才能管理站点。通过环境变量配置：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CHECKIN_USER` | `admin` | 登录用户名 |
| `CHECKIN_PASSWORD` | `changeme` | 登录密码 |
| `CHECKIN_SESSION_SECRET` | （示例占位） | 会话 Cookie 签名密钥，生产环境请换成随机长字符串 |

```bash
# 方式 A：项目根目录 .env（docker compose 会读取）
cp .env.example .env
# 编辑 CHECKIN_USER / CHECKIN_PASSWORD / CHECKIN_SESSION_SECRET

# 方式 B：启动时传入
CHECKIN_USER=myuser CHECKIN_PASSWORD='strong-pass' docker compose up -d --build
```

默认 `admin` / `changeme` 仅方便初次试用；**对公网或局域网暴露前请改掉**。右上角可「退出」登出。

### 3. 常用命令

```bash
# 查看日志
docker compose logs -f checkin

# 停止
docker compose down

# 保留数据重新构建
docker compose up -d --build
```

### 4. 在网页里做什么

1. 打开首页 → **添加站点**
2. 填写名称、基础 URL、路径
3. 选择 **站点类型**（论坛 / 门户 / 通用表单）和 **签到方式**（仅访问 / 点击提交）
4. 需要登录态时填写 Cookies / Headers
5. 设置计划：每天固定时间（如 `09:00`）或 Cron（如 `0 9 * * *`），时区 **Asia/Shanghai**
6. 保存后可点 **签到** 立即试跑，在 **日志** 页查看结果



## 自测示例站

仓库内带有 `examples/test-site`（账号 `demo` / `demo`），`docker compose up` 会一并启动在 **:5001**。

```bash
docker compose up -d --build
# 管理端 http://localhost:4567  admin / changeme
# 示例站 http://localhost:5001  demo / demo

docker exec -e TEST_SITE_URL=http://host.docker.internal:5001 \
  daily-checkin python examples/e2e_against_test_site.py
```

详细说明见 [examples/README.md](examples/README.md)。

## 浏览器录制（推荐用于复杂登录）

容器内使用 **Playwright Chromium + Xvfb + x11vnc + noVNC**：

1. 登录管理界面 → 顶部 **录制**
2. 选择站点或快速新建（类型会变为「浏览器录制」）
3. 点击开始后，页面嵌入远程桌面；在画面中**手动登录并完成签到**
4. 点击 **完成录制**：Cookie、localStorage（storage_state）以及导航 / POST 步骤写入 `/data` 卷中的 SQLite
5. 站点模式自动设为 `recorded`；定时任务与「立即签到」会无头回放该流程

架构要点：

- Web UI（4567）需登录；noVNC 静态资源同样走已登录会话
- x11vnc 仅监听容器内 `127.0.0.1:5900`；应用自身在 `/ws/vnc` 把 WebSocket 桥接到 VNC（相当于内置 websockify），不额外暴露端口
- noVNC 的 WebSocket 地址由浏览器当前地址生成（同主机、同端口 4567），局域网 IP 访问无需额外配置
- 回放使用 Playwright headless + 保存的 `storage_state`，并重放末几步导航与最后一次 POST

> 首次构建镜像会下载 Chromium，体积较大。需要 `shm_size`（compose 已设 256mb）。


## 故障排查：录制页「无法连接到服务器」

链路：`Xvfb :99` → `x11vnc 127.0.0.1:5900` → 应用 `/ws/vnc`（同 4567 端口）→ 浏览器 noVNC iframe。

1. **先更新并重建镜像**（v0.3.1 修复了 `/ws/vnc` 桥接与 noVNC 路径问题）：
   ```bash
   git pull
   docker compose up -d --build
   ```
2. 录制页连不上时，展开页面下方 **「连接诊断」**，或直接访问 `http://<服务器IP>:4567/api/record/diag`（需登录）：
   - `processes` 中 Xvfb / x11vnc 应为 `running: true`
   - `vnc_rfb_handshake: true` 表示 x11vnc 正常
   - `page_ws_url` 应是 `ws://<你访问的IP>:4567/ws/vnc`
3. 看容器日志：
   ```bash
   docker compose logs -f checkin | grep -E "x11vnc|VNC WebSocket|录制"
   ```
   正常会看到 `x11vnc 已就绪` 和 `VNC WebSocket 已连接`。
4. 常见原因：
   - **浏览器缓存了旧的 noVNC 设置**：新版会显式传入当前 host/port；仍有问题可清除该站点的 localStorage 或换无痕窗口
   - **会话已结束 / 未登录**：WebSocket 会以 4404 / 4401 关闭，回到录制页重新开始
   - **HTTPS 反向代理**：需转发 WebSocket（`Upgrade` / `Connection` 头），页面为 https 时自动使用 `wss://`
   - 直接用局域网 IP（如 `http://192.168.x.x:4567`）访问无需任何额外设置；只需放行 4567 端口

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
| `browser` | 浏览器录制回放（配合 mode=recorded） |

也可自行扩展 Adapter：继承 `Adapter`，实现 `do_click()`（`do_visit()` 已在基类提供），注册到 `ADAPTER_REGISTRY`。

## 密钥与安全

| 位置 | 用途 |
|------|------|
| `CHECKIN_USER` / `CHECKIN_PASSWORD` | Web UI 登录（默认 admin / changeme，务必修改） |
| `CHECKIN_SESSION_SECRET` | 会话签名密钥 |
| 网页表单中的 Cookies / Headers | 写入 SQLite（Volume），勿把卷内容提交到公开仓库 |
| `.env`（本地 CLI，已 gitignore） | 登录账号与 YAML `${VAR}` 占位 |
| Webhook URL（设置页） | 可选，签到汇总推送 |

**切勿**提交：`.env`、含真实 Cookie 的配置、导出的 cookie 文件、数据库文件。Cookie 等同登录凭证，泄露等于账号被接管。

## 本地开发（可选，不用 Docker）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
export CHECKIN_DATA_DIR=./data
export CHECKIN_USER=admin
export CHECKIN_PASSWORD=changeme
uvicorn checkin.web.app:app --host 0.0.0.0 --port 4567 --reload
# 浏览器 http://localhost:4567
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
├── docker-compose.yml   # 端口 4567、登录环境变量、shm_size
├── README.md
├── config.example.yaml
├── src/checkin/
│   ├── web/           # FastAPI + Jinja 可视化界面
│   ├── adapters/      # forum / portal / http_form / recorded
│   ├── browser/       # Playwright + noVNC 录制会话
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
