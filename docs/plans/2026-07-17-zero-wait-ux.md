# 「到底吃啥哟」零等待体验改造 · 实施计划（一次性完成版）

> **给执行 agent**：本计划自包含，假设你对本项目零上下文。按任务顺序逐个执行；若你的环境有 superpowers:subagent-driven-development / executing-plans skill 可用其驱动，没有就按顺序人肉执行。**每个任务先写失败测试再实现**。
> 计划撰写: 2026-07-17；2026-07-18 重构为一次性完成版——原 P1/P2/P3 三阶段三次发版合并为一口气改完、单次发版（后端 1.13.1→1.14.0，前端 1.7.1→1.8.0），中途不部署。
> **2026-07-18 对抗审核修订**：修复"LLM 调用跑在线程池"事实错误（实际 async def 里同步调用会**阻塞整个事件循环**，A5 一并修复此现存线上问题）、quick 结果污染共享缓存（A7）、降级链按端点形状拆分（A9）、cache_key 加 count（A4）、全程 feature 分支（全局约束）、DishCard 拆分升为正式步骤（B1）、流式回退先 abort（B3）、补爬日志落盘（C2）、pregen 并发防护（A5）、预设清单 pin 测试（A5/B1）等 13 处。
> 执行前先读两个仓库根目录的 `HANDOFF.md` 获取最新状态。

## 执行状态（2026-07-18）

> 下方逐步 checkbox 保留为原始实施设计，不再逐项代表当前实况；以本节、两仓 `HANDOFF.md` 和代码测试为准。

- [x] **Stage A 核心完成**：A4、A5、A7、A8、A9、A10 已实现；后端 v1.14.0，329 tests，95.43% coverage。
- [x] **A1-A3 已完成（2026-07-18 用户授权后补做）**：真实页面 fixture 已存、步骤解析两处根因已修（JSON-LD 字符串形态 + 提前 return，见 `xiachufang-selector-notes.md`）、双通道补全落地（`app/crawler/steps_backfill.py` + `scripts/backfill_steps_via_llm.py` / `backfill_recipe_steps.py`，优先级 scraped>llm、幂等断点续跑、熔断/CAPTCHA 即停）；`recipes.steps_source` 列迁移随启动幂等执行。后端 v1.14.1，352 tests，95.64%。
- [ ] **A6 未执行**：可选双模型竞速跳过；保留 fast model 串行降级。
- [x] **Stage B 完成**：B1-B5 已实现；前端 v1.8.0，184 Jest tests，`build:weapp` 通过。
- [x] **对抗审查完成**：两仓最终 reviewer 均给出 Ship it；修复了请求竞态、缓存碰撞/原子写、偏好隔离、严格菜谱解析、流断连资源释放等 blocker。
- [x] **Stage C 后端已部署（2026-07-18）**：v1.14.1（SHA `0be5a2939030033ddac230bb2fa3bc5c48b411bb`）上线，`RECIPE_SCRAPE_ENABLED=true` 已开；实测预生成命中 0.09s、LLM 调用期间 health 0.03s（事件循环修复生效）；LLM 步骤补写生产执行中，真实补爬随后。
- [ ] **Stage C 前端待办**：`feature/zero-wait` 真机回归 → 合并 main → 微信提审。
- **审查后协议调整**：A8/B3 使用结构化 NDJSON `delta/complete/error`，不再使用 `@@JSON@@/@@ERR@@` sentinel；`/steps` 不复用缺少精确食材上下文的本地同名菜谱，只接受 exact-context cache 或带上下文生成。

**目标**：把"有啥做啥"（食材→AI 推荐菜品）的用户等待压到——预生成命中 **0 秒**、未命中 **2~5 秒**出菜名、步骤按需流式展开、全程无报错。（现状：LLM 路径已通过切 DeepSeek 官网直连降到 ~14 秒，但仍远超"无感"标准。）

**架构**：一次交付全部能力。后端——修复本地菜谱库 + 预生成缓存矩阵（命中即 0 秒）+ LLM 调用移出事件循环 + 双模型竞速（可选）+ 两段式端点（quick/steps）+ 步骤流式 + 分端点降级；前端——两段式交互（先菜名后步骤）+ 投机预取 + 流式渲染 + 静默降级。

**技术栈**：后端 FastAPI + SQLAlchemy 2 + SQLite(WAL) + APScheduler + openai SDK（走 OpenRouter 或 DeepSeek 官网，由 env 决定，代码不感知）；前端 Taro 4.1.11 + React 18 + TS（微信小程序）。

## 全局约束（每个任务都隐含遵守）

