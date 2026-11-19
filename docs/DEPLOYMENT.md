# 云端部署指南

本文档用于把 `grid_monitor` 部署到 Oracle Cloud Free Tier 或普通 VPS。适用于 Ubuntu 22.04 / Ubuntu 24.04，Oracle Cloud ARM 实例和普通 x86 VPS 都可以使用同一套流程。

部署文档只说明服务器准备和运行方式，不包含任何真实 token、webhook 或密钥。

## 1. 部署前原则

- 不要在公网直接开放 Flask 开发端口 `5001`。
- 不要在公网直接开放 Gunicorn 端口 `8000`。
- 只开放 `22`、`80`、`443`。
- Dashboard 必须启用登录保护，并设置强密码。
- 生产环境目前必须使用 `gunicorn --workers 1`。
- 如果 iFind 在海外云访问不稳定，先只启用少数合约观察 1-2 个交易时段。

`workers=1` 很重要：当前项目有后台自动监控 worker。如果 Gunicorn workers 大于 1，多个进程可能重复启动 worker，导致重复请求 iFind、重复创建报警或重复发送飞书通知。

## 部署前本地自检

正式连接 Oracle Cloud / VPS 之前，可以先在本地仓库运行部署前自检：

```bash
chmod +x scripts/production_smoke_check.sh
./scripts/production_smoke_check.sh
```

这个脚本只做文件和示例配置检查，例如确认 `requirements.txt` 包含 Gunicorn、systemd 示例使用 `workers=1`、Caddy 示例转发到 `127.0.0.1:8000`、备份脚本和部署文档存在。

它不会连接云服务器，不会读取真实 `.env`，不会启动长期服务，不会请求 iFind，也不会发送飞书。自检通过后，再继续进行 Oracle Cloud 或普通 VPS 部署。

## 本地生产演练

上云前可以先在本地 Mac 用 Gunicorn 模拟生产启动方式。第一版云端策略仍然是 Web + Worker 合并运行，所以本地和云端都必须保持 `workers=1`，避免后台自动监控 worker 被多个进程重复启动。

先运行本地生产演练检查：

```bash
chmod +x scripts/local_production_rehearsal.sh
./scripts/local_production_rehearsal.sh
```

脚本会先执行：

```bash
python -m compileall app main.py tests
python -m pytest -q
```

然后检查 Gunicorn、`.env`、`FLASK_SECRET_KEY`、`DASHBOARD_AUTH_ENABLED`、`MARKET_DATA_MODE` 和 `NOTIFICATION_MODE` 是否具备生产演练条件。脚本不会打印 token、webhook 或 secret。

安全演练模式不会请求真实 iFind，也不会真实发送飞书：

```bash
MARKET_DATA_MODE=manual_only NOTIFICATION_MODE=dry_run gunicorn -c gunicorn.conf.py main:app
```

真实演练模式会请求 iFind，并且在触发真实报警或执行测试通知时可能发送飞书：

```bash
MARKET_DATA_MODE=ifind NOTIFICATION_MODE=feishu gunicorn -c gunicorn.conf.py main:app
```

浏览器访问：

```text
http://127.0.0.1:8000
```

停止方式：

```text
Ctrl+C
```

注意事项：

- `.env` 不可提交。
- 安全提醒：.env 不可提交到 Git。
- `FLASK_SECRET_KEY` 在生产环境必须固定且足够随机。
- 真实演练模式会请求 iFind。
- 真实演练模式可能发送飞书通知。
- 第一版云端使用 `workers=1`，避免 worker 重复启动。

## 阶段 41.3：真实模式本地生产演练

这一节用于正式上云前，在本地 Mac 上用 Gunicorn 验证 iFind + 飞书真实链路。它仍然沿用第一版云端策略：Web + Worker 合并、单服务运行、`workers=1`。

启动命令：

```bash
MARKET_DATA_MODE=ifind NOTIFICATION_MODE=feishu gunicorn -c gunicorn.conf.py main:app
```

访问地址：

```text
http://127.0.0.1:8000
```

健康检查：

```text
http://127.0.0.1:8000/healthz
```

建议演练时长：10-30 分钟。

演练前建议：

1. 只启用 1-2 个合约。
2. 确认飞书群机器人可用。
3. 确认 iFind token / refresh token 可用。
4. 确认 `DASHBOARD_AUTH_ENABLED=true`。
5. 确认 `FLASK_SECRET_KEY` 固定。
6. 确认 Gunicorn `workers=1`。

