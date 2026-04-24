# 食物数据质量 AI 增强 — v1.9.0 设计文档

**日期**：2026-04-24
**目标版本**：`APP_VERSION = "1.9.0"`
**状态**：待实施
**作者**：Claude Code（brainstorming 阶段产物，已获用户批准）

---

## 1. 背景与动机

「到底吃啥哟」后端 v1.8.1 运行在 NAS Docker 中，爬取 5 个数据源（baidu_suggest / toutiao / dailyhot / manual / ai_extract）填充 `food_trends` 表（当前 363 行），驱动前端小程序"热门推荐"栏。本次评审发现 4 个数据质量问题 + 2 个 endpoint bug：

| 编号 | 问题 | 具体表现 |
|------|------|----------|
| Q1 | 同名同义食物未归一 | "川式火锅/四川火锅/火锅" 各占一行；Top 20 里"烧烤"出现 2 次（baidu + dailyhot） |
| Q2 | baidu_suggest 污染 Top 排名 | 该源全部固定 heat_score=100，霸占 Top 5，挤走真实热度信号 |
| Q3 | digest 缺乏归因信息 | AI 只知道"火锅很火"，不知道是事件/季节/长青，summary 输出空洞 |
| Q4 | 新食物分类缺失 | `ai_extract` 发现的新食物 category 多为 null |
| B1 | digest endpoint 凌晨返回 null | 当日 digest 未生成时无 fallback（当前 CST 02:16 访问返回 null）|
| B2 | trending endpoint 不去重 | 同一食物的多源记录各占一行，Top N 里出现重复 |

本 spec 把 4 项数据改进 + 2 项 endpoint 修复合并为 **v1.9.0** 一次发版。

## 2. 范围

### In scope
- 新增 `food_aliases` 表 + 相关模型字段，支持食物规范化
- `ai_extractor.py` 合并提取 + 分类 + 归因 + 同义判断为一次 Claude 调用
- `baidu_suggest` 降级为候选源（写 `ai_discovered_foods` 而非 `food_trends`），候选晋级由 AI 决定
- `ai_digest.py` 使用归因字段生成更有洞察的 summary
- `GET /api/trending` 按 `canonical_name` 聚合去重（Bug 2 修复）
- `GET /api/trending/digest` 无 date 参数时 fallback 到最新 digest（Bug 1 修复）
- 历史 363 行数据 backfill + 首次 AI 扫描生成别名映射
- 后端测试新增 25+ 项，保持 coverage ≥ 97%

### Out of scope
- 前端小程序改动（新增字段均为 Optional，向后兼容）
- 图片 URL 补全（候补项，留到后续）
- 跨源 heat_score 数学归一化
- 前端版本升级

## 3. 关键设计决策

决策于 2026-04-24 brainstorming 阶段锁定：

| 决策 | 选项 | 理由 |
|------|------|------|
| FoodAlias 存储形态 | 独立 `food_aliases` 表 + `food_trends.canonical_name` 冗余列 | 可审计；AI 调用缓存；别名→规范映射复用 |
| baidu_suggest 处理 | 降级为候选源，不直接入主表 | 保留发现能力 + 彻底解决 Top 排名污染 |
| Rollout 形状 | 单版本 v1.9.0 一次发布 | 改动虽多但解耦良好，一次回归测试即可 |

## 4. 数据模型变更

### 4.1 新增表 `food_aliases`

```python
class FoodAlias(Base):
    __tablename__ = "food_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alias_name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    canonical_name: Mapped[str] = mapped_column(String(100), index=True)
    created_by: Mapped[str] = mapped_column(String(20))  # "ai" | "manual"
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
```

约束：`alias_name` 全局唯一。一个 canonical 可以有多个 alias。自引用（canonical=alias）允许，表示"该词本身就是规范名"。

### 4.2 `FoodTrend` 新增 3 列

