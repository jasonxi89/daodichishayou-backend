# 「到底吃啥哟」零等待体验改造 · 实施计划 (P1-P3)

> **给执行 agent**：本计划自包含，假设你对本项目零上下文。按 checkbox 顺序逐任务执行；若你的环境有 superpowers:subagent-driven-development / executing-plans skill 可用其驱动，没有就按顺序人肉执行。**每个任务先写失败测试再实现**。
> 计划撰写: 2026-07-17（Claude Code 会话）。执行前先读两个仓库根目录的 `HANDOFF.md` 获取最新状态。

**目标**：把"有啥做啥"（食材→AI 推荐菜品）的用户等待从当前 20~110 秒压到——预生成命中 0 秒、未命中 2~5 秒出菜名、步骤按需流式展开、全程无报错。

**架构**：三阶段递进。P1 修复本地菜谱库并建立预生成缓存矩阵（命中即 0 秒）；P2 前端投机预取 + 后端双模型竞速（残余路径也接近无感）；P3 交互改为两段式（先菜名后步骤）+ 步骤流式 + 全链路静默降级。

**技术栈**：后端 FastAPI + SQLAlchemy 2 + SQLite(WAL) + APScheduler + openai SDK（走 OpenRouter 或 DeepSeek 官网，由 env 决定，代码不感知）；前端 Taro 4.1.11 + React 18 + TS（微信小程序）。

## 全局约束（每个任务都隐含遵守）

- 后端仓库 `C:\Users\goodb\daodichishayou-backend`（GitHub jasonxi89/daodichishayou-backend）；前端仓库 `C:\Users\goodb\WeChatProjects\daodichishayou`（jasonxi89/daodichishayou）
- 后端测试：`.venv/Scripts/python.exe -m pytest`，**CI 覆盖率门控 95%**，当前基线 261 pass / 96%
- 前端测试：`npx jest`，当前基线 159 pass；生产构建 `npm run build:weapp` 必须通过
- **git commit 不加 Co-Authored-By**；message 格式 `type: 中文祈使句`（feat/fix/refactor/test/chore）
- **每阶段完成必须 bump 版本**：后端 `app/config.py` APP_VERSION（P1→1.14.0, P2→1.15.0, P3→1.16.0）；前端 `package.json` version（P2→1.8.0, P3→1.9.0）
- **LLM 调用一律读 env**（`OPENROUTER_API_KEY/MODEL/BASE_URL`，openai SDK），不硬编码网关。渠道可能是 OpenRouter 也可能是 DeepSeek 官网（`https://api.deepseek.com`，模型名无 `deepseek/` 前缀），代码必须两者通吃
- **绝不把密钥写进代码/文档/commit**；NAS SSH 凭据在本机 `C:\Users\goodb\nas_ssh.py`
- NAS 部署流程（后端）：`git push` → GitHub Actions 构建 `jasonxi89/daodichishayou-backend:<commit-SHA>` → 改 NAS compose image 为 SHA tag（**绝不用 latest**）→ `docker compose -p compose_config -f daodichishayou-backend.yaml -f zuitian.yaml pull api && ... up -d --force-recreate api`（**必须 -p compose_config 且两个 -f**，与 zuitian 共 project）→ `GET https://food.zuitian.ai/api/health` 验证版本号。compose 文件在 NAS `/zspace/applications/services/zdocker/config/compose_config/`，改文件需 SFTP 写 /tmp 再 sudo cp
- 生产数据库：容器内 `/app/data/food_trends.db`（volume 必须保留）；只读查询可 `docker exec compose_config-api-1 python - <<脚本`
- 微信小程序注意：真机与开发者工具行为不一致，改完前端必须 `build:weapp` + 开发者工具重新编译预览

## 关键现状（2026-07-17 实测事实，执行前可复核）