观察清单：

1. Dashboard 能打开。
2. 登录保护正常。
3. 每合约行情状态正常。
4. iFind 能返回真实价格。
5. 飞书能收到通知。
6. 没有重复报警刷屏。
7. AlertLog 正常写入。
8. `/healthz` 正常。
9. 终端日志没有持续报错。

风险提醒：

- 真实模式会请求 iFind。
- 真实模式可能发送飞书通知。
- 不要把 `.env` 提交到 Git。
- 不要公开 token、webhook、secret。
- 如果出现重复通知，应立即 `Ctrl+C` 停止服务。

Gunicorn 单服务模式排查：

- 第一版 Web + Worker 合并部署时，Gunicorn 必须保持 `workers=1`。
- 使用 `gunicorn -c gunicorn.conf.py main:app` 启动后，终端日志中应能看到 `Auto monitor worker started.`。
- 自动监控运行时，终端日志中应能看到 `Monitor tick started` 和 `Monitor tick finished`。
- 如果 Dashboard 的“最近行情轮询”长期停留在“等待轮询”，且成功 / 失败计数一直为 0，应优先检查 Gunicorn 日志中是否出现 worker started 和 tick 日志。
- “立即刷新”只刷新 Dashboard 页面，不会主动触发真实行情轮询；真实轮询由后台 worker 按轮询间隔执行。

## 阿里云香港 Ubuntu 22.04 ECS 部署

这一节用于阿里云香港 Ubuntu 22.04 ECS。部署路径固定为 `/opt/grid_monitor`，虚拟环境为 `/opt/grid_monitor/.venv`，第一版仍然采用 Web + Worker 合并、Gunicorn `workers=1`、单服务运行。

安装依赖：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git sqlite3 curl caddy
```

准备代码目录：

```bash
sudo mkdir -p /opt/grid_monitor
sudo chown "$USER":"$USER" /opt/grid_monitor
cd /opt/grid_monitor
```

把项目代码 clone 或上传到 `/opt/grid_monitor` 后，创建虚拟环境并安装依赖：

```bash
cd /opt/grid_monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

创建生产 `.env`：

```bash
cd /opt/grid_monitor
cp .env.example .env
chmod 600 .env
nano .env
```

`.env` 只保存在服务器本地，不要提交到 Git，不要截图或外发 token、webhook、secret。至少确认：

```text
FLASK_ENV=production
FLASK_DEBUG=0
FLASK_SECRET_KEY=请换成固定强随机字符串
DASHBOARD_AUTH_ENABLED=true
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=请换成强密码
MARKET_DATA_MODE=ifind
NOTIFICATION_MODE=feishu
```

Gunicorn 配置由仓库中的 `gunicorn.conf.py` 提供，必须保持：

```text
workers=1
bind=127.0.0.1:8000
```

systemd service：

```bash
sudo cp /opt/grid_monitor/deploy/systemd/grid-monitor.service /etc/systemd/system/grid-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable grid-monitor
sudo systemctl start grid-monitor
sudo systemctl status grid-monitor --no-pager
```

`grid-monitor.service` 会从 `/opt/grid_monitor/.env` 读取环境变量，并执行：

```text
EnvironmentFile=/opt/grid_monitor/.env
/opt/grid_monitor/.venv/bin/gunicorn -c gunicorn.conf.py main:app
```

Caddy 反向代理到本机 Gunicorn：

```bash
sudo cp /opt/grid_monitor/deploy/caddy/Caddyfile.aliyun-http.example /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo systemctl status caddy --no-pager
```

该 Caddy 示例对外监听 `80`，并反代到 `127.0.0.1:8000`。不要把 `8000` 暴露到公网。

阿里云安全组建议：

- 放行 `22/tcp`，用于 SSH。
- 放行 `80/tcp`，用于 Caddy HTTP 访问。
- 不要放行 `5001/tcp`。
- 不要放行 `8000/tcp`。

部署后检查命令：

```bash
sudo systemctl status grid-monitor --no-pager
sudo journalctl -u grid-monitor -n 100 --no-pager
curl -f http://127.0.0.1:8000/healthz
curl -f http://127.0.0.1/healthz
```

也可以运行仓库内检查脚本：

```bash
cd /opt/grid_monitor
./scripts/check_deployment_status.sh
```

如果健康检查失败：