```python
# 全部 nullable=True，历史行 backfill 后保持非空语义（但模型定义保持 Optional 以支持 ALTER TABLE）
canonical_name: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
trend_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
trend_context: Mapped[str | None] = mapped_column(String(100), nullable=True)
```

- `canonical_name`：聚合 key。backfill 后默认 = `food_name`（自引用），AI 合并时改写。应用层对 NULL 做 coalesce 到 `food_name`
- `trend_type`：`"event" | "seasonal" | "evergreen" | "meme" | None`
- `trend_context`：≤ 15 字的归因短语，如"围炉煮茶带动"

### 4.3 `AIDiscoveredFood` 新增 1 列

```python
promoted_to_trends: Mapped[bool] = mapped_column(Boolean, default=False)
```

候选词是否已晋级到 `food_trends`。避免重复晋级。

### 4.4 迁移策略

**重要**：项目未用 Alembic，`Base.metadata.create_all(bind=engine)` 只创建**不存在的表**，不会给已存在的表加新列。因此迁移拆为两件事：

**a. 新表创建** — `food_aliases` 由 `create_all()` 自动创建（已在 `main.py:30` 启动时调用）。

**b. 已有表加列** — `food_trends` 的 3 新列 + `ai_discovered_foods` 的 1 新列需要手动 `ALTER TABLE`。在新增脚本 `app/migrations/backfill_v1_9_0.py` 中实现：

```python
def migrate_v1_9_0(engine):
    with engine.connect() as conn:
        # 幂等加列：检查 column 是否存在（SQLite PRAGMA table_info）
        _add_column_if_missing(conn, "food_trends", "canonical_name", "VARCHAR(100)")
        _add_column_if_missing(conn, "food_trends", "trend_type", "VARCHAR(20)")
        _add_column_if_missing(conn, "food_trends", "trend_context", "VARCHAR(100)")
        _add_column_if_missing(
            conn, "ai_discovered_foods", "promoted_to_trends",
            "BOOLEAN NOT NULL DEFAULT 0"
        )
        # 为 canonical_name 建索引（SQLite CREATE INDEX IF NOT EXISTS）
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_food_trends_canonical "
            "ON food_trends(canonical_name)"
        ))
        # backfill：canonical_name 未设置的行 = food_name
        conn.execute(text(
            "UPDATE food_trends SET canonical_name = food_name "
            "WHERE canonical_name IS NULL"
        ))
        conn.commit()
```

`_add_column_if_missing` 辅助函数查 `PRAGMA table_info(table)`，列存在则跳过（幂等）。

**c. food_aliases 种子** — 若 `food_aliases` 表为空，遍历所有 `food_trends.food_name` 去重后为每个插入 `FoodAlias(alias_name=food_name, canonical_name=food_name, created_by='manual')`。这样每个已有食物都有自引用别名记录，AI 合并后新增 alias 行即可。

**d. 启动钩子** — `main.py:lifespan` 在 `create_all()` 后 try-except 调用 `migrate_v1_9_0(engine)`。失败只 log warning，不阻塞容器启动。

**e. AI 合并扫描**（部署后手动触发一次）：新增内部辅助端点 `POST /api/admin/merge-aliases`（或直接写在 `scheduler.py` 里作为函数，从 API trigger）。逻辑：
1. 取所有 `food_trends.food_name` 列表（去重）
2. 分批（每 50 个）送给 Claude：「以下食物列表里，哪些是同一食物的别名或语义同类？返回 [{canonical, aliases: [...]}] 格式」
3. 解析结果写入 `food_aliases` 表，同步更新 `food_trends.canonical_name`
该操作幂等可重跑。

## 5. AI Extractor 改造

### 5.1 Prompt 合并

`ai_extractor.py` 当前只做"从标题提取食物名"。改造后单次 Claude 调用同时输出 4 维度：

