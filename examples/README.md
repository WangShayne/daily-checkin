# 示例与自测

## test-site（模拟签到网站）

Flask 小站，账号 **demo / demo**：

| 路径 | 说明 |
|------|------|
| `/login` | 登录 |
| `/dashboard` | 登录后控制台，「签到」按钮 POST `/checkin` |
| `/checkin` | 点击签到（需登录 Cookie） |
| `/visit` | 仅访问即记签到（需登录 Cookie） |

与主应用同一 `docker compose` 启动后映射 **http://localhost:5001**。

> 部分环境容器互访（ICC）不稳定时，请在 checkin 容器内使用  
> `http://host.docker.internal:5001`（compose 已配置 `extra_hosts`）。

## E2E 脚本

在已启动的 stack 上验证 visit / click / recorded：

```bash
docker compose up -d --build
docker exec -e TEST_SITE_URL=http://host.docker.internal:5001 \
  daily-checkin python examples/e2e_against_test_site.py
```

录制模式由 Playwright API 自动登录并签到（不依赖 noVNC 人工操作），再回放 `RecordedAdapter`。
