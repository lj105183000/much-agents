# 干净主机生产验收（Linux VM + 真实域名/CA）

> 状态：验收工具与清单已落地（2026-11-02）；**真实 Linux VM、DNS 与 CA 验收尚未执行**，需要部署方提供主机、域名、证书与测试账号。本文是干净主机上的执行手册，不能在本仓库 CI 中代替真实验收。

## 1. 前置检查

| 项 | 要求 |
|---|---|
| 主机 | Linux，Docker Engine 24+，`docker-compose`/compose 插件，4 GB+ 内存，20 GB+ 磁盘 |
| 域名 | 内网 DNS 或公网 DNS 已指向目标主机；测试机可解析 |
| 证书 | `deploy/nginx/certs/fullchain.pem`、`privkey.pem` 由企业 CA 或受信 CA 签发，SAN 包含域名 |
| 端口 | 仅 80/443 对外；5432/6379/9000/9001/8000/8001 不暴露 |
| 配置 | `deploy/env.production` 权限 600，所有 `CHANGE_ME` 已替换，`ENVIRONMENT=production` |
| 账号 | 用于登录冒烟的管理员（建议专用验收账号） |
| 备份 | 最近一次 `scripts/backup.sh` 成功，`backups/<timestamp>/SHA256SUMS` 存在 |

## 2. 部署

```bash
git clone <repo> much-agents && cd much-agents
cp deploy/env.production.example deploy/env.production
chmod 600 deploy/env.production
# 编辑 env.production：数据库/Redis/MinIO 密码、CORS_ORIGINS、MCP_API_TOKEN、模型 key
# 放入证书：
#   deploy/nginx/certs/fullchain.pem
#   deploy/nginx/certs/privkey.pem

docker-compose -f docker-compose.prod.yml up -d --build
docker-compose -f docker-compose.prod.yml ps
```

等待 `migrate` 成功、API/worker 启动、Nginx healthy。首次部署后创建验收管理员（也可使用已有管理员）：

```bash
docker-compose -f docker-compose.prod.yml exec -T api \
  python -m api.users create-admin --username deploy-check --display-name "部署验收" --password-stdin
```

## 3. 自动验收

若启用 monitoring profile，先在目标主机渲染并校验 Alertmanager 配置（见 `docs/deployment.md` 7.1）：

```bash
python3 scripts/render_alertmanager.py
docker compose -f docker-compose.prod.yml --profile monitoring \
  run --rm --no-deps alertmanager-config
docker compose -f docker-compose.prod.yml --profile monitoring \
  run --rm --no-deps --entrypoint /bin/amtool alertmanager \
  check-config /etc/alertmanager/alertmanager.yml
```

在目标主机（Python 3.10+，仅标准库）或可访问目标域名/证书的机器上：

```bash
DEPLOY_TEST_USERNAME=deploy-check DEPLOY_TEST_PASSWORD='...' \
python3 scripts/acceptance_check.py \
  --base-url https://much-agents.internal \
  --cafile /etc/ssl/certs/internal-ca.pem \
  --expect-ip 10.0.0.12 \
  --require-backup \
  --require-monitoring
```

检查项：

| 名称 | 期望 |
|---|---|
| `config` | 环境变量安全，无 `CHANGE_ME`；非 production 配置会失败 |
| `tls-file:*` | 证书与私钥存在且非空 |
| `monitoring` | 渲染后的 Alertmanager 配置存在、无占位值且至少有一个 webhook/email 渠道；缺失时默认 warn，`--require-monitoring` 时 fail |
| `backup` | 最近备份在 `--max-backup-age-hours`（默认 48h）内；`--require-backup` 时缺失即失败 |
| `dns` | 域名可解析；`--expect-ip` 时地址匹配 |
| `ports` | 内部端口从外部不可达（loopback 主机会跳过） |
| `tls` | 证书由 `--cafile` 验证通过，剩余 ≥30 天（<30 天 warn，过期 fail） |
| `healthz/live/ready` | 200；`/api/live` 为 `{"status":"ok"}`；`/api/ready` 依赖全部可用 |
| `root` | 200 |
| `docs-hidden` / `metrics-hidden` | `/docs`、`/api/metrics` 由 Nginx 返回 404 |
| `headers` | HSTS、CSP、`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY` |
| `http-redirect` | HTTP 301/308 到 HTTPS |
| `login` | 登录 200 且 `/api/auth/me` 200（未提供账号时 warn 跳过） |

退出码 1 表示存在 fail；warn 不失败。脚本不会打印密码、token 或证书私钥。

仅检查配置（不需要网络）：

```bash
python3 scripts/acceptance_check.py --config-only
```

## 4. 人工验收清单

自动检查之外，必须人工完成并记录证据：

- [ ] 浏览器登录/登出、修改密码；错误密码被拒并触发锁定策略。
- [ ] 项目管理：创建项目、添加 writer/reviewer/viewer、跨项目访问返回 404。
- [ ] 上传一份真实招标 PDF（或验收样本），确认解析/抽取/检索可用。
- [ ] 创建投标 run、编辑大纲、生成至少 1 节、组装、查看合规/一致性/需求覆盖/引用。
- [ ] 评论 @提及与指派，确认站内通知；如配置 Webhook/SMTP，确认收到且不含正文。
- [ ] 审批通过/驳回；修改章节后审批失效；严格导出 DOCX/PDF。
- [ ] 审计：`python -m api.audit verify` 返回 valid；关键操作有事件。
- [ ] 备份：`scripts/backup.sh` 成功；在测试栈执行 `scripts/restore.sh` 恢复并复测登录/产物下载。
- [ ] 重启演练：`docker-compose restart api worker-parse worker-bid nginx` 后服务恢复。
- [ ] 日志/指标：`/api/ready`、Prometheus 目标、Alertmanager 真实通知渠道。

## 5. 记录与退出标准

建议在 `docs/acceptance/`（gitignored 或脱敏后提交）记录：日期、主机/域名、镜像 digest、`acceptance_check.py` 输出、人工清单勾选、备份恢复耗时、已知偏差与负责人签字。

退出标准：自动检查无 fail；人工清单全部完成；备份恢复演练成功；审计链 valid；没有未登记的高危安全问题。

## 6. 当前无法自动完成的部分

本仓库无法代替以下外部条件：真实 Linux 主机、可解析域名、受信 CA 证书、防火墙/DNS 策略、企业 SMTP/Webhook 渠道、真实招标样本与验收账号。完成这些后按本文执行，并把结果回填到验收记录。