- 后端仓库 `C:\Users\goodb\daodichishayou-backend`（GitHub jasonxi89/daodichishayou-backend）；前端仓库 `C:\Users\goodb\WeChatProjects\daodichishayou`（jasonxi89/daodichishayou）
- **分支策略：两仓全程在 `feature/zero-wait` 分支开发**（一次性 ~20 个 commit，期间 main 必须保持可发布——中途线上要 hotfix 时不被半成品夹带）。后端在 Stage C 开头合并回 main（合并触发 CI 构建镜像）；前端在真机回归通过后合并（Stage C 末尾）
- 后端测试：`.venv/Scripts/python.exe -m pytest`，**CI 覆盖率门控 95%**，当前基线 261 pass / 96%
- 前端测试：`npx jest`，当前基线 159 pass；生产构建 `npm run build:weapp` 必须通过
- **git commit 不加 Co-Authored-By**；message 格式 `type: 中文祈使句`（feat/fix/refactor/test/chore）；照常小步提交（每任务 1-2 个 commit），但**中途不 bump 版本、不部署**
- **版本号只在最后 bump 一次**：后端 `app/config.py` APP_VERSION → `1.14.0`（Stage A 末尾）；前端 `package.json` version → `1.8.0`（Stage B 末尾）
- **LLM 调用一律读 env**（`OPENROUTER_API_KEY/MODEL/BASE_URL`，openai SDK），不硬编码网关。当前生产是 DeepSeek 官网直连（`https://api.deepseek.com`，模型 `deepseek-v4-pro`，名字无 `deepseek/` 前缀），但代码必须官网/OpenRouter 两者通吃
- **绝不把密钥写进代码/文档/commit**；NAS SSH 凭据在本机 `C:\Users\goodb\nas_ssh.py`
- NAS 部署流程（后端）：merge 到 main 并 `git push` → GitHub Actions 构建 `jasonxi89/daodichishayou-backend:<commit-SHA>` → 改 NAS compose image 为 SHA tag（**绝不用 latest**）→ `docker compose -p compose_config -f daodichishayou-backend.yaml -f zuitian.yaml pull api && ... up -d --force-recreate api`（**必须 -p compose_config 且两个 -f**，与 zuitian 共 project）→ `GET https://food.zuitian.ai/api/health` 验证版本号。compose 文件在 NAS `/zspace/applications/services/zdocker/config/compose_config/`，改文件需 SFTP 写 /tmp 再 sudo cp
- 生产数据库：容器内 `/app/data/food_trends.db`（volume 必须保留）；只读查询可 `docker exec compose_config-api-1 python - <<脚本`
- 微信小程序注意：真机与开发者工具行为不一致，改完前端必须 `build:weapp` + 开发者工具重新编译预览

## 一次性发版的回滚策略

- 部署前记录当前生产镜像 SHA：`61b313b312d7907a24e8a3ed3abfd3386a6662ef`（v1.13.1）。新版本出问题 → compose image 改回该 SHA 重新 up 即整体回滚
- 新表 `recommend_cache` 由 lifespan `create_all` 自动创建，回滚后旧代码不认识该表也不受影响（SQLite 多余表无害），无需数据迁移回退
- 双模型竞速由 env `OPENROUTER_FAST_MODEL` 控制（默认空=关闭），部署后可单独用 env 开关，不用回滚代码
- 前端小程序发版走微信审核，可独立回退到上一版；后端 quick/steps 是**新增**端点，老前端只调 `/api/recommend`（响应契约保持不变，见 A7 的缓存污染防护），所以后端先上、前端后上不会互相破坏

## 关键现状（2026-07-17 实测事实，执行前可复核）

- `POST /api/recommend`（`app/routers/recommend.py`）当前流程：无偏好且不允许额外购买时先查本地 recipes 表（`_search_local_recipes`，要求 `steps_json IS NOT NULL`），**命中≥1 条直接返回**（2026-07-17 已改，commit f0c0fc3）；否则调 LLM。LLM 路径在切 DeepSeek 官网直连后实测 ~14 秒（此前经 OpenRouter 时 44~110 秒）
- **⚠️ 现存线上问题（本计划 A5 修复）**：`recommend.py` 的三个端点（recommend / foods-by-category / bulk-foods-by-category）都是 **`async def` 且直接同步调 OpenAI client**——同步调用在 async 端点里**不会**进 FastAPI 线程池（那只对 `def` 端点成立），而是**阻塞整个事件循环**：uvicorn 单 worker 下，每次 LLM 调用的 ~14 秒内 health/trending/所有请求全部排队
- **致命缺陷**：生产库 656 条 recipes 的 `steps_json` 全部 NULL → 本地路径永远 miss。根因：`app/crawler/xiachufang.py::_parse_detail_page` 两条步骤解析路径全失效（JSON-LD 无 `recipeInstructions`；`.steps` DOM 选择器过时）。**配料解析正常**（636 条有 ingredients_text），说明详情页能拉到、只是步骤选择器不对
- 前端"有啥做啥"页 `src/pages/ingredient/ingredient.tsx`（471 行）：预设食材 30 个（第 6-11 行 `COMMON_INGREDIENTS`：蔬菜12 肉类8 水产蛋奶5 主食5）+ 自定义输入；`handleRecommend`（~220 行）与 `handleLoadMore`（~255 行）直接 `Taro.request` POST /api/recommend，timeout 120000；前端请求恒发 `count: 3`
- LLM 网关封装：`recommend.py` 内直接 `OpenAI(base_url=OPENROUTER_BASE_URL, api_key=..., timeout=LLM_TIMEOUT_SECONDS)`
- 测试 fixtures 模式：后端 `tests/conftest.py` 提供 in-memory `db` fixture + `client` fixture（dependency override）；LLM 一律 `patch("app.routers.recommend.OpenAI")` mock

