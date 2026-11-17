# HANDOFF — 到底吃啥哟 · 后端
> 跨 agent/IDE 接手文档 | 最后更新: 2026-11-16 | 改动项目后请同步更新此文档

## 项目定位
微信小程序「到底吃啥哟」的后端服务：一个 FastAPI + SQLite 的**美食热度 API**，帮用户解决"今天吃什么"。
核心能力：多源热度排行聚合、AI 食材配菜推荐、下厨房菜谱库、每日趋势快报。
前端为独立仓库（Taro + React + TS），本仓库只负责后端。

## 当前状态
- **开发版本**: v1.16.0（全部 LLM 调用改走 **Claude API**：官方 `anthropic` SDK，`claude-opus-5-5`，思考强度 `low`，2026-11-16；在分支 `feature/claude-opus-5-5`，尚未合并 main）
- **v1.16.0 部署 SHA**: 待主会话部署后回填。部署时 NAS compose 要把 `OPENROUTER_*` 三项换成 `ANTHROPIC_API_KEY` / `CLAUDE_MODEL=claude-opus-5-5` / `CLAUDE_EFFORT=low`，旧 compose 另存一份备份以便回滚
- **生产版本/镜像 SHA**: 截至 2026-11-16 线上仍是 v1.15.3（SHA `c777c4d`，DeepSeek `deepseek-flash`，2026-09-10 部署；更早的回滚备份 compose `.bak.pre-flash` = v1.15.1 `ba2a1e5...` + 模型 `deepseek-v4-pro`）
- **新端点 `GET /api/trending/categories/annotated`**（v1.15.0/1，供前端混血主题菜单格小字）：返回 `{categories:[{name,note}]}`；小注缺失时单次 LLM 批量生成→`category_notes` 表永久缓存（生产已生成 15 条，缓存命中 0.11s）；LLM 失败/无 key 时 note=null 静默降级，老端点 `/categories` 形状不变。⚠️ 解析必须截取 JSON 子串（v1.15.1 修复：deepseek 纯JSON prompt 仍可能带前后缀文字）
- **上线实测（2026-07-18）**: 预生成命中 `/api/recommend` **0.09s**；LLM 调用期间并发 health **0.03s**（事件循环修复生效）；冷门组合全量 LLM 路径 ~23s（新前端走 quick 端点后为 2-5s）
- **生产补全（2026-07-20 完成）**: LLM 步骤补写**已完成** — recipes 656 条中 653 条 steps 已填充（steps_source=llm），仅 3 条失败；真实补爬升级**已尝试并失败**：`scripts/backfill_recipe_steps.py` 首个请求即 302→humancheck CAPTCHA 熔断（processed 1 / updated 0，日志 `/app/data/backfill_scrape.log`）——NAS 出口 IP 被下厨房风控锁定，短期勿重试；后续选项 = 等 IP 冷却 / 换代理出口 / 接受 llm 步骤为最终数据
- **465 组合矩阵已铺满（2026-07-20 手动触发完成）**: fresh 243 → **462/465**（`/tmp/run_pregen.py` budget=465：attempts 223 / generated 219 / 4 次失败，日志 `/app/data/pregen_manual.log`，历时约 85 分钟）；剩 3 个组合与日常过期刷新由每日 03:30 cron（预算 120/天）兜底
- **测试基线**: 451 tests pass / 96% coverage（CI 门控 95%）；本机 Python 3.13 和按新 requirements 新建的 3.12 环境都跑过，CI 是 3.11
- **部署位置**: 极空间 Z4Pro NAS Docker，内网 `http://192.168.1.64:8900`，外网 `https://food.zuitian.ai`（Cloudflare Tunnel；AT&T 封 443 端口所以走 Tunnel 绕过）
- **步骤数据补全（2026-07-18 用户决策，取代此前的"合规阻断"）**: 双通道并行——`scripts/backfill_steps_via_llm.py`（LLM 按菜名+配料补写，落 `steps_source='llm'`）+ `scripts/backfill_recipe_steps.py`（下厨房真实补爬，落 `'scraped'`，可覆盖 llm，反向禁止）。核心逻辑在 `app/crawler/steps_backfill.py`，逐行 commit 可断点续跑、连续 5 失败熔断、CAPTCHA 即停。**实测下厨房风控极敏感（10s 间隔第 2 个请求即 CAPTCHA）**，真实补爬默认 30s 间隔、预期进度缓慢
- 步骤全空的根因已修（`xiachufang.py::_parse_detail_page`：JSON-LD 字符串形态 recipeInstructions 未处理 + 提前 return 挡住 DOM fallback；详见 `docs/plans/xiachufang-selector-notes.md`）

