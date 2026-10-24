# 依赖安全审计与例外

CI 的 `security` job 包含两项检查：

1. **gitleaks**：扫描完整 Git 历史中的密钥与凭据，不允许例外。
2. **pip-audit**：对 `uv export --frozen --no-dev` 导出的生产依赖做漏洞审计（OSV 数据源）。

本文只记录 pip-audit 的**精确例外**及其风险接受理由。任何新增例外都必须单独评审，禁止使用包级忽略或关闭整个审计。

## 当前例外：chromadb 1.5.9

截至 **2026-10-24**，PyPI 最新版仍是 `chromadb 1.5.9`，OSV 报告 **5 条记录 / 4 个唯一漏洞**，均无修复版本：

| GHSA | CVE / PYSEC | 类型 |
| --- | --- | --- |
| `GHSA-f4j7-r4q5-qw2c` | CVE-2026-45829 / PYSEC-2026-311 | 预认证代码注入 |
| `GHSA-2wm9-hf6c-p5cr` | CVE-2026-45830 / PYSEC-2026-3813 | 已认证用户跨租户任意读写 |
| `GHSA-36p7-vc44-83pf` | CVE-2026-45833 / PYSEC-2026-3814 | 代码注入 |
| `GHSA-xph7-9rjv-w5fr` | CVE-2026-45831 / PYSEC-2026-3815 | SimpleRBAC 未校验租户/数据库/集合 |

CI 通过 `--ignore-vuln` 精确忽略以上 4 个 GHSA ID；PYSEC/CVE 别名由 pip-audit 自动匹配。其他依赖或未来新增漏洞仍然会让 job 失败。

### 风险接受理由

- 四个漏洞都位于 **Chroma 服务端**代码路径（HTTP 服务、认证与 RBAC）；本项目只使用 `chromadb.HttpClient` 作为客户端，不启动 Python 内置服务端，也不使用 `SimpleRBACAuthorizationProvider`。
- 生产 Compose 中 Chroma 是独立容器，没有发布任何宿主机端口，只通过内部网络供 API/worker 访问；对外只暴露 Nginx 的 80/443。
- API/worker 镜像以非 root、只读根文件系统运行，且 Chroma 不可达时检索会自动回退关键词。
- 上游尚无修复版本，无法通过升级消除；降级或改用旧版会引入更多已知问题。

### 例外失效条件（必须重新评审）

出现以下任一情况时，应立即移除例外或重新评估，而不是继续沿用：

- `chromadb` 或 `chromadb-client` 发布修复版本，或升级到新的大版本；
- 部署改为对外暴露 Chroma 端口，或允许不可信用户直接访问 Chroma API；
- 代码改为在 API/worker 进程内启动 Chroma 服务端（embedded/server 模式）；
- 启用 Chroma 的认证/RBAC 多租户能力，或把 Chroma 用于跨项目共享集合；
- OSV 为该包新增漏洞，或 pip-audit 报告其他依赖的问题。

### 复核命令

```bash
uv export --frozen --no-dev --no-hashes --format requirements-txt > /tmp/requirements.txt
uvx --from pip-audit pip-audit -r /tmp/requirements.txt --format columns
```

如输出中出现新的 ID，不要直接扩大忽略列表；先评估可达性并记录到本文，再修改 `.github/workflows/ci.yml`。

## 移除例外

上游修复后：

1. 升级 `pyproject.toml` 中的 `chromadb` 约束并运行 `uv lock`；
2. 删除 `.github/workflows/ci.yml` 中对应的 `--ignore-vuln` 参数；
3. 更新或删除本文的例外表；
4. 重新运行完整测试与 CI。