- `POST /api/recommend`（`app/routers/recommend.py`）当前流程：无偏好且不允许额外购买时先查本地 recipes 表（`_search_local_recipes`，要求 `steps_json IS NOT NULL`），**命中≥1 条直接返回**（2026-07-17 已改，commit f0c0fc3）；否则调 LLM，实测 20~110 秒
- **致命缺陷**：生产库 656 条 recipes 的 `steps_json` 全部 NULL → 本地路径永远 miss。根因：`app/crawler/xiachufang.py::_parse_detail_page` 两条步骤解析路径全失效（JSON-LD 无 `recipeInstructions`；`.steps` DOM 选择器过时）。**配料解析正常**（636 条有 ingredients_text），说明详情页能拉到、只是步骤选择器不对
- 前端"有啥做啥"页 `src/pages/ingredient/ingredient.tsx`：预设食材 30 个（第 5-11 行 `COMMON_INGREDIENTS`：蔬菜12 肉类8 水产蛋奶5 主食5）+ 自定义输入；`handleRecommend`（~220 行）与 `handleLoadMore`（~255 行）直接 `Taro.request` POST /api/recommend，timeout 120000
- LLM 网关封装：`recommend.py` 内直接 `OpenAI(base_url=OPENROUTER_BASE_URL, api_key=..., timeout=LLM_TIMEOUT_SECONDS)`，同步调用跑在 FastAPI 线程池里
- 测试 fixtures 模式：后端 `tests/conftest.py` 提供 in-memory `db` fixture + `client` fixture（dependency override）；LLM 一律 `patch("app.routers.recommend.OpenAI")` mock

---

# Phase 1 — 本地秒回复活 + 预生成矩阵（后端 v1.14.0）

## Task 1.1: 摸清下厨房详情页真实结构（调查任务，产出 fixture）

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
- [ ] **Step 3**: 挑 1 个含完整步骤的页面存为 `tests/fixtures/xiachufang_detail_2026.html`（脱敏无需，公开网页）。若 3 个页面全是 CAPTCHA/风控页：**停下**，在 notes 里记录风控特征，改用"曲线方案"——LLM 离线补写步骤（见 Task 1.3 Step 4 的备选路径），Task 1.2 跳过
- [ ] **Step 4**: Commit：`git add tests/fixtures/ docs/plans/xiachufang-selector-notes.md && git commit -m "test: 添加下厨房详情页 2026 真实结构 fixture"`

## Task 1.2: 修 `_parse_detail_page` 步骤解析

**Files:**
- Modify: `app/crawler/xiachufang.py::_parse_detail_page`（约 137-205 行）
- Test: `tests/crawler/test_xiachufang.py`

**Interfaces:**
- Produces: `_parse_detail_page(html, item) -> RecipeItem`，`item.steps` 为 `[{"text": "步骤文字"}, ...]`（下游 `_save_recipes` 已按此格式落库，勿改形状）

- [ ] **Step 1**: 写失败测试（用 Task 1.1 的 fixture）：
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
- [ ] **Step 3**: 按 Task 1.1 结论实现新选择器。保留现有 JSON-LD 和旧 `.steps` 逻辑作为前置 fallback 链（老 fixture 的测试必须继续过），新逻辑追加在后
- [ ] **Step 4**: 全量跑 `tests/crawler/test_xiachufang.py` 确认全绿（旧 12 个 + 新 1 个）
- [ ] **Step 5**: Commit：`fix: 适配下厨房 2026 详情页步骤解析`

## Task 1.3: 补爬存量 656 条菜谱的步骤

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
- [ ] **Step 4**: **生产执行**（约 2 小时，656 条 × 10s）：部署 v1.14.0 后在 NAS 容器里跑
  `docker exec -d compose_config-api-1 python scripts/backfill_recipe_steps.py`，之后用 SQL 验收：`SELECT COUNT(*) FROM recipes WHERE steps_json IS NOT NULL` 目标 ≥500。
  **备选路径**（Task 1.1 判定风控走不通时）：写 `scripts/backfill_steps_via_llm.py`，对每条菜谱用 LLM 按菜名+配料生成步骤，落库时 `list_source` 标记 `llm_backfill` 以便区分数据来源。菜谱名/配料/评分仍是真实的，只有步骤是 AI 补写
- [ ] **Step 5**: 验收后在 HANDOFF.md 当前状态里记录补爬结果数字

## Task 1.3b（可选，不阻塞后续）: 菜谱库扩容

现有 656 条来自下厨房 honor 榜。若 Task 1.2/1.3 顺利（真实抓取可行），把 `XiachufangScraper.scrape()` 的列表页范围扩到按食物词典关键词搜索页（`app/crawler/food_keywords.py` 的 FOOD_NAMES 取 Top100），目标库存 2000+。同样 TDD：先给新列表页类型写 fixture 测试。工作量大、收益是本地命中率，时间紧可跳过——预生成矩阵已保证预设食材组合全覆盖。

## Task 1.4: 预生成缓存表 + recommend 命中路径

**Files:**
- Modify: `app/models.py`（新表）、`app/main.py`（lifespan 建表已有 create_all 自动覆盖）
- Modify: `app/routers/recommend.py`
- Test: `tests/routers/test_recommend.py`、`tests/test_models.py`

