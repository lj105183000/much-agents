# TLS 证书

生产 Nginx 从以下固定路径读取证书：

- `/etc/nginx/certs/fullchain.pem`
- `/etc/nginx/certs/privkey.pem`

本目录默认被 git 忽略，**不要把证书或私钥提交到仓库**。

## 内网自签（仅验收/临时使用）

```bash
./deploy/nginx/generate-self-signed.sh 192.168.1.20
```

脚本会生成 `fullchain.pem` 与 `privkey.pem`（输入为 IP 时使用 IP SAN，否则使用 DNS SAN）。把
`fullchain.pem` 分发给团队成员导入“受信任的根证书颁发机构”，或直接使用公司
内部 CA 签发的证书。

## 正式证书

将内部 CA 或公开 CA 签发的 `fullchain.pem` / `privkey.pem` 放入本目录即可，
`docker compose -f docker-compose.prod.yml restart nginx` 生效。

证书权限建议：`chmod 600 privkey.pem`。