```
输入：未匹配的热搜标题列表 + 已知 canonical_names 集合
输出（纯 JSON，无 markdown）：
{
  "items": [
    {
      "source_title": "...",
      "foods": [
        {
          "name": "川式火锅",
          "category": "火锅",
          "canonical_of": "火锅",     // 若该食物是某已知食物的别名；否则等于 name
          "trend_type": "seasonal",    // event | seasonal | evergreen | meme
          "trend_context": "入冬涮锅季" // ≤15 字
        }
      ]
    }
  ]
}
```

### 5.2 复用现有 `AITitleCache`

标题哈希缓存继续用。缓存内容从原来的 `foods: list[str]` 扩展为新的完整 items 结构（JSON）。旧缓存条目兼容：解析失败则视为缓存未命中，重新提取。

### 5.3 模块化

新增 dataclass：

```python
# app/crawler/ai_extractor.py
@dataclass
class ExtractedFoodItem:
    name: str
    category: str | None = None
    canonical_of: str | None = None   # 若是别名，填规范名；否则 None 或 == name
    trend_type: str | None = None      # event | seasonal | evergreen | meme
    trend_context: str | None = None   # ≤15 字归因短语
    source_title: str | None = None    # 来源热搜标题（溯源用）
```

`extract_foods_from_titles()` 返回类型从 `list[FoodTrendItem]` 改为 `list[ExtractedFoodItem]`。调用方 `scheduler._save_items()` 感知新字段：根据 canonical_of 决定是否插入 `food_aliases`，其他字段写入 `food_trends` 对应列。

## 6. Scheduler 改造

### 6.1 baidu_suggest 分流

`run_all_crawlers()` 当前对所有 crawler 统一调 `_save_items(db, source, items)`。改造：

```python
for crawler in ALL_CRAWLERS:
    items = crawler.crawl()
    if crawler.get_source_name() == "baidu_suggest":
        _save_candidates(db, items)          # 新函数：写入 AIDiscoveredFood
    else:
        _save_items(db, source, items)       # 原逻辑保留
```

### 6.2 候选晋级逻辑

所有爬虫跑完后，新增 `_promote_candidates(db)`：

```
SELECT d.food_name
FROM ai_discovered_foods d
WHERE d.promoted_to_trends = 0
  AND EXISTS (
    SELECT 1 FROM food_trends t
    WHERE t.canonical_name = d.food_name
       OR t.food_name = d.food_name
  );
```

匹配到的候选插入 `food_trends`（source='baidu_suggest', heat_score = 其他源最高分 × 0.8），并标记 `promoted_to_trends=True`。

### 6.3 别名落库

当 `extracted.canonical_of != extracted.name` 时：
1. 若 `food_aliases` 表中该 alias 不存在 → insert（`created_by='ai'`, `confidence` 由 AI 返回）
2. 落到 `food_trends` 的 `canonical_name` 字段使用 canonical_of 而非 name

### 6.4 trend_type / trend_context 落库

每轮 AI 提取返回的这两个字段直接写入对应 `food_trends` 记录（同名不同源共享——以最近一次为准，简化实现）。

## 7. Digest 改造

### 7.1 数据构建

`ai_digest.py` 的 `generate_daily_digest()` 查询 Top 30 时 JOIN / SELECT 时带上 `trend_type` + `trend_context`：

```
data_lines:
- 火锅（正餐）热度:100 来源:toutiao  type:seasonal context:入冬涮锅季
- 奶茶（饮品）热度:95  来源:dailyhot type:event    context:围炉煮茶带动
- 煎饼（小吃）热度:98  来源:dailyhot type:evergreen context:(null)
```

### 7.2 Prompt 调整

system prompt 新增：

```
【归因类型说明】
- event: 事件/综艺/直播带火，说明时可引用 context
- seasonal: 季节相关，说明时结合节气
- evergreen: 长青品类，无需特殊归因
- meme: 网络梗/社交话题

使用 trend_context 让 summary 有故事感，而非仅列举数字。
```

## 8. Endpoint 变更

### 8.1 `GET /api/trending`（Bug 2 修复）