---

# Stage A — 后端全量改造（最终 v1.14.0，全程在 feature/zero-wait 分支）

## Task A1: 摸清下厨房详情页真实结构（调查任务，产出 fixture）

**Files:**
- Create: `tests/fixtures/xiachufang_detail_2026.html`（真实详情页存档）
- Create: `docs/plans/xiachufang-selector-notes.md`（选择器结论，≤20 行）

这是调查任务，无法预写代码，但产出物明确可验收。

- [ ] **Step 1**: 用仓库现有的抓取通道拉 3 个真实详情页存档（沿用 `xiachufang.py` 里的 headers/UA/间隔逻辑，10 秒间隔，别裸 curl 触发反爬）：
```python
# 临时脚本 scratch_fetch.py（用完删除，不 commit）
import time
from app.crawler.xiachufang import XiachufangScraper
s = XiachufangScraper()
# 3 个 URL 从生产库取: docker exec compose_config-api-1 python -c
#   "import sqlite3; print(sqlite3.connect('/app/data/food_trends.db').execute(
#    'SELECT source_url FROM recipes WHERE ingredients_text IS NOT NULL LIMIT 3').fetchall())"
for i, url in enumerate(URLS):
    resp = s._client.get(url)  # scraper 内部是 httpx.Client（xiachufang.py:210），自带 headers
    open(f"detail_{i}.html", "w", encoding="utf-8").write(resp.text)
    time.sleep(10)
```
- [ ] **Step 2**: 人工检查 HTML：① `<script type="application/ld+json">` 里有没有 `recipeInstructions`（大概率没有，确认之）；② 步骤所在 DOM 的真实 class/结构（2026 年页面步骤区块可能是 `<div class="steps">` 的变体、`<li>` 内 `<p class="text">` 的变体，或整段搬进了 JS 渲染——如果是 JS 渲染需要在 HTML 里找内嵌 JSON 数据块）。把结论写进 `xiachufang-selector-notes.md`
- [ ] **Step 3**: 挑 1 个含完整步骤的页面存为 `tests/fixtures/xiachufang_detail_2026.html`（脱敏无需，公开网页）。若 3 个页面全是 CAPTCHA/风控页：**停下**，在 notes 里记录风控特征，改用"曲线方案"——LLM 离线补写步骤（见 Task A3 Step 4 的备选路径），Task A2 跳过
- [ ] **Step 4**: Commit：`git add tests/fixtures/ docs/plans/xiachufang-selector-notes.md && git commit -m "test: 添加下厨房详情页 2026 真实结构 fixture"`

## Task A2: 修 `_parse_detail_page` 步骤解析

**Files:**
- Modify: `app/crawler/xiachufang.py::_parse_detail_page`（约 137-205 行）
- Test: `tests/crawler/test_xiachufang.py`

**Interfaces:**
- Produces: `_parse_detail_page(html, item) -> RecipeItem`，`item.steps` 为 `[{"text": "步骤文字"}, ...]`（下游 `_save_recipes` 已按此格式落库，勿改形状）

- [ ] **Step 1**: 写失败测试（用 Task A1 的 fixture）：
```python
def test_parse_detail_page_extracts_steps_from_2026_layout():
    html = (FIXTURES / "xiachufang_detail_2026.html").read_text(encoding="utf-8")
    item = RecipeItem(name="测试菜", source_url="https://www.xiachufang.com/recipe/x/")
    result = _parse_detail_page(html, item)
    assert result.steps, "2026 版详情页应能解析出步骤"
    assert len(result.steps) >= 3
    assert all(s.get("text") for s in result.steps)
```
- [ ] **Step 2**: 跑测试确认 FAIL（`.venv/Scripts/python.exe -m pytest tests/crawler/test_xiachufang.py -k 2026 -v`）
- [ ] **Step 3**: 按 Task A1 结论实现新选择器。保留现有 JSON-LD 和旧 `.steps` 逻辑作为前置 fallback 链（老 fixture 的测试必须继续过），新逻辑追加在后
- [ ] **Step 4**: 全量跑 `tests/crawler/test_xiachufang.py` 确认全绿（旧 12 个 + 新 1 个）
- [ ] **Step 5**: Commit：`fix: 适配下厨房 2026 详情页步骤解析`

## Task A3: 补爬存量 656 条菜谱的步骤（脚本先写好，生产执行在 Stage C）

**Files:**
- Create: `scripts/backfill_recipe_steps.py`
- Test: `tests/test_backfill_steps.py`

**Interfaces:**
- Produces: CLI 脚本，幂等可断点续跑：只处理 `steps_json IS NULL AND source_url IS NOT NULL` 的行，每条间隔 10 秒，连续 5 次风控/失败自动停