## 技术栈与结构
- **栈**: FastAPI 0.115 + SQLAlchemy 2.0 + SQLite（WAL 模式）+ APScheduler + httpx + BeautifulSoup4；LLM 走 **Claude API**（官方 `anthropic` SDK 2.3.0，基于 httpx2，和爬虫用的 httpx 互不影响）；Docker + GitHub Actions CI/CD
- **LLM 封装（v1.16.0 起）**: 全部调用收在 `app/services/llm.py`，调用点只拼提示词、解析业务字段。对外四样：`complete_text(系统提示词, 用户提示词, model=None)` 同步补全；`run_realtime(函数, ...)` 给实时接口用，在线程里跑并加总时限；`stream_text(...)` 异步流式（NDJSON 步骤流）；`parse_json_object(回复)` 从回复里取 JSON（截取第一个 `{` 到最后一个 `}`，围栏和前后说明都不碍事）。失败统一抛 `LLMError`（接口报错 / 限流 / 连不上 / 超时 / 拒答 / 截断 / 空回复），调用点原有的降级链和 502/503 不变
- **请求形态**: `client.beta.messages.stream(model, max_tokens=128000, system, messages, output_config={"effort": CLAUDE_EFFORT}, betas=["server-side-fallback-2026-07-01"], fallbacks="default")`；拒答兜底参数只对 `llm.MODELS_WITH_SERVER_SIDE_FALLBACK` 里的模型发送（Fable 5.1/5.2、Opus 5/5.5、Sonnet 5.5；Haiku 5.5 没有服务端兜底，`CLAUDE_FAST_MODEL` 填它时自动不带）。**不要加** `thinking`、`temperature`、`top_p`、prefill、强制 `tool_choice`，Opus 5.5 会 400；继续用“提示词要求纯 JSON + 宽松解析”，没有上结构化输出
- **思考强度为什么是 low（用户 2026-11-16 决定）**: 近 10 天没有真实用户请求，751 次调用全是后台任务（凌晨预生成每天 56–120 次 + 每天 4 轮爬虫）。主会话用预生成提示词实测：`max` 档 125 秒、0.29 美元一次；`low` 档 3.4 秒出第一个字、17.8 秒写完、1822 个输出 token、约 0.04 美元。用户选了「切 Claude，low 档，预生成数量不变」，估算每天 3–5 美元。**改档 = 改 NAS compose 的 `CLAUDE_EFFORT` 后 recreate**（只能填 low/medium/high/xhigh/max，填错容器起不来）；想省钱可以调小 `PREGEN_DAILY_BUDGET`
- **时限**: `LLM_TIMEOUT_SECONDS`（默认 60）有两个用途。①所有调用的读超时（相邻数据之间）。②实时接口单次调用的总时限：`/api/recommend`、`/api/recommend/quick`、`/api/recommend/steps`（含流式）、`/api/foods-by-category`、`/api/bulk-foods-by-category`、`/api/trending/categories/annotated` 经 `llm.run_realtime`（流式那条经 `stream_text(deadline_seconds=...)`）调用，超过就取消请求并走降级。后台任务（预生成、爬虫提取、快报、别名归并、步骤补写）不设总时限。对应测试 `tests/test_llm_deadline_policy.py`
- **回滚到 DeepSeek**: 镜像改回 v1.15.3 的 SHA `c777c4d59b3ea705c067b0c4eec32fb46ae0415d`，compose 恢复部署 v1.16.0 前的备份（`OPENROUTER_*` 三项）。v1.16.0 没有改表结构，数据不用动
- **目录**:
  ```
  app/
  ├── main.py            # FastAPI 入口 + lifespan（建表/v1.9.0 迁移/seed/APScheduler cron 调度）
  ├── config.py          # 配置 + APP_VERSION + AI_CORE_RULES
  ├── database.py        # 引擎/Session（WAL + busy timeout）
  ├── models.py          # ORM 模型（FoodTrend/Recipe/FoodDigest/别名/快照/缓存等）
  ├── schemas.py         # Pydantic 请求/响应
  ├── routers/           # trending / recommend / recipe / admin 四个 router
  ├── services/          # llm.py（Claude API 封装）+ 本地菜谱搜索 / 推荐缓存 / 降级链
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
- `LLM_TIMEOUT_SECONDS`（默认 60s）是读超时，实时接口另外当作单次调用的总时限，详见上面的「时限」。
- **`llm.complete_text` 只能在没有事件循环的线程里调用**（内部用 `asyncio.run`）：`async def` 接口一律 `await llm.run_realtime(生成函数, ...)`，不要直接调，也不要换回裸的 `asyncio.to_thread`（那样没有总时限）。后台任务和脚本直接调即可。
- **测试不许真连 Claude**：`tests/conftest.py` 在导入 app 前清掉 `ANTHROPIC_API_KEY` / `CLAUDE_*` 环境变量（这台 Windows 的用户级环境变量里有一个已失效的 key），并有一个 autouse 防护，没打桩就走到客户端工厂会直接失败。调用点的测试在 `app.services.llm.complete_text` / `stream_text` 上打桩；`tests/services/test_llm_with_real_sdk.py` 用真实 SDK 加假传输层校验请求体和 SSE 解析，升级 SDK 后先看它。
- **两个分类食物提示词的 JSON 示例**曾多写一层花括号（f-string 里写了四个），v1.16.0 一并改对（单独一个提交）；以后在 f-string 提示词里写 JSON 示例，花括号写两个。
- 诊断技巧：客户端超时断开的请求 uvicorn **不写 access log**（后端日志里会“隐形”，只留下大模型那几行）。每次调用成功或失败都有 `app.services.llm` 的日志：`claude done model=... stop_reason=... input_tokens=... output_tokens=... seconds=...`，报错行带 `request_id`，拿它可以在 Anthropic Console 里查到那次请求。

## 进行中 / TODO
- **v1.16.0（Claude API）待合并部署**：分支 `feature/claude-opus-5-5` 已在本地通过 CI 同款命令，没有 push。合并 main → CI 构建 → 改 NAS compose 环境变量 → recreate → `GET /api/health` 应返回 1.16.0。
- **上线后要观察的**：①小程序步骤流首块 3 秒、块间 3 秒就回退到非流式（`src/services/api.ts`），Claude low 档实测 3.4 秒才出第一个字，回退会比以前常见，回退时那次流式调用白花；②分类小注接口前端只等 8 秒，缺小注时内联生成可能超时（生产 15 条已入库，一般不触发）；③快报文风和标题提取质量在 low 档下没评估过；④每天实际花费，看日志里的 `claude done` 行或 Console 用量。
- **零等待 Stage A/B 已实现并通过双 reviewer**：缓存/预生成、阻塞 LLM 隔离、quick+steps、异步 NDJSON 流、静默降级、严格本地菜谱解析、输入/缓存/并发安全均已完成。最终门控 329 tests / 95.43%。前端 v1.8.0 同步完成 184 tests + WeChat build。
- **Stage C 已完成（2026-07-20）**：后端 v1.14.1 已部署、LLM 步骤补写完成（653/656）；**前端 v1.8.0 已于 2026-07-20 审核通过并发布上线**（合并 main `31a5aea`）。发布日 API 抽查：health 1.14.1 ✓ / quick 0.07s ✓ / steps 流式 0.11s ✓。
- **A1-A3 已完成（2026-07-18）**：真实页面 fixture 已存（`tests/fixtures/xiachufang_detail_2026.html`）、解析 bug 已修、双通道补全脚本已建；`RECIPE_SCRAPE_ENABLED` 代码默认仍 false，**部署时在 NAS compose 显式置 true** 恢复每周菜谱抓取（用户决策）。
- **`/steps` 端点仍不复用无上下文本地菜谱**（防错配主食材/过敏原）：补全的 steps 主要惠及老端点 `/api/recommend` 本地秒回与降级链兜底；若要新流程复用真实菜谱（菜名精确匹配+食材相容），是后续可选小任务。
- **`trend_type` 填充率低**：AI extractor 保守，靠日常爬虫渐进填充。
- **A6 双模型竞速未实现**：该任务本来就是可选；现有降级链可配置 fast model 串行重试，不是双模型并发竞速。
- ~~**README.md 已过时**（还写着 Claude API / 150+ 词典 / 只列 trending 端点）~~ **已于 2026-08-23 完成**：README 已全面同步至 v1.15.1 实况（commit `0184e2f`，18 个业务端点、DeepSeek 直连、502 词典 / 15 分类、环境变量表与调度均已重写）。承载该改动的 `docs/readme-refresh` 分支已合并进 main 并于同日删除。

## 相关资源
- 仓库: https://github.com/jasonxi89/daodichishayou-backend
- 前端仓库: `C:\Users\goodb\WeChatProjects\daodichishayou`（Taro+React，AppID wx5b37ff3cec339cfb）
- Memory（过往会话知识沉淀，以仓库实况为准）: `daodichishayou_progress.md`（进度/TODO/版本记录）、`nas_deployment.md`（部署六步）、`openrouter_gateway.md`（LLM 网关）
- **凭据位置（本文档不含任何密钥）**: NAS SSH 凭据在本机 `C:\Users\goodb\nas_ssh.py`；`ANTHROPIC_API_KEY` 在 NAS compose 环境变量（本地 `.env`，已 gitignore）；Docker Hub token 在 GitHub repo Secrets（`DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN`）。