1. 先看 `sudo journalctl -u grid-monitor -n 100 --no-pager`。
2. 确认 `.env` 存在且权限为本地可读。
3. 确认 `FLASK_SECRET_KEY` 已设置。
4. 确认 `gunicorn.conf.py` 仍为 `workers=1` 和 `127.0.0.1:8000`。
5. 确认日志中出现 `Auto monitor worker started.`。

## 2. 安装系统依赖

在 Ubuntu 22.04 / 24.04 上：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git sqlite3 curl
```

如果使用 Caddy 做 HTTPS 反向代理，可按 Caddy 官方方式安装。也可以先使用系统包：

```bash
sudo apt install -y caddy
```

Oracle Cloud ARM 和普通 x86 VPS 都不需要项目层面的特殊处理。只要 Python、SQLite、Gunicorn 和 Caddy 可用即可。

## 3. 获取代码

方式一：在服务器上 clone 仓库：

```bash
sudo mkdir -p /opt/grid_monitor
sudo chown "$USER":"$USER" /opt/grid_monitor
git clone <your-repo-url> /opt/grid_monitor
cd /opt/grid_monitor
```

方式二：本地打包后上传到 `/opt/grid_monitor`。无论哪种方式，都不要上传本地 `.env` 中的真实密钥到公开仓库。

## 4. 创建虚拟环境并安装依赖

```bash
cd /opt/grid_monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

确认 `requirements.txt` 中包含 `gunicorn`。

## 5. 创建生产 .env

复制示例文件：

```bash
cd /opt/grid_monitor
cp .env.example .env
chmod 600 .env
```

编辑 `.env`：

```bash
nano .env
```

至少确认这些配置：

```text
FLASK_ENV=production
FLASK_DEBUG=0
FLASK_SECRET_KEY=请换成强随机字符串
DASHBOARD_AUTH_ENABLED=true
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=请换成强密码
MARKET_DATA_MODE=ifind
NOTIFICATION_MODE=feishu
IFIND_CLIENT_ID=
IFIND_CLIENT_SECRET=
IFIND_REFRESH_TOKEN=
FEISHU_WEBHOOK_URL=
IFIND_BATCH_SIZE=5
```

如果你在终端里执行 `source .env` 后，`os.getenv("IFIND_REFRESH_TOKEN")` 有值，但 Flask 中 `app.config.get("IFIND_REFRESH_TOKEN")` 仍然为空，说明 Flask 配置映射没有正确把环境变量同步进 `app.config`。真实模式启动前，应确认 `IFIND_REFRESH_TOKEN` 能进入 Flask `app.config`，这样 access token 缺失时系统才可以用 refresh token 继续刷新授权。

云端部署后，如果 iFind refresh token 需要更新，可以登录 Dashboard，在“运维设置 / iFind 授权设置”中提交新的 refresh token、账号到期日和权限更新日。页面只显示“已配置 / 未配置”，不会回显 token 原文；保存前会备份服务器当前 `.env`，保存后请点击“测试 iFind 行情”确认授权。不要截图 token，不要把 `.env` 提交到 Git。

安全提醒：

- 不要使用示例里的 `change-me`。
- 不要把 `.env` 提交到 Git。
- 不要截图或外发 iFind token、飞书 webhook、Dashboard 密码或 `FLASK_SECRET_KEY`。

## 6. 初始化数据库

首次部署时：

```bash
cd /opt/grid_monitor
source .venv/bin/activate
flask --app main init-db
```

如果你是从本地迁移已有 SQLite 数据库，可以把数据库文件放到 `/opt/grid_monitor/instance/grid_monitor.sqlite3`，不要重新初始化覆盖。

## 7. Gunicorn 本地测试

先只绑定本机地址测试：

```bash
cd /opt/grid_monitor
source .venv/bin/activate
gunicorn --workers 1 --bind 127.0.0.1:8000 "main:app"
```

另开一个终端在服务器内检查：

```bash
curl -I http://127.0.0.1:8000
```

确认无明显错误后，按 `Control + C` 停止这个临时 Gunicorn。

## 8. systemd 启动

项目提供示例：

```text
deploy/systemd/grid-monitor.service.example
```

复制到系统目录：

```bash
sudo cp /opt/grid_monitor/deploy/systemd/grid-monitor.service.example /etc/systemd/system/grid-monitor.service
```

