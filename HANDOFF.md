# HANDOFF — 到底吃啥哟 · 后端
> 跨 agent/IDE 接手文档 | 最后更新: 2026-07-18 | 改动项目后请同步更新此文档

## 项目定位
微信小程序「到底吃啥哟」的后端服务：一个 FastAPI + SQLite 的**美食热度 API**，帮用户解决"今天吃什么"。
核心能力：多源热度排行聚合、AI 食材配菜推荐、下厨房菜谱库、每日趋势快报。
前端为独立仓库（Taro + React + TS），本仓库只负责后端。

## 当前状态
- **开发版本**: v1.14.0（`feature/zero-wait`；尚未合并/推送/部署）
- **生产版本/镜像 SHA**: v1.13.1 `61b313b312d7907a24e8a3ed3abfd3386a6662ef`（2026-07-17 部署）
- **测试基线**: 329 tests pass / 95.43% coverage（CI 门控 95%）
- **生产健康检查**: `GET https://food.zuitian.ai/api/health` 仍应返回 `{"status":"ok","version":"1.13.1"}`
- **部署位置**: 极空间 Z4Pro NAS Docker，内网 `http://192.168.1.64:8900`，外网 `https://food.zuitian.ai`（Cloudflare Tunnel；AT&T 封 443 端口所以走 Tunnel 绕过）
- `feature/zero-wait` 已完成 Stage A/B 代码与对抗审查，工作区干净；Stage C 合并、推送、部署与生产验证尚未执行

## 技术栈与结构
- **栈**: FastAPI 0.115 + SQLAlchemy 2.0 + SQLite（WAL 模式）+ APScheduler + httpx + BeautifulSoup4；LLM 走 **OpenRouter**（`openai` SDK，非 anthropic）；Docker + GitHub Actions CI/CD
- **LLM 网关**: openai SDK + env 决定渠道。**2026-07-17 起走 DeepSeek 官网直连**：`OPENROUTER_BASE_URL=https://api.deepseek.com`、`OPENROUTER_MODEL=deepseek-v4-pro`（试过 flash 因快报文风干瘪换回 pro；官网模型名无 `deepseek/` 前缀；官网直连 + 自动上下文缓存后 pro 实测 recommend ~14s，远快于 OpenRouter 时代的 44s）。切模型/渠道只改 NAS compose env 后 recreate；调用形态 `client.chat.completions.create(...)`，读 `resp.choices[0].message.content`。回滚 OpenRouter：compose 备份 `.bak.preds-direct`
- **目录**:
  ```
  app/
  ├── main.py            # FastAPI 入口 + lifespan（建表/v1.9.0 迁移/seed/APScheduler cron 调度）
  ├── config.py          # 配置 + APP_VERSION + AI_CORE_RULES
  ├── database.py        # 引擎/Session（WAL + busy timeout）
  ├── models.py          # ORM 模型（FoodTrend/Recipe/FoodDigest/别名/快照/缓存等）
  ├── schemas.py         # Pydantic 请求/响应
  ├── routers/           # trending / recommend / recipe / admin 四个 router
  ├── crawler/           # base + food_keywords(500+) + toutiao/baidu_suggest/dailyhot
  │                      #   + recipe_base/xiachufang(下厨房) + ai_extractor/ai_digest + scheduler
  └── migrations/        # backfill_v1_9_0（启动时幂等执行）
  tests/                 # pytest，asyncio_mode=auto，按 routers/crawler 分子目录
  ```
- **主要端点**: `GET /api/health`、`GET /api/trending`、`GET /api/trending/digest`、`POST /api/recommend`、`POST /api/recommend/quick`、`POST /api/recommend/steps`（支持 NDJSON stream）、`GET /api/recipes/search`、`POST /api/foods-by-category` 及 bulk 版、`POST /api/admin/merge-aliases`。完整文档见 `/docs`。

## 常用命令
```bash
# 本地跑测试（仓库已带 .venv）
.venv/Scripts/python.exe -m pytest              # 全量
.venv/Scripts/python.exe -m pytest --cov=app    # 带覆盖率

# 本地起服务
uvicorn app.main:app --port 8900                # 访问 http://localhost:8900/docs

# Docker 本地
docker compose up --build
```
**部署流程概要**（详见 memory `nas_deployment.md` 六步）：
1. `git push` → GitHub Actions 自动构建镜像推 Docker Hub（`jasonxi89/daodichishayou-backend:<SHA>`，NAS 不会自动拉）
2. `git rev-parse HEAD` 取 SHA → SFTP 写新 compose 到 NAS `/tmp` → `sudo cp` 到 compose_config 目录
3. `docker compose -p compose_config -f <本项目yaml> -f <zuitian yaml> pull` → `up -d --force-recreate`
4. `docker image prune -f` 清旧镜像 → `GET /api/health` 验证版本号

