# 产品设计与技术架构演示

`much-agents-product-architecture.pptx` 是 Much Agents 的产品设计与技术架构演示文稿，18 页，16:9。

## 产品介绍视频

- ▶ [在线播放（GitHub Pages）](https://lj105183000.github.io/much-agents/presentation/)
- [下载 MP4（1080p，60 秒，中文男声旁白）](biaoshu-shaobing-product-intro-1080p.mp4)

视频使用浅色前端界面与合成演示数据录制，展示登录、文档库、编写大纲、企业资质、文档检索、任务历史、门禁合规与审计日志。

## 结构

| 页码 | 主题 |
|---|---|
| 01 | 封面：把标书生产做成一条可信流水线 |
| 02 | 执行摘要：4 个关键数字与 3 条设计原则 |
| 03 | 问题与产品定位 |
| 04 | 产品全景：6 个业务模块 + 3 层治理 |
| 05 | 角色、项目 ACL 与权限矩阵 |
| 06 | 端到端用户旅程与 4 个人工门 |
| 07 | 工作台交互示意 |
| 08 | 确定性骨架 + 受控 Agent 的生成流水线 |
| 09 | 21 项合规检查、21 类一致性事实与导出闸门 |
| 10 | 确定性 AI 配图（不做文生图） |
| 11 | 技术架构总览 |
| 12 | 解析、结构对齐、抽取、分块与向量入库 |
| 13 | LangGraph 与章节级持久化 runtime |
| 14 | PostgreSQL / Redis / MinIO / Chroma 的事实源边界 |
| 15 | 身份、项目隔离、隐私与审计哈希链 |
| 16 | 生产部署、可观测性与备份恢复 |
| 17 | P0 已交付与 P1–P4 路线图 |
| 18 | 收尾：AI 产能、规则底线、人的决定权 |

## 重新生成

从仓库根目录运行：

```bash
uv run --with python-pptx --with pillow python docs/presentation/build_deck.py
```

脚本只依赖 `python-pptx` 与 `Pillow`，背景图在构建时生成到临时目录，不进入仓库。演示文稿使用 Microsoft YaHei；在未安装该字体的机器上打开时会自动替换字体，排版可能略有变化。

## 内容边界

- 第 16 页的 `63/63` 与 `27/27` 来自 `docs/m1-acceptance-report.md` 的本地 Docker Desktop 演练，不代表最终生产验收。
- P0 之外的路线图条目是规划，不表示已实现；具体能力以仓库代码和 `CLAUDE.md` 为准。
- 演示中没有真实招标文件、投标人资料、账号口令或密钥。第 7 页为工作台交互示意，第 15 页的哈希值为链式结构示意。

## 预览

本地可用 LibreOffice 转 PDF，再按需渲染图片；`render/` 已在 `.gitignore` 中忽略：

```bash
soffice --headless --convert-to pdf --outdir render much-agents-product-architecture.pptx
```