**Interfaces:**
- Produces: `RecommendCache` 模型；`make_cache_key(ingredients: list[str]) -> str`（sorted + "|".join，全小写去空格）；recommend 端点在 `is_local_eligible and not exclude_dishes` 时优先查缓存

- [ ] **Step 1**: models.py 新增（注意本项目踩过的坑：**DateTime 列比较必须用 datetime，不能用裸 date**）：
```python
class RecommendCache(Base):
    """食材组合 → 推荐结果预生成缓存。"""
    __tablename__ = "recommend_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    payload: Mapped[str] = mapped_column(Text)  # IngredientRecommendResponse 的 JSON
    model: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
```
- [ ] **Step 2**: 失败测试（模型 + 端点命中/未命中/过期三态 + key 归一化 `["番茄","鸡蛋"]==["鸡蛋"," 番茄"]`）
- [ ] **Step 3**: recommend.py 实现：`make_cache_key`；端点开头（本地搜索之前）查缓存，命中且未过期 → 直接 `IngredientRecommendResponse(**json.loads(payload))`；LLM 成功生成后 upsert 缓存（TTL 7 天）。**注意 upsert 用 `datetime.now(timezone.utc)` 比较 expires_at**
- [ ] **Step 4**: 全量测试绿 → commit：`feat: recommend 结果缓存（食材组合 key，7 天 TTL）`

## Task 1.5: 预生成跑批任务

**Files:**
- Create: `app/crawler/pregen.py`
- Modify: `app/crawler/scheduler.py`（注册 cron job）、`app/config.py`（开关+预算 env）
- Test: `tests/crawler/test_pregen.py`

**Interfaces:**
- Consumes: Task 1.4 的 `make_cache_key` / `RecommendCache`；recommend.py 需先把"LLM 生成 dishes"抽成可复用函数 `generate_dishes_via_llm(ingredients, count, preferences, allow_extra, exclude) -> list[RecommendedDish]`（本任务第一步做这个抽取重构，端点行为零变化）
- Produces: `run_pregeneration(db, budget: int) -> int`（返回本次生成条数）；`PRESET_INGREDIENTS`（30 个，与前端 `src/pages/ingredient/ingredient.tsx` COMMON_INGREDIENTS 手动同步，文件头注释注明同步来源）；APScheduler cron 每天 03:30 CST

- [ ] **Step 1**: 重构抽取 `generate_dishes_via_llm`（纯搬代码，behavior 不变），全量测试绿，单独 commit：`refactor: 抽取 LLM 生成函数供跑批复用`
- [ ] **Step 2**: 失败测试：组合枚举（30 单 + 435 对 = 465）、budget 截断、已有未过期缓存跳过、单条失败不中断整批
- [ ] **Step 3**: 实现 `pregen.py`：
```python
PRESET_INGREDIENTS = ["番茄", "土豆", ...]  # 30 个，同步自前端 ingredient.tsx

def iter_preset_combos():
    yield from ([i] for i in PRESET_INGREDIENTS)
    yield from ([a, b] for a, b in itertools.combinations(PRESET_INGREDIENTS, 2))

def run_pregeneration(db, budget: int = PREGEN_DAILY_BUDGET) -> int:
    # 遍历 combos，跳过缓存未过期的；调 generate_dishes_via_llm(count=3)
    # 成功 upsert RecommendCache（TTL 7 天错峰：TTL = 7d + random 0-24h 防止同日全体过期）
    # 达到 budget 或全部覆盖即停；单条异常 log 后继续
```
- [ ] **Step 4**: config.py 加 `PREGEN_ENABLED`（默认 true）`PREGEN_DAILY_BUDGET`（默认 120，即全矩阵约 4 天铺满、之后每天只刷过期的）；scheduler.py 注册 cron（`hour=3, minute=30`），**跑批必须 try/except 全包裹，绝不能把异常抛给 APScheduler**（本项目 7 月刚发生过爬虫 job 因异常连崩 3 天的事故）
- [ ] **Step 5**: 测试全绿 → commit：`feat: 食材组合预生成跑批（每日 03:30，预算制）`

## Task 1.6: P1 发版部署

- [ ] **Step 1**: `app/config.py` APP_VERSION → `1.14.0`，commit：`chore: 版本号升至 1.14.0`
- [ ] **Step 2**: push → 等 CI 绿 → 按全局约束的 NAS 部署流程上线 → `GET https://food.zuitian.ai/api/health` 确认 1.14.0
- [ ] **Step 3**: 执行 Task 1.3 Step 4 的补爬
- [ ] **Step 4**: 手动触发一轮预生成验证（容器内 `python -c "from app.crawler.pregen import ...; run_pregeneration(db, budget=5)"`），然后实测：POST /api/recommend 送一个已预生成组合（如 `["番茄","鸡蛋"]`）应 <1 秒返回
- [ ] **Step 5**: 更新两仓库 HANDOFF.md 与 memory 的状态段