- [ ] **Step 1**: 写失败测试（mock 抓取函数，验证：只挑 steps 为 NULL 的行、成功后写库、连续失败熔断）：
```python
def test_backfill_only_targets_null_steps(db): ...
def test_backfill_stops_after_5_consecutive_failures(db): ...
def test_backfill_is_resumable(db): ...  # 跑一半再跑，不重复处理已完成行
```
- [ ] **Step 2**: 实现脚本：复用 `XiachufangScraper` 的 fetch + `_parse_detail_page`，argparse 支持 `--limit N --dry-run`，日志打进度 `[123/656]`
- [ ] **Step 3**: 测试全绿后 commit：`feat: 菜谱步骤补爬脚本（幂等+熔断）`
- [ ] **Step 4**: **备选路径**（Task A1 判定风控走不通时）：写 `scripts/backfill_steps_via_llm.py`，对每条菜谱用 LLM 按菜名+配料生成步骤，落库时 `list_source` 标记 `llm_backfill` 以便区分数据来源。菜谱名/配料/评分仍是真实的，只有步骤是 AI 补写。生产执行统一放 Stage C Task C2

## Task A3b（可选，不阻塞后续）: 菜谱库扩容

现有 656 条来自下厨房 honor 榜。若 Task A2/A3 顺利（真实抓取可行），把 `XiachufangScraper.scrape()` 的列表页范围扩到按食物词典关键词搜索页（`app/crawler/food_keywords.py` 的 FOOD_NAMES 取 Top100），目标库存 2000+。同样 TDD：先给新列表页类型写 fixture 测试。工作量大、收益是本地命中率，时间紧可跳过——预生成矩阵已保证预设食材组合全覆盖。

## Task A4: 预生成缓存表 + recommend 命中路径

**Files:**
- Modify: `app/models.py`（新表）、`app/main.py`（lifespan 建表已有 create_all 自动覆盖）
- Modify: `app/routers/recommend.py`
- Test: `tests/routers/test_recommend.py`、`tests/test_models.py`

**Interfaces:**
- Produces: `RecommendCache` 模型；`make_cache_key(ingredients: list[str], count: int) -> str`（sorted + 全小写去空格 + "|".join + `#c{count}` 后缀——**key 必须含 count**：pregen 固定生成 count=3，若 key 不含 count，count=5 的请求命中后只拿到 3 道，形成隐式契约）；recommend 端点在 `is_local_eligible and not exclude_dishes` 时优先查缓存
- **查询优先级（有意决策，非实现顺序的偶然）**：缓存 → 本地 recipes → LLM。理由：缓存 payload 字段完整（summary/difficulty/cook_time 齐全）且恰好 count 道多样菜；本地真实菜谱转换后 summary 是占位文案、菜品多样性靠 LIKE 撞——它继续服务未预生成的组合、steps 端点查询和降级层。此顺序若后续想反转（真实菜谱优先），只动端点里两个代码块的先后，不影响其他任务

- [ ] **Step 1**: models.py 新增（注意本项目踩过的坑：**DateTime 列比较必须用 datetime，不能用裸 date**）：
```python
class RecommendCache(Base):
    """食材组合 → 推荐结果预生成缓存。"""
    __tablename__ = "recommend_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    payload: Mapped[str] = mapped_column(Text)  # IngredientRecommendResponse 的 JSON（必须是全量 payload，见 A7）
    model: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
```
- [ ] **Step 2**: 失败测试（模型 + 端点命中/未命中/过期三态 + key 归一化 `["番茄","鸡蛋"]==["鸡蛋"," 番茄"]` + **count 不同 key 不同**）
- [ ] **Step 3**: recommend.py 实现：`make_cache_key`；端点开头（本地搜索之前）查缓存，命中且未过期 → 直接 `IngredientRecommendResponse(**json.loads(payload))`；LLM 成功生成**全量结果**后 upsert 缓存（TTL 7 天）。**注意 upsert 用 `datetime.now(timezone.utc)` 比较 expires_at**（可参照同文件 `foods_by_category` 的 `FoodsCategoryCache` 同款模式）
- [ ] **Step 4**: 全量测试绿 → commit：`feat: recommend 结果缓存（食材组合+count key，7 天 TTL）`

## Task A5: LLM 调用移出事件循环 + 抽取生成函数 + 预生成跑批

**Files:**
- Create: `app/crawler/pregen.py`
- Modify: `app/routers/recommend.py`（抽取重构 + to_thread）、`app/crawler/scheduler.py`（注册 cron job）、`app/config.py`（开关+预算 env）
- Test: `tests/crawler/test_pregen.py`

**Interfaces:**
- Consumes: Task A4 的 `make_cache_key` / `RecommendCache`
- Produces:
  - `generate_dishes_via_llm(ingredients, count, preferences, allow_extra, exclude) -> list[RecommendedDish]`——从 recommend 端点抽取的可复用**同步**函数（A6/A7/A9/pregen 都复用）
  - **事件循环阻塞修复（修的是现存线上问题，见"关键现状"）**：所有 async 端点里的 LLM 调用一律 `await asyncio.to_thread(generate_dishes_via_llm, ...)`；`foods_by_category` / `bulk_foods_by_category` 的同步 LLM 调用同样包 `asyncio.to_thread`（顺手修，行为零变化）
  - `run_pregeneration(db, budget: int) -> int`（返回本次生成条数）；`PRESET_INGREDIENTS`（30 个，与前端 `src/pages/ingredient/ingredient.tsx` COMMON_INGREDIENTS 手动同步，文件头注释注明同步来源，**并加 pin 测试**：断言清单恰好是这 30 项，防止无意识改动漂移——前端 B1 有对应的 pin 测试）；APScheduler cron 每天 03:30 CST