新增 query 参数 `aggregate: bool = True`（默认聚合）。

**aggregate=True（默认）** — 按 canonical_name 聚合：
```sql
SELECT
  MIN(id) as id,                     -- 代表行
  canonical_name,
  MAX(heat_score) as heat_score,
  SUM(post_count) as post_count,
  MAX(category) as category,
  MAX(image_url) as image_url,
  MAX(updated_at) as updated_at,
  GROUP_CONCAT(DISTINCT source) as sources,
  GROUP_CONCAT(DISTINCT food_name) as aliases_raw,
  MAX(trend_type) as trend_type,
  MAX(trend_context) as trend_context
FROM food_trends
WHERE ...filters...
GROUP BY canonical_name
ORDER BY heat_score DESC
LIMIT ? OFFSET ?;
```

响应 item 额外字段：`canonical_name`, `aliases: list[str]`（去重），`sources: list[str]`, `trend_type`, `trend_context`。`total` 改为去重后总数：`SELECT COUNT(DISTINCT canonical_name)`。

**aggregate=False** — 保持原行为（返回原始行，无聚合）。用于调试或需要按源分析的场景。

### 8.2 `GET /api/trending/digest`（Bug 1 修复）

```python
if target_date is None:
    # fallback: 最新一条
    digest = db.execute(
        select(FoodDigest).order_by(FoodDigest.digest_date.desc()).limit(1)
    ).scalar_one_or_none()
else:
    # 精确查询（原逻辑）
    target_dt = datetime.combine(target_date, time.min)
    digest = db.execute(
        select(FoodDigest).where(FoodDigest.digest_date == target_dt)
    ).scalar_one_or_none()
```

## 9. Schema 变更（`app/schemas.py`）

`FoodTrendOut` 新增（全部 Optional 保向后兼容）：

```python
canonical_name: str | None = None
aliases: list[str] = []
sources: list[str] = []
trend_type: str | None = None
trend_context: str | None = None
```

`FoodDigestOut` 不变。

## 10. 测试计划

现有 219 tests 全部保留不破坏。新增 ~25 tests：

| 模块 | 新增测试 |
|------|----------|
| `test_models.py` | FoodAlias 唯一约束、canonical_name 默认值、promoted_to_trends 默认 False（3） |
| `test_ai_extractor.py` | canonical_of 返回 / trend_type 分类 / context 长度限制 / cache 命中不调 API / cache 旧格式兼容（5）|
| `test_scheduler.py` | baidu_suggest 入 AIDiscoveredFood / 候选晋级条件 / 晋级 heat_score 计算 / alias 落库 / trend 字段落库（5）|
| `test_ai_digest.py` | prompt 包含 trend 字段 / 归因齐全 vs 缺失时 summary 差异（2）|
| `test_trending_endpoint.py` | aggregate=True 去重 / aliases 合并 / sources 合并 / total 反映 canonical / aggregate=False 原行为 / digest fallback / digest 精确查询（7）|
| `test_migrations.py` | backfill 幂等 / canonical 自引用 / 重跑不重复插入（3）|

目标：244+ tests，coverage ≥ 97%。

## 11. 部署流程