---

# Phase 2 — 投机预取 + 模型竞速（前端 v1.8.0 + 后端 v1.15.0）

## Task 2.1: 后端双模型竞速

**Files:**
- Modify: `app/config.py`（`OPENROUTER_FAST_MODEL` env，默认空=不竞速）、`app/routers/recommend.py`
- Test: `tests/routers/test_recommend.py`

**Interfaces:**
- Produces: 当 `OPENROUTER_FAST_MODEL` 非空且请求走 LLM 路径时，并发调 fast+主模型，**先完成者返回给用户**；慢的那路完成后结果写入 RecommendCache（下次命中的是质量版）。实现用 `asyncio.get_event_loop().run_in_executor` 包两个同步调用 + `asyncio.wait(FIRST_COMPLETED)`（openai 同步 client 保持不变，避免大改）

- [ ] **Step 1**: 失败测试：mock 两个模型一快一慢 → 返回快者结果；慢者结果落缓存；fast env 为空时行为与现在完全一致（回归保护）
- [ ] **Step 2**: 实现（注意：败者写缓存要用独立 db session，请求级 session 在响应后已关闭；参考 `scheduler.py` 的 `SessionLocal()` 用法）
- [ ] **Step 3**: 测试全绿 → commit：`feat: recommend 双模型竞速，先到先得+败者入缓存`
- [ ] **Step 4**: NAS compose 加 env `OPENROUTER_FAST_MODEL`（OpenRouter 渠道填 `deepseek/deepseek-v4-flash`，官网直连渠道填 `deepseek-v4-flash`）

## Task 2.2: 前端投机预取

**Files:**
- Modify: `src/pages/ingredient/ingredient.tsx`
- Test: `src/__tests__/pages/ingredient.test.tsx`

**Interfaces:**
- Produces: 用户勾选食材停顿 1 秒后后台预发 recommend 请求；点"开始推荐"时若预取 key（sorted ingredients + preference + allowExtra 序列化）与当前选择一致 → 复用预取 promise，否则正常请求。预取失败静默丢弃，不 toast

- [ ] **Step 1**: 失败测试（本项目 jest 约定：**不用 fake timers**，用真实定时器 + `waitFor({timeout})`；`useDidShow` 禁用——历史上它会让整个测试文件静默崩掉）：
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
- [ ] **Step 3**: 全量 jest 绿 + `npm run build:weapp` 过 → commit：`feat: 选食材时投机预取推荐结果`
- [ ] **Step 4**: 版本 bump（后端 1.15.0 / 前端 1.8.0）各自 commit，后端走 NAS 部署验证 health，前端开发者工具真机预览验证预取生效（Network 面板看请求时机）

---

# Phase 3 — 两段式 + 流式 + 永不报错（前端 v1.9.0 + 后端 v1.16.0）

## Task 3.1: 后端两段式端点

**Files:**
- Modify: `app/routers/recommend.py`、`app/schemas.py`
- Test: `tests/routers/test_recommend.py`

**Interfaces:**
- Produces:
  - `POST /api/recommend/quick`：body 同 /api/recommend，返回 `{"dishes": [{"name","summary","difficulty","cook_time"}], "input_ingredients": [...]}`（无 ingredients/steps；prompt 明确只要菜名+简介，max_tokens=800，比全量快 3-5 倍）。缓存命中时直接从缓存裁剪字段返回
  - `POST /api/recommend/steps`：body `{"dish_name": str, "ingredients": [str]}`，返回单道菜完整 `RecommendedDish`。先查 RecommendCache 与本地 recipes 表（名字模糊匹配），miss 才调 LLM

- [ ] **Step 1**: 失败测试（两端点各覆盖：正常/缓存命中/LLM 异常 502/参数校验 422）
- [ ] **Step 2**: 实现 + 测试绿 → commit：`feat: 两段式推荐端点 quick/steps`

## Task 3.2: 步骤流式输出

**Files:**
- Modify: `app/routers/recommend.py`（steps 端点加 `?stream=1` 支持）
- Test: `tests/routers/test_recommend.py`
- Modify（前端）: `src/services/api.ts`、`src/pages/ingredient/ingredient.tsx`