- [ ] **Step 1**: 重构抽取 `generate_dishes_via_llm`（纯搬代码）+ 三个端点的 LLM 调用全部改 `asyncio.to_thread` 包裹。**验证不阻塞**：写一个测试用 mock LLM sleep 0.5s + 并发请求 `/api/health`，断言 health 在 LLM 完成前返回。全量测试绿，单独 commit：`fix: LLM 调用移出事件循环并抽取生成函数`
- [ ] **Step 2**: 失败测试：组合枚举（30 单 + 435 对 = 465）、budget 截断、已有未过期缓存跳过、单条失败不中断整批、PRESET_INGREDIENTS pin 测试
- [ ] **Step 3**: 实现 `pregen.py`：
```python
PRESET_INGREDIENTS = ["番茄", "土豆", ...]  # 30 个，同步自前端 ingredient.tsx（对应 pin 测试勿删）

def iter_preset_combos():
    yield from ([i] for i in PRESET_INGREDIENTS)
    yield from ([a, b] for a, b in itertools.combinations(PRESET_INGREDIENTS, 2))

def run_pregeneration(db, budget: int = PREGEN_DAILY_BUDGET) -> int:
    # 遍历 combos，跳过缓存未过期的；调 generate_dishes_via_llm(count=3)（与前端恒发的 count=3 一致）
    # 成功 upsert RecommendCache（TTL 7 天错峰：TTL = 7d + random 0-24h 防止同日全体过期）
    # 达到 budget 或全部覆盖即停；单条异常 log 后继续
```
- [ ] **Step 4**: config.py 加 `PREGEN_ENABLED`（默认 true）`PREGEN_DAILY_BUDGET`（默认 120，即全矩阵约 4 天铺满、之后每天只刷过期的）；scheduler.py 注册 cron（`hour=3, minute=30`），**必须 `max_instances=1, coalesce=True, misfire_grace_time=3600`**——单次跑批 120 条 × ~14s ≈ 28 分钟，不设防会与自身/其他 job 叠跑；**跑批必须 try/except 全包裹，绝不能把异常抛给 APScheduler**（本项目 7 月刚发生过爬虫 job 因异常连崩 3 天的事故）
- [ ] **Step 5**: 测试全绿 → commit：`feat: 食材组合预生成跑批（每日 03:30，预算制）`

## Task A6: 双模型竞速（可选——收益已缩水，时间紧可跳过）

> ⚠️ 2026-07-18 注：本任务设计时主模型经 OpenRouter 需 44~110s，竞速收益大；现已切 DeepSeek 官网直连、pro 实测 ~14s，竞速的边际收益明显变小。保留本任务因其默认 env 关闭、零风险，但**优先级降为最低，可跳过不影响其余任务**。

**Files:**
- Modify: `app/config.py`（`OPENROUTER_FAST_MODEL` env，默认空=不竞速）、`app/routers/recommend.py`
- Test: `tests/routers/test_recommend.py`

**Interfaces:**
- Produces: 当 `OPENROUTER_FAST_MODEL` 非空且请求走 LLM 路径时，并发调 fast+主模型，**先完成者返回给用户**；慢的那路完成后结果写入 RecommendCache（下次命中的是质量版）。实现：两路都是 `asyncio.to_thread(generate_dishes_via_llm, ...)`（A5 已铺好）+ `asyncio.wait(FIRST_COMPLETED)`

- [ ] **Step 1**: 失败测试：mock 两个模型一快一慢 → 返回快者结果；慢者结果落缓存；fast env 为空时行为与现在完全一致（回归保护）
- [ ] **Step 2**: 实现（注意：败者写缓存要用独立 db session，请求级 session 在响应后已关闭；参考 `scheduler.py` 的 `SessionLocal()` 用法）
- [ ] **Step 3**: 测试全绿 → commit：`feat: recommend 双模型竞速，先到先得+败者入缓存`
- [ ] **Step 4**: NAS compose 加 env `OPENROUTER_FAST_MODEL`（部署时一并加；官网直连渠道填 `deepseek-v4-flash`，OpenRouter 渠道填 `deepseek/deepseek-v4-flash`；不想启用就不加）

## Task A7: 两段式端点 quick/steps

**Files:**
- Modify: `app/routers/recommend.py`、`app/schemas.py`
- Test: `tests/routers/test_recommend.py`

**Interfaces:**
- Produces:
  - `POST /api/recommend/quick`：body 同 /api/recommend，返回 `{"dishes": [{"name","summary","difficulty","cook_time"}], "input_ingredients": [...]}`（无 ingredients/steps；prompt 明确只要菜名+简介，max_tokens=800，比全量快 3-5 倍）。缓存命中时直接从缓存裁剪字段返回
  - `POST /api/recommend/steps`：body `{"dish_name": str, "ingredients": [str]}`，返回单道菜完整 `RecommendedDish`。先查 RecommendCache（按 payload 内菜名匹配，如 `payload LIKE '%"name": "鱼香肉丝"%'`）与本地 recipes 表（名字模糊匹配），miss 才调 LLM