## 约定与坑
- **镜像必须用 commit SHA tag，绝不用 `:latest`**：NAS registry mirror 会缓存旧 manifest，用 latest 会拉到旧代码。
- **compose 必须显式 `-p compose_config`**：否则 project 名变成目录名产生孤儿容器。本项目与 zuitian **共用同一 project**，`up` 时必须两个 `-f` 一起带，否则对方被判为 orphan 清掉。
- **SQLite DateTime 列不能用裸 `date` 对象做 `==` 比较**：存储值是 `'... 00:00:00.000000'`，裸 date 绑定成 `'2026-07-17'` 永远不相等 → 曾导致每日快照同日重跑撞 UNIQUE 崩溃。要用 `datetime.combine(date.today(), datetime.min.time())`。
- **版本号每次功能更新/修复必须 bump**：`app/config.py` → `APP_VERSION`（semver：功能 +minor，fix +patch），靠 `/api/health` 验证。
- **git commit 不加 Co-Authored-By 行**，不把 Claude 写进 contributor。
- **`./data:/app/data` volume 必须保留**：SQLite 库 `food_trends.db` 在里面，删卷 = 丢全部热度/菜谱/快照数据。
- 所有 LLM 调用已加显式超时 `LLM_TIMEOUT_SECONDS`（默认 60s）。
- 诊断技巧：客户端超时断开的请求 uvicorn **不写 access log**（后端日志里会"隐形"，只留孤儿 httpx OpenRouter 行）。

## 进行中 / TODO
- **零等待 Stage A/B 已实现并通过双 reviewer**：缓存/预生成、阻塞 LLM 隔离、quick+steps、AsyncOpenAI NDJSON 流、静默降级、严格本地菜谱解析、输入/缓存/并发安全均已完成。最终门控 329 tests / 95.43%。前端 v1.8.0 同步完成 184 tests + WeChat build。
- **Stage C 尚未执行**：两仓仍在 `feature/zero-wait`；下一步是按 `docs/plans/2026-07-17-zero-wait-ux.md` 合并/推送、构建 SHA 镜像、NAS 部署、健康检查和真机回归。未经明确授权不要直接部署。
- **A1-A3 数据抓取未执行**：未请求下厨房、未保存第三方页面 fixture、未对抓取数据做 LLM backfill；原因与边界见 `docs/plans/xiachufang-selector-notes.md`。菜谱抓取 scheduler 和手工入口现默认禁用（`RECIPE_SCRAPE_ENABLED=false`）。
- **生产 656 条 recipes 的 `steps_json` 可能仍为空**：`/steps` 不会按模糊菜名复用无上下文本地菜谱，只使用 exact-context cache 或带请求上下文的 LLM，避免错配主食材/过敏原。
- **`trend_type` 填充率低**：AI extractor 保守，靠日常爬虫渐进填充。
- **A6 双模型竞速未实现**：该任务本来就是可选；现有降级链可配置 fast model 串行重试，不是双模型并发竞速。
- **README.md 已过时**（还写着 Claude API / 150+ 词典 / 只列 trending 端点）：以本 HANDOFF 为准，有空可同步更新 README。

## 相关资源
- 仓库: https://github.com/jasonxi89/daodichishayou-backend
- 前端仓库: `C:\Users\goodb\WeChatProjects\daodichishayou`（Taro+React，AppID wx5b37ff3cec339cfb）
- Memory（过往会话知识沉淀，以仓库实况为准）: `daodichishayou_progress.md`（进度/TODO/版本记录）、`nas_deployment.md`（部署六步）、`openrouter_gateway.md`（LLM 网关）
- **凭据位置（本文档不含任何密钥）**: NAS SSH 凭据在本机 `C:\Users\goodb\nas_ssh.py`；`OPENROUTER_API_KEY` 在 NAS compose 环境变量（本地 `.env`，已 gitignore）；Docker Hub token 在 GitHub repo Secrets（`DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN`）。