**Interfaces:**
- Produces: `POST /api/recommend/steps?stream=1` → `StreamingResponse`（`text/plain; charset=utf-8`，直接转发 LLM delta 文本，结束后最后一行输出 `\n@@JSON@@{完整RecommendedDish JSON}` 供前端落最终结构）；前端 `Taro.request({enableChunked: true})` + `requestTask.onChunkReceived` 增量渲染，**onChunkReceived 不可用或首 chunk 3 秒未到 → 自动回退非流式**（微信基础库/真机兼容性兜底；分块回调拿到的是 ArrayBuffer，需 TextDecoder 解码且注意 UTF-8 断字——按字节缓冲、解码失败的尾部字节留到下一 chunk）

- [ ] **Step 1**: 后端失败测试：`stream=1` 返回 StreamingResponse、chunk 拼接后含 @@JSON@@ 结构、LLM 中途异常时输出错误标记行 `@@ERR@@`
- [ ] **Step 2**: 后端实现（openai SDK `stream=True` 迭代 delta）→ 测试绿 → commit：`feat: 步骤生成流式输出`
- [ ] **Step 3**: 前端失败测试：流式路径渲染增量文本、@@JSON@@ 到达后替换为结构化展示、回退路径正常
- [ ] **Step 4**: 前端实现 → jest 绿 + build:weapp 过 → commit：`feat: 步骤流式渲染+自动回退`
- [ ] **Step 5**: **真机验证必做**（开发者工具的 chunked 行为与真机不一致是本项目已知坑类型）：预览版实测流式逐字出现；不支持的机型回退无感

## Task 3.3: 前端两段式交互改造

**Files:**
- Modify: `src/pages/ingredient/ingredient.tsx`（handleRecommend 改调 quick；卡片点开触发 steps）
- Test: `src/__tests__/pages/ingredient.test.tsx`

- [ ] **Step 1**: 失败测试：点推荐 → 渲染 3 张菜名卡（无步骤）；点某卡 → 该卡 loading → 步骤出现；再点收起
- [ ] **Step 2**: 实现（预取逻辑同步改为预取 quick 端点；「加载更多」同样走 quick）→ jest 绿 → commit：`feat: 两段式交互——秒出菜名,点开看步骤`
- [ ] **Step 3**: 等待文案阶段化（简单实现：loading 态每 3 秒轮换 `正在翻 2 万本菜谱… / 大厨思考中… / 快好了快好了`）→ commit：`feat: 等待文案阶段化`

## Task 3.4: 永不报错（全链路静默降级）

**Files:**
- Modify: `app/routers/recommend.py`、前端 `ingredient.tsx`
- Test: 两侧对应测试文件

**Interfaces:**
- Produces: 后端降级链——LLM 异常时依次尝试：fast 模型重试 1 次 → RecommendCache 里**任意包含首个食材的旧缓存** → 本地 recipes 表（此时允许无 steps，摘要写"点开看详细做法"由 steps 端点兜底）→ 全部失败才 502。前端——502/超时/网络错误一律不弹「网络异常」，改为展示按食材匹配的本地硬编码菜谱（`src/data/` 已有 19 道）+ 温和文案「网络开小差，先看看这些经典搭配」，并附「重试」按钮

- [ ] **Step 1**: 后端失败测试：主模型炸 → fast 兜住；全炸 → 旧缓存兜住；真全炸 → 502。前端失败测试：502 → 渲染本地推荐 + 重试按钮，**不出现「网络异常，请重试」toast**
- [ ] **Step 2**: 实现两侧 → 全绿 → 各自 commit：`feat: recommend 多级降级` / `feat: 失败静默降级到本地推荐`
- [ ] **Step 3**: 版本 bump（后端 1.16.0 / 前端 1.9.0）+ 部署 + health 验证 + 真机全流程回归（预生成命中/未命中/飞行模式断网三个场景）
- [ ] **Step 4**: 更新 HANDOFF.md ×2 与 memory；把本计划文件标记为已完成（文件头加 `> ✅ 已于 YYYY-MM-DD 完成` 行）

---

## 完成定义（整体验收）

1. 预设食材任选 1-2 个 → 点推荐 → **<1 秒**出结果（预生成命中）
2. 冷门自定义食材 → **<5 秒**出 3 张菜名卡，点开步骤流式展开
3. 后端 kill 掉 LLM env（模拟故障）→ 前端仍有可用推荐，无报错 toast
4. 后端测试 ≥261+新增全绿、覆盖率 ≥95%；前端 ≥159+新增全绿
5. 每晚 03:30 跑批日志正常（`docker logs compose_config-api-1 | grep pregen`），无异常堆栈
