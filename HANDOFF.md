# HANDOFF — 到底吃啥哟 · 后端
> 跨 agent/IDE 接手文档 | 最后更新: 2026-07-17 | 改动项目后请同步更新此文档

## 项目定位
微信小程序「到底吃啥哟」的后端服务：一个 FastAPI + SQLite 的**美食热度 API**，帮用户解决"今天吃什么"。
核心能力：多源热度排行聚合、AI 食材配菜推荐、下厨房菜谱库、每日趋势快报。
前端为独立仓库（Taro + React + TS），本仓库只负责后端。

## 当前状态
- **版本**: v1.13.1（`app/config.py` → `APP_VERSION`；`main.py` FastAPI 也读同一常量）
- **部署镜像 SHA**: `61b313b312d7907a24e8a3ed3abfd3386a6662ef`（2026-07-17 部署）
- **测试基线**: 261 tests pass / 96% coverage（CI 门控 95%）
- **健康检查**: `GET https://food.zuitian.ai/api/health` → `{"status":"ok","version":"1.13.1"}`（已实测）
- **部署位置**: 极空间 Z4Pro NAS Docker，内网 `http://192.168.1.64:8900`，外网 `https://food.zuitian.ai`（Cloudflare Tunnel；AT&T 封 443 端口所以走 Tunnel 绕过）
- main 与 origin/main 同步，工作区干净，无待办部署事项

## 技术栈与结构
- **栈**: FastAPI 0.115 + SQLAlchemy 2.0 + SQLite（WAL 模式）+ APScheduler + httpx + BeautifulSoup4；LLM 走 **OpenRouter**（`openai` SDK，非 anthropic）；Docker + GitHub Actions CI/CD
- **LLM 网关**: `OpenAI(base_url="https://openrouter.ai/api/v1")`，默认模型 `deepseek/deepseek-v4-pro`；切模型只改 NAS compose 的 `OPENROUTER_MODEL` env 后 recreate；调用形态 `client.chat.completions.create(...)`，读 `resp.choices[0].message.content`
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
- **主要端点**: `GET /api/health`、`GET /api/trending`（聚合排行）、`GET /api/trending/digest`（每日快报）、`POST /api/recommend`（AI 食材配菜）、`GET /api/recipes/search`（菜名/食材搜索）、`POST /api/foods-by-category` 及 bulk 版、`POST /api/admin/merge-aliases`。完整文档见 `/docs`。

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
- **本地菜谱库 `steps_json` 全空**（生产 656 条 recipes 步骤全 NULL）：下厨房 `xiachufang.py` 的 `_parse_detail_page` 步骤选择器过时（配料解析正常，仅步骤失效）→ `_search_local_recipes` 过滤 `steps_json IS NOT NULL` 永远匹配 0 → **recommend 每次都走 LLM（约 20-50s）**，本地秒回从未生效。修法：抓真实详情页找新选择器 → 修 parser → 重爬 656 条补步骤。
- **`trend_type` 填充率低**：AI extractor 保守，靠日常爬虫渐进填充。
- **README.md 已过时**（还写着 Claude API / 150+ 词典 / 只列 trending 端点）：以本 HANDOFF 为准，有空可同步更新 README。

## 相关资源
- 仓库: https://github.com/jasonxi89/daodichishayou-backend
- 前端仓库: `C:\Users\goodb\WeChatProjects\daodichishayou`（Taro+React，AppID wx5b37ff3cec339cfb）
- Memory（过往会话知识沉淀，以仓库实况为准）: `daodichishayou_progress.md`（进度/TODO/版本记录）、`nas_deployment.md`（部署六步）、`openrouter_gateway.md`（LLM 网关）
- **凭据位置（本文档不含任何密钥）**: NAS SSH 凭据在本机 `C:\Users\goodb\nas_ssh.py`；`OPENROUTER_API_KEY` 在 NAS compose 环境变量（本地 `.env`，已 gitignore）；Docker Hub token 在 GitHub repo Secrets（`DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN`）。