如果你的服务器用户不是 `ubuntu`，请编辑 service 文件。Oracle 某些镜像可能使用 `opc`：

```bash
sudo nano /etc/systemd/system/grid-monitor.service
```

启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable grid-monitor
sudo systemctl start grid-monitor
sudo systemctl status grid-monitor
```

查看日志：

```bash
sudo journalctl -u grid-monitor -f
```

重启服务：

```bash
sudo systemctl restart grid-monitor
```

## 9. Caddy 反向代理和 HTTPS

项目提供示例：

```text
deploy/caddy/Caddyfile.example
```

示例内容：

```text
domain.com {
    reverse_proxy 127.0.0.1:8000
}
```

把 `domain.com` 改成你的域名，并让域名 A 记录指向服务器公网 IP。Caddy 在端口 `80/443` 可访问时，可以自动申请和续期 HTTPS 证书。

如果先在阿里云用 HTTP 演练，可以使用：

```text
deploy/caddy/Caddyfile.aliyun-http.example
```

它会监听 `:80` 并反代到 `127.0.0.1:8000`。

复制并重载：

```bash
sudo cp /opt/grid_monitor/deploy/caddy/Caddyfile.example /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo systemctl status caddy
```

## 10. 防火墙建议

只开放：

- `22/tcp` SSH
- `80/tcp` HTTP
- `443/tcp` HTTPS

不要开放：

- `5001`
- `8000`

UFW 示例：

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

Oracle Cloud 还需要在控制台安全列表 / NSG 中放行 `22`、`80`、`443`。

## 11. 更新代码

更新前建议先备份 SQLite：

```bash
cd /opt/grid_monitor
./scripts/backup_sqlite.sh
```

拉取代码并更新依赖：

```bash
cd /opt/grid_monitor
git pull
source .venv/bin/activate
pip install -r requirements.txt
python3 -m compileall app main.py tests
python3 -m pytest -q
sudo systemctl restart grid-monitor
```

如果服务器不安装测试依赖，也可以在本地测试通过后再部署；但正式更新前至少应确认服务能启动，日志无异常。

## 12. SQLite 备份

项目提供备份脚本：

```text
scripts/backup_sqlite.sh
```

默认：

- 数据库：`/opt/grid_monitor/instance/grid_monitor.sqlite3`
- 备份目录：`/opt/grid_monitor/backups`
- 文件名：`grid_monitor_YYYYMMDD_HHMMSS.sqlite3.gz`
- 保留最近 14 天

运行：

```bash
cd /opt/grid_monitor
./scripts/backup_sqlite.sh
```

可以加入 crontab，例如每天收盘后备份一次：

```bash
crontab -e
```

示例：

```text
30 16 * * 1-5 cd /opt/grid_monitor && ./scripts/backup_sqlite.sh >> /opt/grid_monitor/backups/backup.log 2>&1
```

## 13. SQLite 恢复

先停止服务：

```bash
sudo systemctl stop grid-monitor
```

恢复某个备份：

```bash
cd /opt/grid_monitor
cp instance/grid_monitor.sqlite3 instance/grid_monitor.sqlite3.before_restore
gunzip -c backups/grid_monitor_YYYYMMDD_HHMMSS.sqlite3.gz > instance/grid_monitor.sqlite3
sudo systemctl start grid-monitor
```

查看日志确认服务正常：

```bash
sudo journalctl -u grid-monitor -f
```

## 14. iFind 云端观察建议

Oracle Cloud ARM 或海外 VPS 访问 iFind 可能受网络、DNS、延迟或接口限制影响。第一次云端真实观察建议：

1. 只启用少数合约。
2. 先点击“测试 iFind 行情”。
3. 再点击“测试飞书通知”。
4. 开启自动监控后观察 1-2 个交易时段。
5. 确认 Dashboard、`/healthz`、AlertLog、飞书通知和日志都稳定后，再增加更多合约。

如果出现 iFind 参数错误或接口不稳定，优先检查完整合约代码，例如 `CU2608.SHF`、`Y2609.DCE`、`LC2609.GFE`，再看服务器网络连通性。

## 15. 自动交易时段控制

Dashboard 的“自动交易时段”设置默认关闭，启用后会按 `Asia/Shanghai` 北京时间决定是否允许后台 worker 发起行情轮询。它复用现有 Web + Worker 单服务和 `gunicorn workers=1` 模式，不会启动第二个 scheduler 进程。

默认计划：

- 商品期货日盘分为 `08:50-11:35`、`13:20-15:05` 两段；`11:35-13:20` 午间休盘期间自动暂停行情轮询。
- 夜盘 `20:50` 仅在至少一个已启用任务有夜盘时自动开启。
- 夜盘暂停时间取所有已启用夜盘品种中最晚的结束时间，再加 10 分钟缓冲；Dashboard 可调整为 5–30 分钟。
- 周六、周日不会自动开启新的日盘或夜盘。

第一版暂不特殊处理 `10:15-10:30` 商品短休，worker 会保持轮询。午间自动暂停只跳过行情 provider、网格判断、`AlertLog` 写入和飞书报警；不会关闭 Dashboard、Gunicorn、systemd 或 ECS。

例如 `CU2608.SHF` 识别为夜盘至 `01:00`，`NR2608.INE` 为 `23:00`，`LC2609.GFE` 无夜盘，因此最晚计划暂停为 `01:10`。加入 `SC2609.INE` 后会自动重算为 `02:40`。任务新增、删除、启停、代码修改、缓冲时间修改和服务重启后都会重新计算。

当前已确认的补充示例还包括 `SP.SHF` 夜盘至 `23:00`、`SR.CZC` 夜盘至 `23:00`。

交易时段表只维护已确认的品种。未知品种不会静默忽略：页面会显示“交易时段未识别”，并按最晚 `02:30` 加夜盘缓冲的保守兜底时间处理（默认 10 分钟时为 `02:40`）。请确认实际交易时段后再补充规则。第一版尚未接入法定节假日或节前无夜盘的正式交易日历，遇到这类情况应手动暂停。

如果某个已确认品种暂未内置，可在该面板的“品种时段覆盖”填写 `PRODUCT.EXCHANGE=HH:MM`，例如 `XX.CZC=23:00`；填写 `PRODUCT.EXCHANGE=NO_NIGHT` 则明确标记无夜盘。覆盖保存在 SQLite 的 `AppSetting` 中，不需要修改源码。

原有“暂停自动监控 / 恢复自动监控”按钮仍可用。自动交易时段开启时，手动动作是临时覆盖，持续到下一个计划边界；到边界后自动时段重新接管。自动暂停只停止行情轮询，不会关闭 Dashboard、Gunicorn、systemd 或 ECS。

## 16. 飞书报警重试与补发

真实网格报警先持久化到 `AlertLog`，随后立即尝试发送飞书。若遇到临时网络、超时、`HTTP 429`、`HTTP 5xx` 或飞书内部错误 `19006`，系统会在同一个后台 worker 的后续 tick 中重试；不会 `sleep` 阻塞行情轮询，也不会额外启动 worker。

默认重试策略为首次立即发送，失败后分别在 30、60、120 秒后再试，最多 4 次。可通过以下本地环境配置调整：

```text
FEISHU_RETRY_ENABLED=true
FEISHU_MAX_ATTEMPTS=4
FEISHU_RETRY_DELAYS_SECONDS=30,60,120
FEISHU_RETRY_BATCH_SIZE=20
```

如服务器已有早于可靠投递功能的报警历史，可选在 `.env` 设置可靠投递首次上线时间：

```text
NOTIFICATION_DELIVERY_ROLLOUT_AT=2026-07-17T10:57:16+08:00
```

该值必须带明确时区偏移（或使用 `2026-07-17 10:57:16 Asia/Shanghai`），应用会保存为 UTC。若不设置，系统仅在已有 `notification_deliveries` 时以最早投递记录推断上线时间；两者都不存在时，不会猜测当前启动时间，缺少投递记录的报警将保守显示为“无投递记录”。不要在文档、截图或日志中暴露 webhook、token、secret 或 `.env` 内容。

webhook 未配置、签名/权限/消息格式等明确配置错误不会重复重发，而会显示为最终失败。Dashboard 的报警历史可选择“送达：未送达”查看待发送、重试中与失败记录；补发消息会带原报警编号和原报警时间，不会重新创建 `AlertLog` 或重新执行网格计算。服务重启不会丢失 SQLite 中的待重试记录。

“测试飞书通知”仅用于人工验证通道，失败不会进入真实报警补发队列。排查最终失败时查看 Dashboard 的安全错误摘要和 `journalctl -u grid-monitor -f`，不要输出或截图 webhook、token、secret 或 `.env` 内容。
