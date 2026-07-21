# HANDOFF — 到底吃啥哟 · 后端
> 跨 agent/IDE 接手文档 | 最后更新: 2026-07-20 | 改动项目后请同步更新此文档

## 项目定位
微信小程序「到底吃啥哟」的后端服务：一个 FastAPI + SQLite 的**美食热度 API**，帮用户解决"今天吃什么"。
核心能力：多源热度排行聚合、AI 食材配菜推荐、下厨房菜谱库、每日趋势快报。
前端为独立仓库（Taro + React + TS），本仓库只负责后端。

## 当前状态
- **开发版本**: v1.14.1（零等待 Stage A/B + 菜谱步骤补全双通道，2026-07-18）
- **生产版本/镜像 SHA**: v1.14.1 `0be5a2939030033ddac230bb2fa3bc5c48b411bb`（2026-07-18 部署；回滚备份 compose `.bak.pre1141` = v1.13.1 `61b313b...`）
- **上线实测（2026-07-18）**: 预生成命中 `/api/recommend` **0.09s**；LLM 调用期间并发 health **0.03s**（事件循环修复生效）；冷门组合全量 LLM 路径 ~23s（新前端走 quick 端点后为 2-5s）
- **生产补全（2026-07-20 完成）**: LLM 步骤补写**已完成** — recipes 656 条中 653 条 steps 已填充（steps_source=llm），仅 3 条失败；真实补爬升级**已尝试并失败**：`scripts/backfill_recipe_steps.py` 首个请求即 302→humancheck CAPTCHA 熔断（processed 1 / updated 0，日志 `/app/data/backfill_scrape.log`）——NAS 出口 IP 被下厨房风控锁定，短期勿重试；后续选项 = 等 IP 冷却 / 换代理出口 / 接受 llm 步骤为最终数据
- **465 组合矩阵已铺满（2026-07-20 手动触发完成）**: fresh 243 → **462/465**（`/tmp/run_pregen.py` budget=465：attempts 223 / generated 219 / 4 次失败，日志 `/app/data/pregen_manual.log`，历时约 85 分钟）；剩 3 个组合与日常过期刷新由每日 03:30 cron（预算 120/天）兜底
- **测试基线**: 352 tests pass / 95.64% coverage（CI 门控 95%）
- **部署位置**: 极空间 Z4Pro NAS Docker，内网 `http://192.168.1.64:8900`，外网 `https://food.zuitian.ai`（Cloudflare Tunnel；AT&T 封 443 端口所以走 Tunnel 绕过）
- **步骤数据补全（2026-07-18 用户决策，取代此前的"合规阻断"）**: 双通道并行——`scripts/backfill_steps_via_llm.py`（LLM 按菜名+配料补写，落 `steps_source='llm'`）+ `scripts/backfill_recipe_steps.py`（下厨房真实补爬，落 `'scraped'`，可覆盖 llm，反向禁止）。核心逻辑在 `app/crawler/steps_backfill.py`，逐行 commit 可断点续跑、连续 5 失败熔断、CAPTCHA 即停。**实测下厨房风控极敏感（10s 间隔第 2 个请求即 CAPTCHA）**，真实补爬默认 30s 间隔、预期进度缓慢
- 步骤全空的根因已修（`xiachufang.py::_parse_detail_page`：JSON-LD 字符串形态 recipeInstructions 未处理 + 提前 return 挡住 DOM fallback；详见 `docs/plans/xiachufang-selector-notes.md`）

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
- **Stage C 已完成（2026-07-20）**：后端 v1.14.1 已部署、LLM 步骤补写完成（653/656）；**前端 v1.8.0 已于 2026-07-20 审核通过并发布上线**（合并 main `31a5aea`）。发布日 API 抽查：health 1.14.1 ✓ / quick 0.07s ✓ / steps 流式 0.11s ✓。
- **A1-A3 已完成（2026-07-18）**：真实页面 fixture 已存（`tests/fixtures/xiachufang_detail_2026.html`）、解析 bug 已修、双通道补全脚本已建；`RECIPE_SCRAPE_ENABLED` 代码默认仍 false，**部署时在 NAS compose 显式置 true** 恢复每周菜谱抓取（用户决策）。
- **`/steps` 端点仍不复用无上下文本地菜谱**（防错配主食材/过敏原）：补全的 steps 主要惠及老端点 `/api/recommend` 本地秒回与降级链兜底；若要新流程复用真实菜谱（菜名精确匹配+食材相容），是后续可选小任务。
- **`trend_type` 填充率低**：AI extractor 保守，靠日常爬虫渐进填充。
- **A6 双模型竞速未实现**：该任务本来就是可选；现有降级链可配置 fast model 串行重试，不是双模型并发竞速。
- **README.md 已过时**（还写着 Claude API / 150+ 词典 / 只列 trending 端点）：以本 HANDOFF 为准，有空可同步更新 README。

## 相关资源
- 仓库: https://github.com/jasonxi89/daodichishayou-backend
- 前端仓库: `C:\Users\goodb\WeChatProjects\daodichishayou`（Taro+React，AppID wx5b37ff3cec339cfb）
- Memory（过往会话知识沉淀，以仓库实况为准）: `daodichishayou_progress.md`（进度/TODO/版本记录）、`nas_deployment.md`（部署六步）、`openrouter_gateway.md`（LLM 网关）
- **凭据位置（本文档不含任何密钥）**: NAS SSH 凭据在本机 `C:\Users\goodb\nas_ssh.py`；`OPENROUTER_API_KEY` 在 NAS compose 环境变量（本地 `.env`，已 gitignore）；Docker Hub token 在 GitHub repo Secrets（`DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN`）。