- **⚠️ 缓存污染防护**：quick 路径的 LLM 结果（无 steps/ingredients 的残缺 payload）**绝不写入共享 RecommendCache**——否则老端点 `/api/recommend` 缓存命中后会把残缺菜品返回给线上旧版前端（旧前端在小程序审核期间会存活很久）。共享缓存只允许全量 payload 进入；若想缓存 quick 结果，用独立前缀 key `quick|...`（可选，不做也行）
- 老端点 `/api/recommend` **保持响应契约不动**（线上旧版前端还在调它；quick/steps 是新增）

- [ ] **Step 1**: 失败测试（两端点各覆盖：正常/缓存命中/LLM 异常 502/参数校验 422；**外加：quick 的 LLM 结果不落共享缓存**——quick 调用后用相同 ingredients 打 /api/recommend，断言不会命中残缺缓存）
- [ ] **Step 2**: 实现 + 测试绿 → commit：`feat: 两段式推荐端点 quick/steps`

## Task A8: 步骤流式输出（后端部分）

**Files:**
- Modify: `app/routers/recommend.py`（steps 端点加 `?stream=1` 支持）
- Test: `tests/routers/test_recommend.py`

**Interfaces:**
- Produces: `POST /api/recommend/steps?stream=1` → `StreamingResponse`（`text/plain; charset=utf-8`，直接转发 LLM delta 文本，结束后最后一行输出 `\n@@JSON@@{完整RecommendedDish JSON}` 供前端落最终结构）；LLM 中途异常时输出错误标记行 `@@ERR@@`。前端对接在 Task B3
- **实现约束（事件循环）**：openai SDK `stream=True` 得到的是**同步迭代器**——把它包在**同步生成器**里交给 `StreamingResponse`（Starlette 对同步迭代器自动走线程池迭代，不阻塞事件循环）；**不要**写成 async 生成器里同步 `for chunk in ...`（那会把 A5 修掉的阻塞问题原样带回来）

- [ ] **Step 1**: 失败测试：`stream=1` 返回 StreamingResponse、chunk 拼接后含 @@JSON@@ 结构、LLM 中途异常时输出 `@@ERR@@`
- [ ] **Step 2**: 实现（同步生成器迭代 delta）→ 测试绿 → commit：`feat: 步骤生成流式输出`

## Task A9: 后端降级链（按端点形状分两条）

**Files:**
- Modify: `app/routers/recommend.py`
- Test: `tests/routers/test_recommend.py`

**Interfaces:**
- Produces 两条形状不同的链（**列表形的链只适用于返回菜品列表的端点，steps 返回单菜步骤，不能照搬**）：
  - **列表链（/api/recommend 与 /api/recommend/quick 共用）**：LLM 不可用（OpenAIError / 超时 / **API key 未配置**）时依次：① 配了 fast 模型则 fast 重试 1 次（未配则跳过此级）→ ② RecommendCache 里任意包含首个食材的旧缓存（`cache_key LIKE`，**必须按 exclude_dishes 过滤掉已展示的菜**，别把用户看过的再端上来）→ ③ 本地 recipes 表（此时允许无 steps，摘要写"点开看详细做法"由 steps 端点兜底；注意 `_recipe_to_dish` 目前直接 `json.loads(steps_json)`，NULL 会炸，需容错）→ ④ 全部失败才 5xx
  - **单菜链（/api/recommend/steps）**：LLM 不可用时依次：① 本地 recipes 表名字模糊匹配 → ② 5xx（前端 B3/B4 兜底提示重试）。流式模式中途异常 → `@@ERR@@` 标记（响应已 200 无法改状态码，前端识别标记回退）
- **key 未配置的行为变更**：现有三个端点开头的 `if not OPENROUTER_API_KEY: raise 500` **移除**，改为进入各自降级链的非 LLM 层（链全失败才 5xx）。受影响的旧测试同步更新——这正是验收标准 #3"kill 掉 LLM env 前端仍可用"的后端半边

- [ ] **Step 1**: 失败测试（列表链）：主模型炸 → fast 兜住；全炸 → 旧缓存兜住（且 exclude_dishes 被过滤）；真全炸 → 5xx；**key 为空 → 直接进缓存/本地层而非顶层 500**。（单菜链）：LLM 炸 → 本地菜谱兜住；无匹配 → 5xx
- [ ] **Step 2**: 实现 → 全绿 → commit：`feat: recommend 分端点多级降级`

## Task A10: 后端收尾

- [ ] **Step 1**: 全量测试 + 覆盖率：`.venv/Scripts/python.exe -m pytest --cov=app`，确认 ≥95%、全绿
- [ ] **Step 2**: `app/config.py` APP_VERSION → `1.14.0`，commit：`chore: 版本号升至 1.14.0`（**只 commit 不合并不部署**，合并部署统一在 Stage C）

---

# Stage B — 前端全量改造（最终 v1.8.0，全程在 feature/zero-wait 分支）

> B1-B4 都集中改 `src/pages/ingredient/ingredient.tsx`（当前 471 行，B 阶段结束预计 700+ 行——**违反 300 行规范，拆分是 B1 的正式步骤，不是建议**）。
> jest 约定（本项目历史坑）：**不用 fake timers**，用真实定时器 + `waitFor({timeout})`；`useDidShow` 禁用——历史上它会让整个测试文件静默崩掉。

