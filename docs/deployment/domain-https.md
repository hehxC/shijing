# 域名、HTTPS 与反向代理

本文说明如何把石境暴露到公网：申请域名、启用 HTTPS，并用反向代理把流量转发给应用。

## 前置条件

- 一个域名，DNS `A` 记录指向服务器公网 IP（如 `shijing.example.com`）；
- 一台公网服务器，防火墙放行 80 与 443 端口；
- 服务器在大陆的，域名通常需完成 ICP 备案。

## 方案选择

| 方案 | 证书 | 适用 |
| --- | --- | --- |
| **A. Caddy**（推荐） | 自动申请/续期 Let's Encrypt | 想要最少配置、开箱即用 |
| B. Nginx + certbot | certbot 手动/自动申请 | 已有 Nginx 体系或需要精细控制 |

两套反向代理配置都已提供：`deploy/Caddyfile` 与 `deploy/nginx.conf`。

---

## 方案 A：Caddy（docker-compose 内置）

1. 在 `.env` 里填好 `AUTH_SECRET_KEY`、`DEEPSEEK_API_KEY` 等，并设置域名：

   ```dotenv
   DOMAIN=shijing.example.com
   ```

2. 启动整套编排：

   ```bash
   docker compose up -d --build
   ```

3. Caddy 会自动通过 `HTTP-01` 挑战申请证书、监听 443、并把 80 重定向到 HTTPS。
   首次申请需要 DNS 已生效且 80 端口可达；之后就自动续期。

本地无域名联调时，`DOMAIN` 留空，Caddy 只监听 HTTP（`http://localhost`）。

---

## 方案 B：Nginx + certbot

1. 用 `deploy/nginx.conf`，先只保留 80 段的 webroot 与跳转（暂不开 443 段），启动 Nginx。
2. 申请证书：

   ```bash
   certbot certonly --webroot -w /var/www/certbot -d shijing.example.com
   ```

3. 证书生成后，解开 `deploy/nginx.conf` 的 443 段并 `nginx -s reload`。
   或直接用 `certbot --nginx` 让 certbot 自动改写。

---

## 反向代理关键点（针对本应用）

这些是本应用区别于普通静态站的地方，反向代理必须正确处理，否则功能异常。

### 1. SSE 流式不能缓冲

`/chat` 用 SSE 流式返回 AI 回复。若反向代理开缓冲，用户会等到整个回答生成完才一次性看到。

- Caddy：`reverse_proxy` 默认流式转发，无需配置；
- Nginx：必须 `proxy_buffering off` + `proxy_set_header X-Accel-Buffering no`（已在 `deploy/nginx.conf` 写好）。

### 2. 长超时

效果图生成最长约 240 秒。反向代理读超时要给足余量（Nginx 已设 `proxy_read_timeout 300s`），
否则生成到一半连接被代理掐断。

### 3. X-Request-ID 透传

应用会透传客户端的 `X-Request-ID`（缺失则生成）。反向代理应原样转发该头，
这样浏览器看到的请求 ID 能贯穿「代理 → 应用日志 → 观测表」。

### 4. 真实客户端 IP（限流依赖）

登录/注册按客户端 IP 限流，应用读取的是直连地址。反向代理后面必须：

- 代理设置 `X-Forwarded-For`（Caddy 自动；Nginx 已写 `proxy_set_header`）；
- uvicorn 信任该头：启动参数加 `--proxy-headers --forwarded-allow-ips=*`。
  docker-compose 里的 `app` 服务已带这两个参数（应用不对外暴露端口，仅 Caddy 可访问，
  因此 `*` 是安全的）。

否则所有用户都会被识别成代理的 IP，登录/注册限流会共享同一额度。

### 5. 健康检查接线

负载均衡器/编排平台可把流量路由条件接到：

- `/health/live`：进程存活；
- `/health/ready`：数据库可用（不可用时返回 503，实例应被摘除流量）。

---

## 验证清单

部署完成后逐项确认：

- [ ] `https://你的域名/` 能打开工作台，浏览器锁图标正常；
- [ ] `https://你的域名/health/live` 返回 200、`/health/ready` 返回 200（数据库就绪时）；
- [ ] `/chat` 的回答是**流式**逐步出现，而非一次性出现；
- [ ] 连续发消息不出现「429」（转发头/限流主体正确）；
- [ ] 上传图片后刷新页面，图片仍能正常加载（对象存储持久化生效）；
- [ ] 重启容器（`docker compose restart app`）后历史会话与图片不丢失；
- [ ] 日志与响应头中的 `X-Request-ID` 一致，且日志无密钥、无完整图片 Data URL。