| 步骤 | 命令/操作 |
|------|----------|
| 1. 本地测试 | `pytest && pytest --cov` |
| 2. 版本号 | `app/config.py` → `APP_VERSION = "1.9.0"` |
| 3. Commit & Push | 按模块分 commits（models / extractor / scheduler / digest / endpoints / tests / migration），feat/ 分支 |
| 4. CI 等待 | GitHub Actions 自动构建 Docker 镜像推 Docker Hub（~3 min）|
| 5. 获取 SHA | `git rev-parse HEAD` |
| 6. SFTP compose | 写 `/tmp/daodichishayou-backend.yaml` → `sudo cp` 覆盖 `/zspace/applications/services/zdocker/config/compose_config/daodichishayou-backend.yaml`，`image:` 行用新 SHA tag |
| 7. 拉 + 重建 | `sudo docker compose pull && sudo docker compose up -d --force-recreate` |
| 8. 清理旧镜像 | `sudo docker image prune -f` + 删旧 SHA tag |
| 9. 验证 health | `curl https://food.zuitian.ai/api/health` → `version=1.9.0` |
| 10. 首次 AI 合并（独立步骤） | Python urllib 调 `POST /api/admin/merge-aliases`（新端点，section 4.4.e），扫历史 363 行生成 canonical 映射。**不用 curl POST**（Windows bash 引号转义问题）：<br>`python -c "import urllib.request as r; r.urlopen(r.Request('https://food.zuitian.ai/api/admin/merge-aliases', method='POST'))"` |
| 11. 触发一次爬虫 | 同样用 Python urllib 调 `POST /api/trending/crawl`，让新 AI extractor prompt 跑一轮填充 trend_type / context |
| 12. 验证聚合 | `curl 'https://food.zuitian.ai/api/trending?limit=20'` → 无"烧烤"重复；`curl https://food.zuitian.ai/api/trending/digest` → 返回非 null |
| 13. 更新 memory | 更新 `daodichishayou_progress.md` 版本记录（v1.9.0 section）|

## 12. 风险与缓解

| 风险 | 概率 | 缓解 |
|------|------|------|
| AI 合并同义判断错误，把"牛肉面"归到"面条"下 | 中 | 所有 AI 合并写入 `food_aliases` 表 + `confidence` 字段，低置信度人工 review；前端端点 aliases 字段可观察 |
| SQLite `ALTER TABLE ADD COLUMN` 锁住大表 | 低 | 表仅 363 行，ALTER 毫秒级 |
| 启动时 backfill 脚本失败导致 container 起不来 | 中 | try-except 包裹；启动日志打 warning；数据层保持可读（旧字段不变） |
| baidu_suggest 降级后数据量骤降（Top 看起来少） | 低 | Top 排名反而更有意义；若影响前端展示，降级期间前端自动用硬编码补足 |
| AI 调用成本增加 | 中 | `AITitleCache` 继续缓存；首次扫描一次性调用，日常只处理新标题 |

## 13. 成功标准

- [ ] `GET /api/trending?limit=20` 不再出现"烧烤"或任何食物重复
- [ ] `GET /api/trending/digest` 凌晨访问返回非 null（昨日 digest）
- [ ] `food_aliases` 表有 ≥ 10 条 AI 生成的合并记录
- [ ] `food_trends` 所有行 `canonical_name` 非空
- [ ] `ai_discovered_foods` 表反映 baidu_suggest 候选词流入
- [ ] digest summary 明显包含归因（type/context）而非空洞描述
- [ ] 后端测试 244+ 全部通过，coverage ≥ 97%
- [ ] `/api/health` 返回 `version=1.9.0`
- [ ] 前端小程序无需改动即可正常工作（向后兼容验证）

## 14. 相关文件（实现时修改）

| 文件 | 操作 |
|------|------|
| `app/models.py` | 新增 FoodAlias、扩展 FoodTrend/AIDiscoveredFood |
| `app/schemas.py` | 扩展 FoodTrendOut |
| `app/config.py` | 版本 → 1.9.0 |
| `app/crawler/ai_extractor.py` | prompt 重写、返回结构变更、cache 兼容 |
| `app/crawler/scheduler.py` | baidu_suggest 分流、候选晋级、alias 落库 |
| `app/crawler/ai_digest.py` | prompt 加归因字段 |
| `app/routers/trending.py` | aggregate 参数、聚合 SQL、digest fallback |
| `app/migrations/backfill_v1_9_0.py` | 新增，启动时调用 |
| `app/main.py` | 启动钩子调 backfill |
| `tests/*` | 新增 25+ 测试 |

---

## 变更记录

- **2026-04-24** — 初稿，brainstorming 阶段产出，1A/2A/3A 决策