## Task B1: 组件拆分 + 两段式交互改造

**Files:**
- Create: `src/pages/ingredient/DishCard.tsx`（菜品卡片子组件）
- Modify: `src/pages/ingredient/ingredient.tsx`（handleRecommend/handleLoadMore 改调 `/api/recommend/quick`；卡片点开触发 `/api/recommend/steps`）
- Test: `src/__tests__/pages/ingredient.test.tsx`

- [ ] **Step 0**: **先拆组件再改逻辑**：把菜品卡片渲染块（现 ingredient.tsx 409-454 行）抽成 `DishCard` 纯展示组件（props: dish/expanded/onToggle），行为零变化，jest 全绿，单独 commit：`refactor: 抽取 DishCard 组件`。**同时加 COMMON_INGREDIENTS pin 测试**（断言 30 项清单内容，注释注明与后端 `app/crawler/pregen.py::PRESET_INGREDIENTS` 手动同步——两边 pin 测试互为守护，改清单必须两仓一起改）
- [ ] **Step 1**: 失败测试：点推荐 → 渲染 3 张菜名卡（无步骤）；点某卡 → 该卡 loading → 步骤出现；再点收起；「加载更多」同样走 quick
- [ ] **Step 2**: 实现：`handleRecommend`/`handleLoadMore` 改调 quick（timeout 可降到 30000，quick 端点 2-5s 足够）；卡片展开时若该菜无 steps → 调 steps 端点补全并缓存进 state（同一张卡二次展开不重复请求）→ jest 绿 → commit：`feat: 两段式交互——秒出菜名,点开看步骤`
- [ ] **Step 3**: 等待文案阶段化（简单实现：loading 态每 3 秒轮换 `正在翻 2 万本菜谱… / 大厨思考中… / 快好了快好了`）→ commit：`feat: 等待文案阶段化`

## Task B2: 投机预取（直接预取 quick 端点）

> 原三阶段计划里预取先对接 /api/recommend、后期再改 quick，一次性完成版直接预取 quick，省掉返工。

**Files:**
- Modify: `src/pages/ingredient/ingredient.tsx`
- Test: `src/__tests__/pages/ingredient.test.tsx`

**Interfaces:**
- Produces: 用户勾选食材停顿 1 秒后后台预发 quick 请求；点"开始推荐"时若预取 key（sorted ingredients + preference + allowExtra 序列化）与当前选择一致 → 复用预取 promise，否则正常请求。预取失败静默丢弃，不 toast

- [ ] **Step 1**: 失败测试：
```tsx
it('prefetches recommendation 1s after ingredient selection settles', async () => {
  mockRequest.mockResolvedValue({ statusCode: 200, data: { dishes: [] } })
  render(<IngredientPage />)
  fireEvent.click(screen.getByText('番茄'))
  await waitFor(() => expect(mockRequest).toHaveBeenCalledTimes(1), { timeout: 2500 })
})
it('reuses prefetched result when clicking 开始推荐 with same selection', async () => { ... })
it('discards prefetch when selection changed before clicking', async () => { ... })
```
- [ ] **Step 2**: 实现：`useRef` 存 `{key, promise}` + `useEffect` 对 `[selected, preference, allowExtra]` 设 1000ms debounce；`handleRecommend` 先比 key
- [ ] **Step 3**: 全量 jest 绿 → commit：`feat: 选食材时投机预取推荐结果`

## Task B3: 步骤流式渲染 + 自动回退

**Files:**
- Modify: `src/services/api.ts`、`src/pages/ingredient/ingredient.tsx`、`src/pages/ingredient/DishCard.tsx`
- Test: `src/__tests__/pages/ingredient.test.tsx`

**Interfaces:**
- Produces: 卡片展开拉取 steps 时走 `POST /api/recommend/steps?stream=1`：`Taro.request({enableChunked: true})` + `requestTask.onChunkReceived` 增量渲染，`@@JSON@@` 到达后替换为结构化展示；**onChunkReceived 不可用或首 chunk 3 秒未到 → 自动回退非流式**（微信基础库/真机兼容性兜底；分块回调拿到的是 ArrayBuffer，需 TextDecoder 解码且注意 UTF-8 断字——按字节缓冲、解码失败的尾部字节留到下一 chunk）
- **回退必须先 `requestTask.abort()` 终止流式请求再发非流式请求**——否则两路响应竞争，可能重复渲染/错序；`@@ERR@@` 标记到达同样走 abort+回退

- [ ] **Step 1**: 失败测试：流式路径渲染增量文本、@@JSON@@ 到达后替换为结构化展示、超时回退路径正常**且原请求被 abort**、@@ERR@@ 触发 abort+回退
- [ ] **Step 2**: 实现 → jest 绿 + `npm run build:weapp` 过 → commit：`feat: 步骤流式渲染+自动回退`

## Task B4: 静默降级 + 重试（永不报错）

**Files:**
- Modify: `src/pages/ingredient/ingredient.tsx`
- Test: `src/__tests__/pages/ingredient.test.tsx`

**Interfaces:**
- Produces: 502/超时/网络错误一律不弹「网络异常」toast，改为展示按食材匹配的本地硬编码菜谱（`src/data/` 已有 19 道）+ 温和文案「网络开小差，先看看这些经典搭配」，并附「重试」按钮

- [ ] **Step 1**: 失败测试：502 → 渲染本地推荐 + 重试按钮，**不出现「网络异常，请重试」toast**；点重试重新请求
- [ ] **Step 2**: 实现 → 全绿 → commit：`feat: 失败静默降级到本地推荐`

## Task B5: 前端收尾

- [ ] **Step 1**: 全量 `npx jest` 绿（≥159+新增）+ `npm run build:weapp` 过
- [ ] **Step 2**: `package.json` version → `1.8.0`，commit：`chore: 版本号升至 1.8.0`（分支上 commit，合并在 Stage C 真机回归后）

---

# Stage C — 合并、部署与生产验证（一次性）

## Task C1: 后端合并 + 部署

- [ ] **Step 1**: 后端 `feature/zero-wait` 合并回 main（确保合并前分支上全量测试绿）→ `git push` → 等 GitHub Actions 绿
- [ ] **Step 2**: 按全局约束的 NAS 部署流程上线（记得先记下回滚 SHA，见"回滚策略"）
- [ ] **Step 3**: `GET https://food.zuitian.ai/api/health` 确认 `1.14.0`
- [ ] **Step 4**: 若做了 Task A6 且要启用竞速：compose 加 `OPENROUTER_FAST_MODEL` env 后 recreate

## Task C2: 生产补爬

- [ ] **Step 1**: NAS 容器里跑（**必须重定向日志落盘**，2 小时后台任务不能黑盒）：
  `docker exec -d compose_config-api-1 sh -c 'python scripts/backfill_recipe_steps.py > /app/data/backfill.log 2>&1'`
  （约 2 小时，656 条 × 10s；LLM 备选路径则跑 `backfill_steps_via_llm.py`。中途监控：`docker exec compose_config-api-1 tail -5 /app/data/backfill.log`）
- [ ] **Step 2**: SQL 验收：`SELECT COUNT(*) FROM recipes WHERE steps_json IS NOT NULL` 目标 ≥500
- [ ] **Step 3**: 在 HANDOFF.md 当前状态里记录补爬结果数字

## Task C3: 预生成首跑

- [ ] **Step 1**: 手动触发一轮验证（容器内 `python -c "from app.crawler.pregen import ...; run_pregeneration(db, budget=5)"`）
- [ ] **Step 2**: 实测：POST /api/recommend 送一个已预生成组合（如 `["番茄","鸡蛋"]`、count=3）应 <1 秒返回；quick 端点同样验证
- [ ] **Step 3**: **并发不阻塞验证**（A5 修复的验收）：发一个未预生成组合触发 LLM 路径，同时连发 `GET /api/health`，health 必须 <1 秒返回而非排队 ~14 秒
- [ ] **Step 4**: 次日检查 03:30 跑批日志（`docker logs compose_config-api-1 | grep pregen`），无异常堆栈

## Task C4: 前端真机回归（必做）

> 开发者工具的 chunked 行为与真机不一致是本项目已知坑型，流式必须真机验证。

- [ ] **Step 1**: 开发者工具重新编译 + 预览版真机实测：预生成命中组合秒出、冷门自定义食材 2-5 秒出菜名卡、点开步骤流式逐字出现、不支持机型回退无感
- [ ] **Step 2**: 飞行模式断网 → 仍有本地推荐展示 + 重试按钮，无报错 toast
- [ ] **Step 3**: 投机预取生效验证（Network 面板看请求时机：勾食材 1 秒后有 quick 请求，点推荐秒回）
- [ ] **Step 4**: 回归通过后前端 `feature/zero-wait` 合并回 main 并 push

## Task C5: 文档收尾

- [ ] **Step 1**: 更新两仓库 HANDOFF.md 与 memory 的状态段（版本、部署 SHA、补爬数字、新端点清单）
- [ ] **Step 2**: 把本计划文件标记为已完成（文件头加 `> ✅ 已于 YYYY-MM-DD 完成` 行）并 commit

---

## 完成定义（整体验收）

1. 预设食材任选 1-2 个 → 点推荐 → **<1 秒**出结果（预生成命中）
2. 冷门自定义食材 → **<5 秒**出 3 张菜名卡，点开步骤流式展开
3. 后端 kill 掉 LLM env（模拟故障）→ 后端走降级链仍出推荐（A9），前端即便收到 5xx 也静默降级（B4），全程无报错 toast
4. **LLM 调用期间后端不失去响应**：触发 LLM 路径的同时请求 /api/health，<1 秒返回（A5 修复的验收）
5. 后端测试 ≥261+新增全绿、覆盖率 ≥95%；前端 ≥159+新增全绿
6. 每晚 03:30 跑批日志正常（`docker logs compose_config-api-1 | grep pregen`），无异常堆栈
7. 生产 health 返回 `1.14.0`；前端小程序版本 `1.8.0`；两仓 main 干净（feature/zero-wait 已合并）
