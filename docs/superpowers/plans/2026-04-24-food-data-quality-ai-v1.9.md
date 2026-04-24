# 食物数据质量 AI 增强 v1.9.0 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 4 项数据质量 AI 改进（食物规范化、归因标注、baidu_suggest 降级、新食物智能分类）+ 2 项 endpoint bug（digest null / trending 重复）合并为一次 v1.9.0 发版，落到「到底吃啥哟」后端（FastAPI + SQLAlchemy + SQLite + APScheduler）。

**Architecture:** 新增 `food_aliases` 表做 alias→canonical 映射；`food_trends` 扩展 3 列（`canonical_name` / `trend_type` / `trend_context`）；`baidu_suggest` 不再直接入 `food_trends`，改为写 `ai_discovered_foods` 作为候选；AI extractor 单次调用同时输出 canonical/category/trend_type/context；trending endpoint 按 canonical 聚合去重；digest endpoint 无 date 时 fallback 到最新。

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0 ORM, SQLite, Anthropic SDK (Claude Opus 4.7), APScheduler, pytest, httpx.

**Spec**: `docs/superpowers/specs/2026-04-24-food-data-quality-ai-design.md`（决策 1A/2A/3A 已锁定）

---

## 全局约定

- **编码习惯**：遵守 `CLAUDE.md` / 全局 `coding-style.md`：函数 ≤50 行，参数 ≤4，缩进 ≤3 层，commit 不加 Co-Authored-By。
- **commit 风格**：与历史一致 `type: 中文简短描述`（`feat:` / `fix:` / `test:` / `refactor:` / `docs:`）。
- **测试执行**：`pytest tests/ -v`（项目根目录 `C:\Users\goodb\daodichishayou-backend`）。单测用 `pytest tests/path/test.py::test_name -v`。
- **UTC 与 CST**：数据库 `digest_date` / `snapshot_date` 存的是**服务器本地日期（CST）** 的 `datetime.combine(date.today(), time.min)` naive datetime；`updated_at` 是 `datetime.now(timezone.utc)` 带 tz。
- **AI 调用成本控制**：所有 AI 调用必须过 `AITitleCache`（标题哈希缓存），测试里 mock `Anthropic` client。

---

## Task 1: 模型层 — 新增 FoodAlias + 扩展 FoodTrend/AIDiscoveredFood

**Files:**
- Modify: `app/models.py`
- Create: `tests/test_models_v1_9.py`

- [ ] **Step 1: 写 FoodAlias 模型的失败测试**

Create `tests/test_models_v1_9.py`:

```python
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models import AIDiscoveredFood, FoodAlias, FoodTrend


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_food_alias_insert_and_read(db):
    alias = FoodAlias(
        alias_name="川式火锅",
        canonical_name="火锅",
        created_by="ai",
        confidence=0.95,
    )
    db.add(alias)
    db.commit()

    row = db.execute(
        select(FoodAlias).where(FoodAlias.alias_name == "川式火锅")
    ).scalar_one()
    assert row.canonical_name == "火锅"
    assert row.created_by == "ai"
    assert row.confidence == 0.95


def test_food_alias_unique_constraint(db):
    db.add(FoodAlias(alias_name="川式火锅", canonical_name="火锅", created_by="ai"))
    db.commit()
    db.add(FoodAlias(alias_name="川式火锅", canonical_name="火锅", created_by="manual"))
    with pytest.raises(Exception):
        db.commit()


def test_food_trend_new_columns_default_null(db):
    t = FoodTrend(food_name="测试食物", source="manual", heat_score=50, post_count=0)
    db.add(t)
    db.commit()
    row = db.execute(
        select(FoodTrend).where(FoodTrend.food_name == "测试食物")
    ).scalar_one()
    assert row.canonical_name is None
    assert row.trend_type is None
    assert row.trend_context is None


def test_food_trend_new_columns_writable(db):
    t = FoodTrend(
        food_name="围炉煮茶",
        source="toutiao",
        heat_score=90,
        post_count=1000,
        canonical_name="围炉煮茶",
        trend_type="seasonal",
        trend_context="入冬社交茶饮",
    )
    db.add(t)
    db.commit()
    row = db.execute(select(FoodTrend).where(FoodTrend.food_name == "围炉煮茶")).scalar_one()
    assert row.canonical_name == "围炉煮茶"
    assert row.trend_type == "seasonal"
    assert row.trend_context == "入冬社交茶饮"


def test_ai_discovered_food_promoted_default_false(db):
    d = AIDiscoveredFood(food_name="新食物X", category="小吃")
    db.add(d)
    db.commit()
    row = db.execute(
        select(AIDiscoveredFood).where(AIDiscoveredFood.food_name == "新食物X")
    ).scalar_one()
    assert row.promoted_to_trends is False
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_models_v1_9.py -v
```

Expected: FAIL — `FoodAlias` 未定义 / `FoodTrend.canonical_name` 列不存在 / `AIDiscoveredFood.promoted_to_trends` 列不存在。

- [ ] **Step 3: 修改 `app/models.py` 添加 FoodAlias**

在 `app/models.py` 文件末尾添加：

```python
class FoodAlias(Base):
    """食物别名 → 规范名映射，支持同义归并。"""
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

- [ ] **Step 4: 扩展 FoodTrend 模型新增 3 列**

在 `app/models.py` 中找到 `class FoodTrend(Base):` 定义，在 `image_url` 行之后、`updated_at` 之前插入：

```python
    canonical_name: Mapped[str | None] = mapped_column(
        String(100), index=True, nullable=True
    )
    trend_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    trend_context: Mapped[str | None] = mapped_column(String(100), nullable=True)
```

- [ ] **Step 5: 扩展 AIDiscoveredFood 新增 1 列**

在 `app/models.py` 中找到 `class AIDiscoveredFood(Base):` 定义，在 `discovery_count` 行之后插入：

```python
    promoted_to_trends: Mapped[bool] = mapped_column(Boolean, default=False)
```

同时顶部 import 补上 `Boolean`：将 `from sqlalchemy import DateTime, Float, Index, Integer, String, Text` 改为 `from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text`。

- [ ] **Step 6: 跑测试验证通过**

```bash
pytest tests/test_models_v1_9.py -v
```

Expected: PASS — 5 tests passed.

- [ ] **Step 7: 跑全量测试确保没破坏**

```bash
pytest tests/ -v
```

Expected: PASS — 现有 219 tests + 新增 5 = 224 passed。

- [ ] **Step 8: Commit**

```bash
git add app/models.py tests/test_models_v1_9.py
git commit -m "feat: 新增 FoodAlias 表 + FoodTrend/AIDiscoveredFood 扩展列 (v1.9.0 模型层)"
```

---

## Task 2: Backfill 迁移脚本 + 启动钩子

**Files:**
- Create: `app/migrations/__init__.py`（空文件）
- Create: `app/migrations/backfill_v1_9_0.py`
- Modify: `app/main.py`（lifespan 加挂钩）
- Create: `tests/test_backfill_v1_9_0.py`

- [ ] **Step 1: 创建 migrations 包**

```bash
mkdir -p app/migrations
touch app/migrations/__init__.py
```

（Windows bash：`touch` 创建空文件 OK）

- [ ] **Step 2: 写 backfill 失败测试**

Create `tests/test_backfill_v1_9_0.py`:

```python
import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app.database import Base
from app.migrations.backfill_v1_9_0 import migrate_v1_9_0
from app.models import FoodAlias, FoodTrend


def _make_engine():
    return create_engine("sqlite:///:memory:")


def test_backfill_adds_canonical_name_column_if_missing():
    engine = _make_engine()
    # 模拟 v1.8.1 schema：先建一个没有新列的 food_trends
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE food_trends ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "food_name VARCHAR(100) NOT NULL,"
            "source VARCHAR(50) NOT NULL,"
            "heat_score INTEGER DEFAULT 0,"
            "post_count INTEGER DEFAULT 0,"
            "category VARCHAR(50),"
            "image_url VARCHAR(500),"
            "updated_at DATETIME,"
            "created_at DATETIME"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE ai_discovered_foods ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "food_name VARCHAR(100) UNIQUE,"
            "category VARCHAR(50),"
            "source_title VARCHAR(500),"
            "discovery_count INTEGER DEFAULT 1,"
            "created_at DATETIME"
            ")"
        ))
        conn.execute(text(
            "INSERT INTO food_trends (food_name, source, heat_score, post_count) "
            "VALUES ('火锅', 'manual', 90, 100)"
        ))
        conn.commit()

    migrate_v1_9_0(engine)

    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("food_trends")}
    assert "canonical_name" in cols
    assert "trend_type" in cols
    assert "trend_context" in cols
    cols_disc = {c["name"] for c in insp.get_columns("ai_discovered_foods")}
    assert "promoted_to_trends" in cols_disc


def test_backfill_sets_canonical_name_to_food_name():
    engine = _make_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(FoodTrend(food_name="火锅", source="manual", heat_score=90, post_count=100))
        s.add(FoodTrend(food_name="奶茶", source="manual", heat_score=85, post_count=50))
        s.commit()

    migrate_v1_9_0(engine)

    with Session(engine) as s:
        rows = s.execute(select(FoodTrend)).scalars().all()
        for r in rows:
            assert r.canonical_name == r.food_name


def test_backfill_is_idempotent():
    engine = _make_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(FoodTrend(food_name="火锅", source="manual", heat_score=90, post_count=100))
        s.commit()

    migrate_v1_9_0(engine)
    migrate_v1_9_0(engine)  # 二次调用不应报错

    with Session(engine) as s:
        rows = s.execute(select(FoodAlias)).scalars().all()
        names = [r.alias_name for r in rows]
        assert names.count("火锅") == 1  # 不重复插入


def test_backfill_seeds_food_aliases_self_reference():
    engine = _make_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(FoodTrend(food_name="火锅", source="manual", heat_score=90, post_count=100))
        s.add(FoodTrend(food_name="火锅", source="toutiao", heat_score=95, post_count=1000))
        s.add(FoodTrend(food_name="奶茶", source="manual", heat_score=85, post_count=50))
        s.commit()

    migrate_v1_9_0(engine)

    with Session(engine) as s:
        rows = s.execute(select(FoodAlias)).scalars().all()
        alias_map = {r.alias_name: r.canonical_name for r in rows}
        assert alias_map == {"火锅": "火锅", "奶茶": "奶茶"}
```

- [ ] **Step 3: 跑测试确认失败**

```bash
pytest tests/test_backfill_v1_9_0.py -v
```

Expected: FAIL — `app.migrations.backfill_v1_9_0` 不存在。

- [ ] **Step 4: 实现 backfill 脚本**

Create `app/migrations/backfill_v1_9_0.py`:

```python
"""v1.9.0 迁移脚本：给已有表加新列 + backfill canonical_name + 种子 food_aliases。

项目未用 Alembic，`Base.metadata.create_all()` 只创建不存在的表，不会给已存在的表加列。
本脚本在启动时调用，幂等可重跑。
"""

import logging

from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.orm import Session

from app.models import FoodAlias, FoodTrend

logger = logging.getLogger(__name__)


def _column_exists(conn, table: str, column: str) -> bool:
    result = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in result)


def _add_column_if_missing(conn, table: str, column: str, column_type: str) -> None:
    if not _column_exists(conn, table, column):
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"))
        logger.info("已添加列 %s.%s", table, column)


def migrate_v1_9_0(engine: Engine) -> None:
    """幂等迁移：加列 → 建索引 → backfill canonical_name → 种子 food_aliases。"""
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())

    # 依赖前置：food_trends / ai_discovered_foods / food_aliases 都应已存在
    # (food_aliases 由 Base.metadata.create_all() 创建)
    required = {"food_trends", "ai_discovered_foods", "food_aliases"}
    missing = required - existing_tables
    if missing:
        logger.warning("缺少必要表，跳过 v1.9.0 迁移: %s", missing)
        return

    with engine.connect() as conn:
        _add_column_if_missing(conn, "food_trends", "canonical_name", "VARCHAR(100)")
        _add_column_if_missing(conn, "food_trends", "trend_type", "VARCHAR(20)")
        _add_column_if_missing(conn, "food_trends", "trend_context", "VARCHAR(100)")
        _add_column_if_missing(
            conn,
            "ai_discovered_foods",
            "promoted_to_trends",
            "BOOLEAN NOT NULL DEFAULT 0",
        )

        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_food_trends_canonical "
            "ON food_trends(canonical_name)"
        ))

        # backfill canonical_name 未设置的行 = food_name
        conn.execute(text(
            "UPDATE food_trends SET canonical_name = food_name "
            "WHERE canonical_name IS NULL"
        ))
        conn.commit()
        logger.info("v1.9.0 列迁移完成")

    # 种子 food_aliases（幂等）
    with Session(engine) as s:
        existing_aliases = {
            row for row in s.execute(select(FoodAlias.alias_name)).scalars().all()
        }
        unique_food_names = {
            row for row in s.execute(select(FoodTrend.food_name).distinct()).scalars().all()
        }
        to_insert = unique_food_names - existing_aliases
        for name in to_insert:
            s.add(FoodAlias(
                alias_name=name,
                canonical_name=name,
                created_by="manual",
            ))
        if to_insert:
            s.commit()
            logger.info("v1.9.0 种子 food_aliases 插入 %d 条", len(to_insert))
```

- [ ] **Step 5: 跑测试验证通过**

```bash
pytest tests/test_backfill_v1_9_0.py -v
```

Expected: PASS — 4 tests passed.

- [ ] **Step 6: 挂载到 `app/main.py` lifespan**

在 `app/main.py` 中找到 `Base.metadata.create_all(bind=engine)` 这一行（约第 30 行），在它后面插入：

```python
    try:
        from app.migrations.backfill_v1_9_0 import migrate_v1_9_0
        migrate_v1_9_0(engine)
    except Exception:
        logging.getLogger(__name__).warning(
            "v1.9.0 迁移失败，跳过继续启动", exc_info=True
        )
```

- [ ] **Step 7: 跑全量测试**

```bash
pytest tests/ -v
```

Expected: PASS — 224 + 4 = 228 passed。

- [ ] **Step 8: Commit**

```bash
git add app/migrations/ app/main.py tests/test_backfill_v1_9_0.py
git commit -m "feat: v1.9.0 数据库迁移脚本 (ALTER TABLE + backfill + alias 种子)"
```

---

## Task 3: Schema 扩展 — FoodTrendOut 新增字段

**Files:**
- Modify: `app/schemas.py`

- [ ] **Step 1: 扩展 `FoodTrendOut`**

在 `app/schemas.py` 找到 `class FoodTrendOut(BaseModel):` 定义，将 `updated_at: datetime` 前的字段保持，然后在 `updated_at` 之后、`model_config` 之前插入：

```python
    canonical_name: str | None = None
    aliases: list[str] = []
    sources: list[str] = []
    trend_type: str | None = None
    trend_context: str | None = None
```

完整的 `FoodTrendOut` 应该变成：

```python
class FoodTrendOut(BaseModel):
    id: int
    food_name: str
    source: str
    heat_score: int
    post_count: int
    category: str | None = None
    image_url: str | None = None
    updated_at: datetime
    canonical_name: str | None = None
    aliases: list[str] = []
    sources: list[str] = []
    trend_type: str | None = None
    trend_context: str | None = None

    model_config = {"from_attributes": True}
```

- [ ] **Step 2: 跑现有测试确认没破坏**

```bash
pytest tests/ -v
```

Expected: PASS — 所有 228 tests 通过（新字段默认值向后兼容）。

- [ ] **Step 3: Commit**

```bash
git add app/schemas.py
git commit -m "feat: FoodTrendOut 扩展 canonical_name/aliases/sources/trend 字段"
```

---

## Task 4: AI Extractor 重写 — 单次调用输出 canonical + category + trend

**Files:**
- Modify: `app/crawler/ai_extractor.py`
- Modify: `tests/test_ai_extractor.py`（如存在）或 Create: `tests/test_ai_extractor_v1_9.py`

- [ ] **Step 1: 定义 ExtractedFoodItem dataclass**

在 `app/crawler/ai_extractor.py` 顶部 import 区加：

```python
from dataclasses import dataclass
```

在 `_MAX_TITLES_PER_BATCH = 50` 之前插入：

```python
VALID_TREND_TYPES = {"event", "seasonal", "evergreen", "meme"}


@dataclass
class ExtractedFoodItem:
    name: str
    category: str | None = None
    canonical_of: str | None = None  # 若是已有食物的别名，填规范名；否则 = name
    trend_type: str | None = None     # event | seasonal | evergreen | meme
    trend_context: str | None = None  # ≤15 字归因短语
    source_title: str | None = None
```

- [ ] **Step 2: 重写 `_SYSTEM_PROMPT`**

替换 `_SYSTEM_PROMPT` 定义为：

```python
_SYSTEM_PROMPT = f"""{AI_CORE_RULES}

你是一个美食识别+趋势分析专家。给你一批热搜标题，请对每条标题完成 3 件事：
1. 提取具体食物/菜品/饮品名称
2. 给每个食物归入分类
3. 判断该食物当前热度的归因类型 + 关联上下文

规则：
- 只提取具体食物名，不要提取泛称如"美食"、"小吃"
- 食物名长度 2-10 个字
- 每个食物必须归入：正餐/小吃/面食/烧烤/火锅/西餐/日料/韩餐/东南亚/甜品/饮品/早餐/轻食/点心/零食
- canonical_of：如果这个食物是某个已知食物的别名（如"川式火锅"→"火锅"、"酱香拿铁"→"拿铁"），填规范名；否则填本名
- trend_type：event(综艺/直播/事件带火) | seasonal(季节相关) | evergreen(长青品类) | meme(网络梗)
- trend_context：≤15 字，解释为何火（如"综艺XX同款"、"入冬涮锅季"）；如果是 evergreen 可为空
- 如果标题没有食物，foods 返回空数组
- 只返回真实存在的食物，不要编造"""
```

- [ ] **Step 3: 修改 `_extract_batch` 的 user_prompt**

找到 `_extract_batch` 函数里的 user_prompt，替换为：

```python
    user_prompt = f"""请对以下热搜标题提取食物 + 归因。

热搜标题：
{titles_text}

请严格按以下 JSON 格式返回（无 markdown，仅 JSON）：
{{"results": [{{"title": "原标题", "foods": [{{"name": "食物名", "category": "分类", "canonical_of": "规范名或本名", "trend_type": "event|seasonal|evergreen|meme", "trend_context": "归因短语"}}]}}]}}

如果某个标题没有食物，其 foods 为空数组。"""
```

- [ ] **Step 4: 改写 `_parse_response` 返回 ExtractedFoodItem**

找到 `_parse_response` 函数，把返回类型从 `tuple[list[FoodTrendItem], dict[...]]` 改为 `tuple[list[ExtractedFoodItem], dict[str, list[dict]]]`。主体替换：

```python
def _parse_response(
    text: str, original_titles: list[str]
) -> tuple[list[ExtractedFoodItem], dict[str, list[dict]]]:
    """解析 Claude 返回的 JSON，返回 items 和 title→foods 映射。"""
    json_text = text.strip()
    if json_text.startswith("```"):
        lines = json_text.split("\n")
        json_lines = []
        in_block = False
        for line in lines:
            if line.strip().startswith("```"):
                in_block = not in_block
                continue
            if in_block:
                json_lines.append(line)
        json_text = "\n".join(json_lines)

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        logger.warning("AI 返回的 JSON 解析失败: %s", text[:200])
        return [], {}

    items: list[ExtractedFoodItem] = []
    title_mapping: dict[str, list[dict]] = {t: [] for t in original_titles}

    for result in data.get("results", []):
        title = result.get("title", "")
        foods_for_title: list[dict] = []
        for food in result.get("foods", []):
            name = food.get("name", "").strip()
            category = food.get("category", "").strip()
            canonical_of = food.get("canonical_of", "").strip() or name
            trend_type = food.get("trend_type", "").strip() or None
            trend_context = food.get("trend_context", "").strip() or None

            if not name or len(name) < 2 or len(name) > 10:
                continue
            if category not in VALID_CATEGORIES:
                category = "小吃"
            if trend_type not in VALID_TREND_TYPES:
                trend_type = None
            if trend_context and len(trend_context) > 15:
                trend_context = trend_context[:15]

            food_dict = {
                "name": name,
                "category": category,
                "canonical_of": canonical_of,
                "trend_type": trend_type,
                "trend_context": trend_context,
            }
            foods_for_title.append(food_dict)
            items.append(ExtractedFoodItem(
                name=name,
                category=category,
                canonical_of=canonical_of,
                trend_type=trend_type,
                trend_context=trend_context,
                source_title=title,
            ))

        if title in title_mapping:
            title_mapping[title] = foods_for_title

    return items, title_mapping
```

注意：**不再在 `_parse_response` 里做 `name in FOOD_NAMES` 过滤**，留给 scheduler 自己决定是否跳过（因为 FOOD_NAMES 里的词也可能是别名需要归并）。

- [ ] **Step 5: 改写 `_load_cached` 返回新类型**

替换 `_load_cached` 函数主体：

```python
def _load_cached(
    db: Session, titles: list[str]
) -> tuple[list[ExtractedFoodItem], list[str]]:
    """从缓存加载已处理标题的结果，返回 (缓存命中的items, 需要调 AI 的titles)。"""
    cached_items: list[ExtractedFoodItem] = []
    uncached_titles: list[str] = []

    for title in titles:
        h = _hash_title(title)
        row = db.execute(
            select(AITitleCache).where(AITitleCache.title_hash == h)
        ).scalar_one_or_none()

        if row:
            try:
                foods = json.loads(row.extracted_foods)
            except json.JSONDecodeError:
                uncached_titles.append(title)
                continue
            for food in foods:
                name = food.get("name", "")
                if not name:
                    continue
                cached_items.append(ExtractedFoodItem(
                    name=name,
                    category=food.get("category"),
                    canonical_of=food.get("canonical_of") or name,
                    trend_type=food.get("trend_type"),
                    trend_context=food.get("trend_context"),
                    source_title=title,
                ))
        else:
            uncached_titles.append(title)

    return cached_items, uncached_titles
```

注意：**兼容旧缓存格式** —— 旧缓存只有 `{name, category}`，新字段用 `.get()` 默认 None，`canonical_of` 默认 = name。

- [ ] **Step 6: 修改 `extract_foods_from_titles` 返回类型**

签名改为 `-> list[ExtractedFoodItem]`；内部聚合逻辑里 `seen: dict[str, ExtractedFoodItem]`；去重 key 仍用 `item.name`。

最终返回前的 `logger.info` 保持不变。

- [ ] **Step 7: 写新测试**

Create `tests/test_ai_extractor_v1_9.py`:

```python
import json
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.crawler.ai_extractor import (
    ExtractedFoodItem,
    _load_cached,
    _parse_response,
    extract_foods_from_titles,
)
from app.database import Base
from app.models import AITitleCache


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_parse_response_returns_canonical_and_trend():
    raw = json.dumps({
        "results": [
            {
                "title": "入冬第一顿火锅",
                "foods": [{
                    "name": "川式火锅",
                    "category": "火锅",
                    "canonical_of": "火锅",
                    "trend_type": "seasonal",
                    "trend_context": "入冬涮锅季",
                }],
            }
        ]
    }, ensure_ascii=False)
    items, mapping = _parse_response(raw, ["入冬第一顿火锅"])
    assert len(items) == 1
    assert items[0].name == "川式火锅"
    assert items[0].canonical_of == "火锅"
    assert items[0].trend_type == "seasonal"
    assert items[0].trend_context == "入冬涮锅季"


def test_parse_response_invalid_trend_type_falls_back_to_none():
    raw = json.dumps({
        "results": [{
            "title": "测试",
            "foods": [{
                "name": "测试食物",
                "category": "小吃",
                "canonical_of": "测试食物",
                "trend_type": "invalid_type",
                "trend_context": "xxx",
            }],
        }]
    }, ensure_ascii=False)
    items, _ = _parse_response(raw, ["测试"])
    assert items[0].trend_type is None


def test_parse_response_trims_context_to_15_chars():
    raw = json.dumps({
        "results": [{
            "title": "测试",
            "foods": [{
                "name": "测试食物",
                "category": "小吃",
                "canonical_of": "测试食物",
                "trend_type": "event",
                "trend_context": "这是一段非常非常非常长的归因说明一共超过十五个字",
            }],
        }]
    }, ensure_ascii=False)
    items, _ = _parse_response(raw, ["测试"])
    assert len(items[0].trend_context) <= 15


def test_load_cached_backward_compat_old_format(db):
    """旧缓存只有 {name, category}，新字段缺失也能正确解析。"""
    old_foods = [{"name": "火锅", "category": "火锅"}]
    db.add(AITitleCache(
        title_hash="a" * 64,
        title="旧缓存标题",
        extracted_foods=json.dumps(old_foods, ensure_ascii=False),
    ))
    db.commit()

    with patch("app.crawler.ai_extractor._hash_title", return_value="a" * 64):
        cached, uncached = _load_cached(db, ["旧缓存标题"])

    assert len(cached) == 1
    assert cached[0].name == "火锅"
    assert cached[0].canonical_of == "火锅"  # 默认等于 name
    assert cached[0].trend_type is None  # 旧格式无此字段


def test_extract_foods_returns_empty_when_ai_disabled():
    with patch("app.crawler.ai_extractor.AI_EXTRACT_ENABLED", False):
        assert extract_foods_from_titles(["任意标题"]) == []


def test_extract_foods_returns_empty_when_no_api_key():
    with patch("app.crawler.ai_extractor.AI_EXTRACT_ENABLED", True), \
         patch("app.crawler.ai_extractor.CLAUDE_API_KEY", ""):
        assert extract_foods_from_titles(["任意标题"]) == []
```

- [ ] **Step 8: 跑测试验证**

```bash
pytest tests/test_ai_extractor_v1_9.py -v
```

Expected: PASS — 6 tests passed.

- [ ] **Step 9: 跑 ai_extractor 原有测试（若存在）确认向后兼容**

```bash
pytest tests/ -k "ai_extractor" -v
```

Expected: PASS — 新老测试都通过。**如果老测试里引用了 `FoodTrendItem` 作为返回值**，需要相应改为 `ExtractedFoodItem`（同类逻辑：改 assert 的字段名）。遇到这种情况在本步更新测试，保持测试通过。

- [ ] **Step 10: 跑全量测试**

```bash
pytest tests/ -v
```

Expected: PASS — 所有测试通过。

- [ ] **Step 11: Commit**

```bash
git add app/crawler/ai_extractor.py tests/test_ai_extractor_v1_9.py
# 若修改了老测试也一起 add
git commit -m "feat: AI extractor 单次调用输出 canonical+trend_type+context"
```

---

## Task 5: Scheduler baidu_suggest 分流 + 候选晋级

**Files:**
- Modify: `app/crawler/scheduler.py`
- Create: `tests/test_scheduler_baidu_routing.py`

- [ ] **Step 1: 写分流测试**

Create `tests/test_scheduler_baidu_routing.py`:

```python
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.crawler.base import FoodTrendItem
from app.crawler.scheduler import _save_candidates, _promote_candidates
from app.database import Base
from app.models import AIDiscoveredFood, FoodTrend


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_save_candidates_writes_to_discovered_foods_not_trends(db):
    items = [
        FoodTrendItem(food_name="火锅", heat_score=100, post_count=10, category="火锅"),
        FoodTrendItem(food_name="奶茶", heat_score=95, post_count=10, category="饮品"),
    ]
    _save_candidates(db, items)

    discovered = db.execute(select(AIDiscoveredFood)).scalars().all()
    trends = db.execute(
        select(FoodTrend).where(FoodTrend.source == "baidu_suggest")
    ).scalars().all()

    assert len(discovered) == 2
    assert {d.food_name for d in discovered} == {"火锅", "奶茶"}
    assert all(d.promoted_to_trends is False for d in discovered)
    assert len(trends) == 0


def test_save_candidates_idempotent_increments_discovery_count(db):
    items = [FoodTrendItem(food_name="火锅", heat_score=100, post_count=10, category="火锅")]
    _save_candidates(db, items)
    _save_candidates(db, items)

    row = db.execute(
        select(AIDiscoveredFood).where(AIDiscoveredFood.food_name == "火锅")
    ).scalar_one()
    assert row.discovery_count == 2


def test_promote_candidates_only_promotes_when_other_sources_have_it(db):
    # 候选"奶茶"但没有其他源也有 → 不晋级
    # 候选"火锅"且有 toutiao 源的火锅 → 晋级
    db.add(FoodTrend(
        food_name="火锅", source="toutiao",
        heat_score=90, post_count=1000,
        canonical_name="火锅",
    ))
    db.add(AIDiscoveredFood(food_name="火锅", category="火锅"))
    db.add(AIDiscoveredFood(food_name="奶茶", category="饮品"))
    db.commit()

    _promote_candidates(db)

    # 火锅 baidu_suggest 行应被创建
    promoted = db.execute(
        select(FoodTrend).where(
            FoodTrend.source == "baidu_suggest",
            FoodTrend.food_name == "火锅",
        )
    ).scalar_one_or_none()
    assert promoted is not None
    # heat_score 应为其他源 × 0.8
    assert promoted.heat_score == int(90 * 0.8)
    # 候选标记已晋级
    hotpot = db.execute(
        select(AIDiscoveredFood).where(AIDiscoveredFood.food_name == "火锅")
    ).scalar_one()
    assert hotpot.promoted_to_trends is True
    # 奶茶无其他源 → 不晋级
    milk_tea = db.execute(
        select(AIDiscoveredFood).where(AIDiscoveredFood.food_name == "奶茶")
    ).scalar_one()
    assert milk_tea.promoted_to_trends is False


def test_promote_candidates_idempotent_skips_already_promoted(db):
    db.add(FoodTrend(
        food_name="火锅", source="toutiao",
        heat_score=90, post_count=1000, canonical_name="火锅",
    ))
    db.add(AIDiscoveredFood(food_name="火锅", category="火锅", promoted_to_trends=True))
    db.commit()

    _promote_candidates(db)

    promoted = db.execute(
        select(FoodTrend).where(
            FoodTrend.source == "baidu_suggest",
            FoodTrend.food_name == "火锅",
        )
    ).scalar_one_or_none()
    assert promoted is None
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_scheduler_baidu_routing.py -v
```

Expected: FAIL — `_save_candidates` / `_promote_candidates` 未定义。

- [ ] **Step 3: 在 `scheduler.py` 新增 `_save_candidates`**

在 `app/crawler/scheduler.py` 里，`_save_ai_discoveries` 函数后面新增：

```python
def _save_candidates(db: Session, items: list[FoodTrendItem]) -> None:
    """把候选源（如 baidu_suggest）的 items 写入 ai_discovered_foods，不入主表。"""
    for item in items:
        existing = db.execute(
            select(AIDiscoveredFood).where(AIDiscoveredFood.food_name == item.food_name)
        ).scalar_one_or_none()
        if existing:
            existing.discovery_count += 1
        else:
            db.add(AIDiscoveredFood(
                food_name=item.food_name,
                category=item.category,
            ))
    db.commit()


def _promote_candidates(db: Session) -> None:
    """把有其他源佐证的候选词晋级到 food_trends（source='baidu_suggest'）。"""
    pending = db.execute(
        select(AIDiscoveredFood).where(AIDiscoveredFood.promoted_to_trends.is_(False))
    ).scalars().all()

    for candidate in pending:
        # 查该食物在其他源的最高热度
        other_src_max = db.execute(
            select(FoodTrend.heat_score).where(
                FoodTrend.food_name == candidate.food_name,
                FoodTrend.source != "baidu_suggest",
            ).order_by(FoodTrend.heat_score.desc()).limit(1)
        ).scalar_one_or_none()

        if other_src_max is None:
            continue  # 无其他源佐证，继续观察

        # 晋级：写入 baidu_suggest 行（若已存在则更新）
        existing_bs = db.execute(
            select(FoodTrend).where(
                FoodTrend.food_name == candidate.food_name,
                FoodTrend.source == "baidu_suggest",
            )
        ).scalar_one_or_none()
        new_score = int(other_src_max * 0.8)
        if existing_bs:
            existing_bs.heat_score = new_score
            existing_bs.updated_at = datetime.now(timezone.utc)
        else:
            db.add(FoodTrend(
                food_name=candidate.food_name,
                source="baidu_suggest",
                heat_score=new_score,
                post_count=candidate.discovery_count,
                category=candidate.category,
                canonical_name=candidate.food_name,
            ))
        candidate.promoted_to_trends = True

    db.commit()
```

- [ ] **Step 4: 修改 `run_all_crawlers` 分流 baidu_suggest**

在 `run_all_crawlers` 函数里找到 for 循环体，改为：

```python
    for crawler in ALL_CRAWLERS:
        source = crawler.get_source_name()
        try:
            items = crawler.crawl()
            if source == "baidu_suggest":
                _save_candidates(db, items)
                saved = len(items)
                message = f"百度候选写入 ai_discovered_foods: {saved} 条"
            else:
                saved = _save_items(db, source, items)
                message = f"抓取完成，保存{saved}条"
            all_unmatched.extend(crawler.unmatched_titles)
            db.add(
                CrawlLog(source=source, status="success", items_count=saved)
            )
            db.commit()
            results.append(
                CrawlResult(
                    source=source,
                    status="success",
                    items_count=saved,
                    message=message,
                )
            )
            logger.info("爬虫 %s 完成: %d 条", source, saved)
        except Exception as e:
            # ... 原有异常处理保持不变 ...
```

（完整的 except 分支保持原样不动）

- [ ] **Step 5: 在 AI 提取完成后调用 `_promote_candidates`**

在 `run_all_crawlers` 末尾，`_save_daily_snapshot(db)` 之前插入：

```python
    # 候选词晋级：baidu_suggest 候选 + 其他源佐证 → 进主表
    try:
        _promote_candidates(db)
    except Exception:
        logger.error("候选词晋级失败", exc_info=True)
```

- [ ] **Step 6: 跑测试验证**

```bash
pytest tests/test_scheduler_baidu_routing.py -v
```

Expected: PASS — 4 tests passed.

- [ ] **Step 7: 跑全量测试**

```bash
pytest tests/ -v
```

Expected: PASS — 全部通过。**如果 `tests/test_scheduler.py` 里有断言 baidu_suggest 写入 food_trends 的测试**，需要更新对应测试断言（改为断言写入 ai_discovered_foods）。

- [ ] **Step 8: Commit**

```bash
git add app/crawler/scheduler.py tests/test_scheduler_baidu_routing.py
# 若改了老测试也一起
git commit -m "feat: baidu_suggest 降级为候选源 + 候选晋级逻辑"
```

---

## Task 6: Scheduler — ExtractedFoodItem 落库 (alias + trend 字段)

**Files:**
- Modify: `app/crawler/scheduler.py`
- Create: `tests/test_scheduler_extracted_save.py`

- [ ] **Step 1: 写测试**

Create `tests/test_scheduler_extracted_save.py`:

```python
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.crawler.ai_extractor import ExtractedFoodItem
from app.crawler.scheduler import _save_extracted_items
from app.database import Base
from app.models import FoodAlias, FoodTrend


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_save_extracted_writes_trend_fields(db):
    items = [ExtractedFoodItem(
        name="围炉煮茶",
        category="饮品",
        canonical_of="围炉煮茶",
        trend_type="seasonal",
        trend_context="入冬社交茶饮",
        source_title="入冬第一波围炉煮茶火了",
    )]
    _save_extracted_items(db, items)

    row = db.execute(
        select(FoodTrend).where(
            FoodTrend.food_name == "围炉煮茶",
            FoodTrend.source == "ai_extract",
        )
    ).scalar_one()
    assert row.canonical_name == "围炉煮茶"
    assert row.trend_type == "seasonal"
    assert row.trend_context == "入冬社交茶饮"


def test_save_extracted_writes_alias_when_canonical_differs(db):
    items = [ExtractedFoodItem(
        name="川式火锅",
        category="火锅",
        canonical_of="火锅",
        trend_type="seasonal",
        trend_context="入冬涮锅季",
    )]
    _save_extracted_items(db, items)

    alias = db.execute(
        select(FoodAlias).where(FoodAlias.alias_name == "川式火锅")
    ).scalar_one()
    assert alias.canonical_name == "火锅"
    assert alias.created_by == "ai"

    trend = db.execute(
        select(FoodTrend).where(FoodTrend.food_name == "川式火锅")
    ).scalar_one()
    assert trend.canonical_name == "火锅"


def test_save_extracted_no_duplicate_alias_when_canonical_equals_name(db):
    items = [ExtractedFoodItem(
        name="奶茶",
        category="饮品",
        canonical_of="奶茶",  # 自引用
        trend_type="evergreen",
        trend_context=None,
    )]
    _save_extracted_items(db, items)

    aliases = db.execute(
        select(FoodAlias).where(FoodAlias.alias_name == "奶茶")
    ).scalars().all()
    # 不主动插入自引用 alias（由 backfill 脚本负责，或当其他人提及时才插）
    # 但若已存在也不重复
    assert len(aliases) <= 1


def test_save_extracted_is_idempotent_on_existing_trend(db):
    items = [ExtractedFoodItem(
        name="围炉煮茶",
        category="饮品",
        canonical_of="围炉煮茶",
        trend_type="seasonal",
        trend_context="入冬社交茶饮",
    )]
    _save_extracted_items(db, items)
    _save_extracted_items(db, items)  # 二次调用

    rows = db.execute(
        select(FoodTrend).where(
            FoodTrend.food_name == "围炉煮茶",
            FoodTrend.source == "ai_extract",
        )
    ).scalars().all()
    assert len(rows) == 1  # 只有一条（upsert）
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_scheduler_extracted_save.py -v
```

Expected: FAIL — `_save_extracted_items` 未定义。

- [ ] **Step 3: 实现 `_save_extracted_items`**

在 `app/crawler/scheduler.py` 顶部 import 区补：

```python
from app.crawler.ai_extractor import ExtractedFoodItem
from app.models import AIDiscoveredFood, CrawlLog, FoodAlias, FoodTrend, FoodTrendSnapshot, Recipe
```

（FoodAlias 新 import）

在 `_save_ai_discoveries` 函数之后新增：

```python
def _save_extracted_items(db: Session, items: list[ExtractedFoodItem]) -> int:
    """把 AI 提取的 ExtractedFoodItem 写入 food_trends (source='ai_extract')。

    同时处理：
    - 若 canonical_of != name → 插入 food_aliases (created_by='ai')
    - food_trends.canonical_name 写入 canonical_of
    - trend_type/trend_context 写入对应列
    """
    count = 0
    for item in items:
        # 处理 alias
        if item.canonical_of and item.canonical_of != item.name:
            existing_alias = db.execute(
                select(FoodAlias).where(FoodAlias.alias_name == item.name)
            ).scalar_one_or_none()
            if not existing_alias:
                db.add(FoodAlias(
                    alias_name=item.name,
                    canonical_name=item.canonical_of,
                    created_by="ai",
                ))

        # 写 food_trends
        existing = db.execute(
            select(FoodTrend).where(
                FoodTrend.food_name == item.name,
                FoodTrend.source == "ai_extract",
            )
        ).scalar_one_or_none()

        canonical = item.canonical_of or item.name

        if existing:
            existing.category = item.category or existing.category
            existing.canonical_name = canonical
            existing.trend_type = item.trend_type or existing.trend_type
            existing.trend_context = item.trend_context or existing.trend_context
            existing.updated_at = datetime.now(timezone.utc)
        else:
            db.add(FoodTrend(
                food_name=item.name,
                source="ai_extract",
                heat_score=50,  # AI 提取默认分数
                post_count=0,
                category=item.category,
                canonical_name=canonical,
                trend_type=item.trend_type,
                trend_context=item.trend_context,
            ))
        count += 1

    db.commit()
    return count
```

- [ ] **Step 4: 在 `run_all_crawlers` 里用 `_save_extracted_items` 替换旧的 AI 提取保存逻辑**

找到 `run_all_crawlers` 中的 AI 提取分支：

```python
        if all_unmatched:
            ai_items = extract_foods_from_titles(all_unmatched)
            if ai_items:
                saved = _save_items(db, "ai_extract", ai_items)  # 旧逻辑
                _save_ai_discoveries(db, ai_items)
```

改为：

```python
        if all_unmatched:
            ai_items = extract_foods_from_titles(all_unmatched)
            if ai_items:
                saved = _save_extracted_items(db, ai_items)
                # AIDiscoveredFood 记录保持（用于长期统计/词典扩充）
                _save_ai_discoveries_from_extracted(db, ai_items)
```

同时把原有的 `_save_ai_discoveries` 函数改名为 `_save_ai_discoveries_from_extracted` 并更新签名：

```python
def _save_ai_discoveries_from_extracted(
    db: Session, items: list[ExtractedFoodItem]
) -> None:
    """记录 AI 发现的新食物到 ai_discovered_foods 表。"""
    for item in items:
        existing = db.execute(
            select(AIDiscoveredFood).where(
                AIDiscoveredFood.food_name == item.name
            )
        ).scalar_one_or_none()
        if existing:
            existing.discovery_count += 1
        else:
            db.add(AIDiscoveredFood(
                food_name=item.name,
                category=item.category,
            ))
    db.commit()
```

- [ ] **Step 5: 删除或弃用旧的 `_save_items` 对 "ai_extract" source 的使用**

`_save_items` 函数本身保留给其他爬虫用（toutiao / dailyhot / manual）。但 ai_extract 走新路径，不再调 `_save_items(db, "ai_extract", ...)`。Step 4 已完成这个切换。

- [ ] **Step 6: 跑测试验证**

```bash
pytest tests/test_scheduler_extracted_save.py -v
```

Expected: PASS — 4 tests passed.

- [ ] **Step 7: 跑全量测试**

```bash
pytest tests/ -v
```

Expected: PASS — 所有通过。**若老测试断言 `_save_ai_discoveries(db, items)` 使用 `FoodTrendItem`**，更新断言用 `ExtractedFoodItem` 或导入新函数名。

- [ ] **Step 8: Commit**

```bash
git add app/crawler/scheduler.py tests/test_scheduler_extracted_save.py
git commit -m "feat: AI 提取落库支持 canonical/trend_type/context + alias 同步写入"
```

---

## Task 7: AI Digest prompt 升级 — 使用归因字段

**Files:**
- Modify: `app/crawler/ai_digest.py`
- Modify: `tests/test_ai_digest.py`（若存在）或 Create: `tests/test_ai_digest_v1_9.py`

- [ ] **Step 1: 写测试**

Create `tests/test_ai_digest_v1_9.py`:

```python
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.crawler.ai_digest import generate_daily_digest
from app.database import Base
from app.models import FoodDigest, FoodTrend


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_digest_prompt_includes_trend_type_and_context(db):
    db.add(FoodTrend(
        food_name="围炉煮茶", source="toutiao",
        heat_score=95, post_count=1000, category="饮品",
        canonical_name="围炉煮茶",
        trend_type="seasonal", trend_context="入冬社交茶饮",
    ))
    db.add(FoodTrend(
        food_name="奶茶", source="dailyhot",
        heat_score=90, post_count=800, category="饮品",
        canonical_name="奶茶",
        trend_type="evergreen", trend_context=None,
    ))
    db.commit()

    with patch("app.crawler.ai_digest.CLAUDE_API_KEY", "fake-key"), \
         patch("app.crawler.ai_digest.Anthropic") as mock_anth:
        mock_client = MagicMock()
        mock_anth.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text='{"summary":"s","top_foods":["围炉煮茶"],"recommendation":"喝茶"}')]
        mock_client.messages.create.return_value = mock_resp

        generate_daily_digest(db)

        call_args = mock_client.messages.create.call_args
        user_content = call_args.kwargs["messages"][0]["content"]
        # 确认归因字段已传入 prompt
        assert "type:seasonal" in user_content
        assert "入冬社交茶饮" in user_content
        assert "type:evergreen" in user_content


def test_digest_system_prompt_explains_trend_types(db):
    db.add(FoodTrend(
        food_name="火锅", source="toutiao",
        heat_score=90, post_count=1000, category="火锅",
        canonical_name="火锅",
    ))
    db.commit()

    with patch("app.crawler.ai_digest.CLAUDE_API_KEY", "fake-key"), \
         patch("app.crawler.ai_digest.Anthropic") as mock_anth:
        mock_client = MagicMock()
        mock_anth.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text='{"summary":"s","top_foods":[],"recommendation":""}')]
        mock_client.messages.create.return_value = mock_resp

        generate_daily_digest(db)

        call_args = mock_client.messages.create.call_args
        system_prompt = call_args.kwargs["system"]
        # 确认 system prompt 解释了归因类型
        assert "event" in system_prompt
        assert "seasonal" in system_prompt
        assert "evergreen" in system_prompt
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_ai_digest_v1_9.py -v
```

Expected: FAIL — prompt 里没有 `type:` 和 `evergreen` 等字样。

- [ ] **Step 3: 重写 `_SYSTEM_PROMPT`**

在 `app/crawler/ai_digest.py` 中替换 `_SYSTEM_PROMPT` 定义：

```python
_SYSTEM_PROMPT = f"""{AI_CORE_RULES}

你是一位美食趋势分析师。根据今日各平台热搜美食数据，生成一份简洁的美食趋势快报。

数据说明：每条记录带 trend_type 和 trend_context 标注。
- type=event: 事件/综艺/直播带火，context 是关联事件（引用可让叙事更生动）
- type=seasonal: 季节相关，context 是节气/季节关联
- type=evergreen: 长青品类，无需特殊归因
- type=meme: 网络梗/社交话题
- type=None 或 context=None: 未标注，按常识解读

要求：
1. 总结当前最火的 3-5 种美食，结合 trend_context 说明"为什么火"（而非仅列热度数字）
2. 发现趋势变化：哪些食物在上升、哪些在下降
3. 给出一句话"今日推荐"，适合当天吃的食物建议
4. 风格轻松有趣，适合年轻人阅读，100-200 字即可
5. 只分析提供的数据，不要编造数据中没有的食物

返回格式（纯 JSON，无 markdown）：
{{"summary": "趋势快报正文", "top_foods": ["食物1", "食物2", "食物3"], "recommendation": "今日推荐一句话"}}"""
```

- [ ] **Step 4: 修改 `generate_daily_digest` 的 data_lines 构建**

找到 `data_lines` 的构建循环：

```python
    data_lines = []
    for item in top_items:
        data_lines.append(
            f"- {item.food_name}（{item.category or '未分类'}）"
            f" 热度:{item.heat_score} 来源:{item.source}"
        )
```

替换为：

```python
    data_lines = []
    for item in top_items:
        type_str = f" type:{item.trend_type}" if item.trend_type else " type:None"
        ctx_str = f" context:{item.trend_context}" if item.trend_context else ""
        data_lines.append(
            f"- {item.food_name}（{item.category or '未分类'}）"
            f" 热度:{item.heat_score} 来源:{item.source}{type_str}{ctx_str}"
        )
```

- [ ] **Step 5: 跑测试验证**

```bash
pytest tests/test_ai_digest_v1_9.py -v
```

Expected: PASS — 2 tests passed.

- [ ] **Step 6: 跑全量测试**

```bash
pytest tests/ -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/crawler/ai_digest.py tests/test_ai_digest_v1_9.py
git commit -m "feat: digest prompt 接入 trend_type/context 归因字段"
```

---

## Task 8: Trending endpoint 聚合 + digest fallback

**Files:**
- Modify: `app/routers/trending.py`
- Create: `tests/test_trending_endpoint_v1_9.py`

- [ ] **Step 1: 写测试**

Create `tests/test_trending_endpoint_v1_9.py`:

```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.main import app
from app.models import FoodDigest, FoodTrend


@pytest.fixture
def client():
    from app.database import engine as app_engine
    Base.metadata.create_all(app_engine)
    with TestClient(app) as c:
        yield c
    with Session(app_engine) as s:
        s.query(FoodTrend).delete()
        s.query(FoodDigest).delete()
        s.commit()


def test_trending_aggregate_default_dedupes_canonical_name(client):
    from app.database import engine as app_engine
    with Session(app_engine) as s:
        s.add(FoodTrend(
            food_name="烧烤", source="baidu_suggest",
            heat_score=100, post_count=10,
            canonical_name="烧烤", category="烧烤",
        ))
        s.add(FoodTrend(
            food_name="烧烤", source="dailyhot",
            heat_score=95, post_count=7_000_000,
            canonical_name="烧烤", category="烧烤",
        ))
        s.commit()

    resp = client.get("/api/trending?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    names = [i["food_name"] for i in data["items"]]
    # 烧烤去重后只出现一次
    assert names.count("烧烤") == 1


def test_trending_aggregate_items_contain_aliases_and_sources(client):
    from app.database import engine as app_engine
    with Session(app_engine) as s:
        s.add(FoodTrend(
            food_name="川式火锅", source="toutiao",
            heat_score=90, post_count=500,
            canonical_name="火锅", category="火锅",
            trend_type="seasonal", trend_context="入冬涮锅季",
        ))
        s.add(FoodTrend(
            food_name="重庆火锅", source="dailyhot",
            heat_score=88, post_count=400,
            canonical_name="火锅", category="火锅",
        ))
        s.commit()

    resp = client.get("/api/trending?limit=5")
    data = resp.json()
    items = [i for i in data["items"] if i.get("canonical_name") == "火锅"]
    assert len(items) == 1
    assert set(items[0]["aliases"]) == {"川式火锅", "重庆火锅"}
    assert set(items[0]["sources"]) == {"toutiao", "dailyhot"}
    # 归因信息保留至少一条
    assert items[0]["trend_type"] in ("seasonal", None)


def test_trending_aggregate_false_returns_raw_rows(client):
    from app.database import engine as app_engine
    with Session(app_engine) as s:
        s.add(FoodTrend(
            food_name="烧烤", source="baidu_suggest",
            heat_score=100, post_count=10, canonical_name="烧烤",
        ))
        s.add(FoodTrend(
            food_name="烧烤", source="dailyhot",
            heat_score=95, post_count=7_000_000, canonical_name="烧烤",
        ))
        s.commit()

    resp = client.get("/api/trending?limit=5&aggregate=false")
    data = resp.json()
    names = [i["food_name"] for i in data["items"]]
    assert names.count("烧烤") == 2


def test_trending_total_reflects_canonical_count_when_aggregate(client):
    from app.database import engine as app_engine
    with Session(app_engine) as s:
        # 3 行但只有 2 个 canonical
        s.add(FoodTrend(food_name="烧烤", source="a", heat_score=100, post_count=0, canonical_name="烧烤"))
        s.add(FoodTrend(food_name="烧烤", source="b", heat_score=95, post_count=0, canonical_name="烧烤"))
        s.add(FoodTrend(food_name="奶茶", source="a", heat_score=90, post_count=0, canonical_name="奶茶"))
        s.commit()

    resp = client.get("/api/trending?limit=10")
    data = resp.json()
    assert data["total"] == 2  # 去重后


def test_digest_fallback_to_latest_when_no_date_param(client):
    from datetime import datetime
    from app.database import engine as app_engine
    with Session(app_engine) as s:
        s.add(FoodDigest(
            digest_date=datetime(2026, 4, 23, 0, 0),
            summary="昨日快报",
            top_foods='["火锅"]',
            recommendation="吃火锅",
        ))
        s.add(FoodDigest(
            digest_date=datetime(2026, 4, 24, 0, 0),
            summary="今日快报",
            top_foods='["奶茶"]',
            recommendation="喝奶茶",
        ))
        s.commit()

    resp = client.get("/api/trending/digest")
    assert resp.status_code == 200
    data = resp.json()
    assert data is not None
    assert data["summary"] == "今日快报"  # 最新一条


def test_digest_exact_date_still_works(client):
    from datetime import datetime
    from app.database import engine as app_engine
    with Session(app_engine) as s:
        s.add(FoodDigest(
            digest_date=datetime(2026, 4, 23, 0, 0),
            summary="昨日快报",
            top_foods='["火锅"]',
        ))
        s.commit()

    resp = client.get("/api/trending/digest?date=2026-04-23")
    data = resp.json()
    assert data["summary"] == "昨日快报"


def test_digest_returns_null_when_table_empty(client):
    resp = client.get("/api/trending/digest")
    assert resp.status_code == 200
    assert resp.json() is None
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_trending_endpoint_v1_9.py -v
```

Expected: FAIL — aggregate 参数未实现、fallback 未实现。

- [ ] **Step 3: 改写 `get_trending` 端点**

替换 `app/routers/trending.py` 中的 `get_trending` 函数：

```python
from sqlalchemy import func, select


@router.get("", response_model=TrendingResponse)
def get_trending(
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    source: str | None = Query(None),
    category: str | None = Query(None),
    aggregate: bool = Query(True, description="按 canonical_name 聚合去重"),
    db: Session = Depends(get_db),
):
    if aggregate:
        return _get_trending_aggregated(db, limit, offset, source, category)
    return _get_trending_raw(db, limit, offset, source, category)


def _get_trending_raw(
    db: Session, limit: int, offset: int,
    source: str | None, category: str | None,
) -> TrendingResponse:
    stmt = select(FoodTrend)
    count_stmt = select(func.count(FoodTrend.id))
    if source:
        stmt = stmt.where(FoodTrend.source == source)
        count_stmt = count_stmt.where(FoodTrend.source == source)
    if category:
        stmt = stmt.where(FoodTrend.category == category)
        count_stmt = count_stmt.where(FoodTrend.category == category)

    total = db.execute(count_stmt).scalar() or 0
    items = db.execute(
        stmt.order_by(FoodTrend.heat_score.desc()).offset(offset).limit(limit)
    ).scalars().all()
    return TrendingResponse(total=total, items=items)


def _get_trending_aggregated(
    db: Session, limit: int, offset: int,
    source: str | None, category: str | None,
) -> TrendingResponse:
    # 先拉所有满足 filter 的行（再 Python 聚合避免复杂 SQL）
    stmt = select(FoodTrend)
    if source:
        stmt = stmt.where(FoodTrend.source == source)
    if category:
        stmt = stmt.where(FoodTrend.category == category)

    all_rows = db.execute(stmt).scalars().all()

    # 按 canonical_name (fallback to food_name) 聚合
    groups: dict[str, list[FoodTrend]] = {}
    for row in all_rows:
        key = row.canonical_name or row.food_name
        groups.setdefault(key, []).append(row)

    # 每组按 heat_score 最高的代表；聚合 aliases/sources/trend
    aggregated: list[FoodTrendOut] = []
    for canonical, rows in groups.items():
        top = max(rows, key=lambda r: r.heat_score)
        aliases = sorted({r.food_name for r in rows})
        sources = sorted({r.source for r in rows})
        trend_type = next((r.trend_type for r in rows if r.trend_type), None)
        trend_context = next((r.trend_context for r in rows if r.trend_context), None)
        aggregated.append(FoodTrendOut(
            id=top.id,
            food_name=top.food_name,
            source=top.source,
            heat_score=top.heat_score,
            post_count=sum(r.post_count for r in rows),
            category=top.category,
            image_url=top.image_url,
            updated_at=top.updated_at,
            canonical_name=canonical,
            aliases=aliases,
            sources=sources,
            trend_type=trend_type,
            trend_context=trend_context,
        ))

    aggregated.sort(key=lambda x: x.heat_score, reverse=True)
    total = len(aggregated)
    page = aggregated[offset : offset + limit]
    return TrendingResponse(total=total, items=page)
```

（注意从 schemas 补 import：`from app.schemas import FoodTrendOut`。若已 import 的 TrendingResponse 已经引了 FoodTrendOut 间接使用，直接 `from app.schemas import FoodTrendOut, TrendingResponse` 再次 import 即可。）

- [ ] **Step 4: 改写 `get_digest` 端点实现 fallback**

替换 `get_digest` 函数：

```python
@router.get("/digest", response_model=FoodDigestOut | None)
def get_digest(
    target_date: date | None = Query(None, alias="date"),
    db: Session = Depends(get_db),
):
    """获取美食趋势快报。无 date 参数时 fallback 到最新一条。"""
    if target_date is None:
        digest = db.execute(
            select(FoodDigest).order_by(FoodDigest.digest_date.desc()).limit(1)
        ).scalar_one_or_none()
    else:
        target_dt = datetime.combine(target_date, time.min)
        digest = db.execute(
            select(FoodDigest).where(FoodDigest.digest_date == target_dt)
        ).scalar_one_or_none()

    if not digest:
        return None

    return FoodDigestOut(
        id=digest.id,
        digest_date=digest.digest_date,
        summary=digest.summary,
        top_foods=json.loads(digest.top_foods),
        recommendation=digest.recommendation,
        updated_at=digest.updated_at,
    )
```

- [ ] **Step 5: 跑测试验证**

```bash
pytest tests/test_trending_endpoint_v1_9.py -v
```

Expected: PASS — 7 tests passed.

- [ ] **Step 6: 跑全量测试**

```bash
pytest tests/ -v
```

Expected: PASS — 全部通过。**若老测试依赖 trending endpoint 返回的 items 里没有 canonical_name 等新字段，不会失败（新字段默认 None，老 assert 仍成立）**。

- [ ] **Step 7: Commit**

```bash
git add app/routers/trending.py tests/test_trending_endpoint_v1_9.py
git commit -m "feat: trending 按 canonical 聚合 + digest fallback 到最新"
```

---

## Task 9: Admin merge-aliases 端点（历史数据 AI 合并入口）

**Files:**
- Create: `app/routers/admin.py`
- Modify: `app/main.py`（注册 router）
- Create: `tests/test_admin_merge_aliases.py`

- [ ] **Step 1: 写测试**

Create `tests/test_admin_merge_aliases.py`:

```python
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import Base
from app.main import app
from app.models import FoodAlias, FoodTrend


@pytest.fixture
def client():
    from app.database import engine as app_engine
    Base.metadata.create_all(app_engine)
    with TestClient(app) as c:
        yield c
    with Session(app_engine) as s:
        s.query(FoodTrend).delete()
        s.query(FoodAlias).delete()
        s.commit()


def test_merge_aliases_endpoint_returns_200(client):
    with patch("app.routers.admin.Anthropic") as mock_anth, \
         patch("app.routers.admin.CLAUDE_API_KEY", "fake-key"):
        mock_client = MagicMock()
        mock_anth.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text='{"groups":[]}')]
        mock_client.messages.create.return_value = mock_resp

        resp = client.post("/api/admin/merge-aliases")
        assert resp.status_code == 200


def test_merge_aliases_writes_alias_and_updates_canonical(client):
    from app.database import engine as app_engine
    with Session(app_engine) as s:
        s.add(FoodTrend(food_name="川式火锅", source="toutiao", heat_score=90, post_count=0, canonical_name="川式火锅"))
        s.add(FoodTrend(food_name="重庆火锅", source="dailyhot", heat_score=88, post_count=0, canonical_name="重庆火锅"))
        s.add(FoodTrend(food_name="火锅", source="manual", heat_score=80, post_count=0, canonical_name="火锅"))
        s.commit()

    ai_response = '{"groups":[{"canonical":"火锅","aliases":["川式火锅","重庆火锅"]}]}'
    with patch("app.routers.admin.Anthropic") as mock_anth, \
         patch("app.routers.admin.CLAUDE_API_KEY", "fake-key"):
        mock_client = MagicMock()
        mock_anth.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text=ai_response)]
        mock_client.messages.create.return_value = mock_resp

        resp = client.post("/api/admin/merge-aliases")
        data = resp.json()

    assert data["status"] == "ok"
    assert data["groups_processed"] >= 1

    with Session(app_engine) as s:
        aliases = {
            a.alias_name: a.canonical_name
            for a in s.execute(select(FoodAlias)).scalars().all()
        }
        assert aliases.get("川式火锅") == "火锅"
        assert aliases.get("重庆火锅") == "火锅"

        trends = s.execute(select(FoodTrend)).scalars().all()
        for t in trends:
            if t.food_name in ("川式火锅", "重庆火锅"):
                assert t.canonical_name == "火锅"


def test_merge_aliases_no_api_key_returns_error(client):
    with patch("app.routers.admin.CLAUDE_API_KEY", ""):
        resp = client.post("/api/admin/merge-aliases")
        assert resp.status_code == 503
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_admin_merge_aliases.py -v
```

Expected: FAIL — `/api/admin/merge-aliases` 端点不存在。

- [ ] **Step 3: 实现 admin router**

Create `app/routers/admin.py`:

```python
"""管理端点 — 人工触发的一次性或低频操作（如 AI 别名合并）。"""

import json
import logging
from datetime import datetime, timezone

from anthropic import Anthropic
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AI_CORE_RULES, CLAUDE_API_KEY, CLAUDE_MODEL
from app.database import get_db
from app.models import FoodAlias, FoodTrend

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

_MERGE_BATCH_SIZE = 50

_MERGE_SYSTEM_PROMPT = f"""{AI_CORE_RULES}

你是一个食物同义词归并专家。给你一批食物名，请识别其中哪些是同一食物的别名或语义同类，输出规范化分组。

规则：
- 同一食物的变体归为一组（如"川式火锅"、"重庆火锅"、"四川火锅" → canonical="火锅"）
- canonical 必须是该组中最通用、最短的规范名
- 只归并语义上明确同类的词；若有疑虑，独立成组（每个词自成 canonical）
- 不要把不同食物强行归组

返回格式（纯 JSON，无 markdown）：
{{"groups": [{{"canonical": "火锅", "aliases": ["川式火锅", "重庆火锅"]}}, ...]}}
"""


@router.post("/merge-aliases")
def merge_aliases(db: Session = Depends(get_db)) -> dict:
    """扫描 food_trends 里所有 food_name，用 AI 生成 alias → canonical 映射。"""
    if not CLAUDE_API_KEY:
        raise HTTPException(status_code=503, detail="CLAUDE_API_KEY 未配置")

    names = sorted({
        row for row in db.execute(
            select(FoodTrend.food_name).distinct()
        ).scalars().all()
    })

    if not names:
        return {"status": "ok", "groups_processed": 0, "aliases_created": 0}

    client = Anthropic(api_key=CLAUDE_API_KEY)
    groups_processed = 0
    aliases_created = 0

    for i in range(0, len(names), _MERGE_BATCH_SIZE):
        batch = names[i:i + _MERGE_BATCH_SIZE]
        try:
            batch_groups = _call_merge(client, batch)
        except Exception:
            logger.error("batch %d 合并失败", i // _MERGE_BATCH_SIZE, exc_info=True)
            continue

        for group in batch_groups:
            canonical = group.get("canonical", "").strip()
            aliases = group.get("aliases", [])
            if not canonical:
                continue
            groups_processed += 1
            for alias in aliases:
                alias = alias.strip()
                if not alias or alias == canonical:
                    continue
                # upsert food_aliases
                existing = db.execute(
                    select(FoodAlias).where(FoodAlias.alias_name == alias)
                ).scalar_one_or_none()
                if existing:
                    existing.canonical_name = canonical
                    existing.created_by = "ai"
                else:
                    db.add(FoodAlias(
                        alias_name=alias,
                        canonical_name=canonical,
                        created_by="ai",
                    ))
                    aliases_created += 1
                # 同步更新 food_trends.canonical_name
                db.execute(
                    FoodTrend.__table__.update()
                    .where(FoodTrend.food_name == alias)
                    .values(canonical_name=canonical, updated_at=datetime.now(timezone.utc))
                )

        db.commit()

    return {
        "status": "ok",
        "groups_processed": groups_processed,
        "aliases_created": aliases_created,
        "total_names_scanned": len(names),
    }


def _call_merge(client: Anthropic, batch: list[str]) -> list[dict]:
    user_prompt = "请归并以下食物名（找出同义/变体）：\n" + "\n".join(
        f"- {n}" for n in batch
    )
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,
        system=_MERGE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines)
    data = json.loads(raw)
    return data.get("groups", [])
```

- [ ] **Step 4: 在 `main.py` 注册 admin router**

在 `app/main.py` 中：

```python
from app.routers import trending, recommend, recipe  # 原行
```

改为：

```python
from app.routers import admin, trending, recommend, recipe
```

并在 `app.include_router(recipe.router)` 下面加一行：

```python
app.include_router(admin.router)
```

- [ ] **Step 5: 跑测试验证**

```bash
pytest tests/test_admin_merge_aliases.py -v
```

Expected: PASS — 3 tests passed.

- [ ] **Step 6: 跑全量测试**

```bash
pytest tests/ -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/routers/admin.py app/main.py tests/test_admin_merge_aliases.py
git commit -m "feat: 新增 POST /api/admin/merge-aliases 端点（AI 历史别名合并）"
```

---

## Task 10: 版本升级 + 全量测试 + coverage

**Files:**
- Modify: `app/config.py`

- [ ] **Step 1: 版本号 → 1.9.0**

在 `app/config.py` 修改：

```python
APP_VERSION = "1.9.0"
```

- [ ] **Step 2: 跑全量测试 + coverage**

```bash
pytest tests/ --cov=app --cov-report=term-missing -v
```

Expected: 
- 全部测试通过（约 244+ tests）
- Coverage ≥ 97%（符合历史基线）

若 coverage 掉到 97% 以下，补测试到未覆盖的分支（常见未覆盖：异常路径、fallback 分支）。

- [ ] **Step 3: Commit**

```bash
git add app/config.py
git commit -m "feat: bump APP_VERSION 到 1.9.0"
```

- [ ] **Step 4: Push 到 GitHub**

```bash
git push origin main
```

Expected: push 成功 → GitHub Actions 自动启动 Docker 构建。

- [ ] **Step 5: 等待 CI 构建**

等 Docker Hub 出现新镜像（~3 min）。可在 GitHub Actions 页面确认，或：

```bash
git rev-parse HEAD
```

记下 SHA（后面部署 compose 需要）。

---

## Task 11: NAS 部署（生产环境）

**Files:**
- 远程：`/zspace/applications/services/zdocker/config/compose_config/daodichishayou-backend.yaml`

⚠️ **全局教训（来自 tool_mistakes memory）**：
- compose **必须用 SHA tag**，不要用 :latest
- 修改 compose 文件要 sudo：先 SFTP 写 /tmp，再 `sudo cp`
- Windows bash 发 POST 用 Python urllib，不用 curl

- [ ] **Step 1: 获取本次部署 SHA**

```bash
git rev-parse HEAD
```

记下完整 SHA（如 `abcd1234...`，共 40 字符）。

- [ ] **Step 2: 读取当前 compose 文件**

```bash
python ~/nas_ssh.py "sudo cat /zspace/applications/services/zdocker/config/compose_config/daodichishayou-backend.yaml"
```

记录当前 image tag。

- [ ] **Step 3: 生成新 compose（用 Python 脚本避免 shell 转义）**

Create `C:\Users\goodb\Downloads\temp_deploy_v1_9_0.py`（借鉴 memory 里 `temp_deploy_compose.py` 做法）:

```python
"""部署 v1.9.0 到 NAS：SFTP 新 compose → sudo cp → docker compose pull/up。"""

import subprocess
import sys
from pathlib import Path

SHA = "<填入 Step 1 的 SHA>"
NAS_SCRIPT = Path.home() / "nas_ssh.py"
COMPOSE_PATH = "/zspace/applications/services/zdocker/config/compose_config/daodichishayou-backend.yaml"
TMP_PATH = "/tmp/daodichishayou-backend-v1.9.0.yaml"
LOCAL_TMP = Path.home() / "Downloads" / "temp_compose_v1_9_0.yaml"

# 本地构造新 compose（从当前 compose 修改 image tag）
current_compose = subprocess.check_output([
    "python", str(NAS_SCRIPT),
    f"sudo cat {COMPOSE_PATH}",
], text=True, encoding="utf-8")

# 简单 regex 替换 image 行（假设格式 `image: jasonxi89/daodichishayou-backend:<sha>`)
import re
new_compose = re.sub(
    r"image:\s*jasonxi89/daodichishayou-backend:[\w.-]+",
    f"image: jasonxi89/daodichishayou-backend:{SHA}",
    current_compose,
)
LOCAL_TMP.write_text(new_compose, encoding="utf-8")
print(f"本地新 compose 写入 {LOCAL_TMP}")

# SFTP 传到 NAS /tmp
subprocess.run([
    "python", str(NAS_SCRIPT),
    f"put {LOCAL_TMP} {TMP_PATH}",
], check=True)

# sudo cp 覆盖正式路径
subprocess.run([
    "python", str(NAS_SCRIPT),
    f"sudo cp {TMP_PATH} {COMPOSE_PATH}",
], check=True)

# docker compose pull + up
subprocess.run([
    "python", str(NAS_SCRIPT),
    f"cd {Path(COMPOSE_PATH).parent} && sudo docker compose -f {COMPOSE_PATH} pull && "
    f"sudo docker compose -f {COMPOSE_PATH} up -d --force-recreate",
], check=True)

# 清理
subprocess.run([
    "python", str(NAS_SCRIPT),
    "sudo docker image prune -f",
], check=True)

print("✅ v1.9.0 部署完成")
```

- [ ] **Step 4: 执行部署脚本**

```bash
python "C:\Users\goodb\Downloads\temp_deploy_v1_9_0.py"
```

Expected: 打印"v1.9.0 部署完成"，无错误。

- [ ] **Step 5: 验证 health**

```bash
curl https://food.zuitian.ai/api/health
```

Expected: `{"status":"ok","version":"1.9.0"}`

- [ ] **Step 6: 触发首次 AI 历史合并**

Windows bash 下 POST 用 Python urllib（memory 教训 #8）：

```bash
python -c "import urllib.request as r,json; resp=r.urlopen(r.Request('https://food.zuitian.ai/api/admin/merge-aliases', method='POST')); print(resp.read().decode())"
```

Expected: `{"status":"ok","groups_processed":N,"aliases_created":M,"total_names_scanned":363}`

- [ ] **Step 7: 触发一次爬虫填充 trend_type / context**

```bash
python -c "import urllib.request as r; resp=r.urlopen(r.Request('https://food.zuitian.ai/api/trending/crawl', method='POST')); print(resp.read().decode())"
```

Expected: 返回各 crawler 的 CrawlResult 列表。

- [ ] **Step 8: 验证聚合 + digest**

```bash
curl "https://food.zuitian.ai/api/trending?limit=20"
curl "https://food.zuitian.ai/api/trending/digest"
```

Expected:
- trending Top 20 无"烧烤"重复，items[].canonical_name 非 null
- digest 返回非 null（最新一条）

- [ ] **Step 9: 清理临时文件**

```bash
rm -f "$USERPROFILE/Downloads/temp_deploy_v1_9_0.py" "$USERPROFILE/Downloads/temp_compose_v1_9_0.yaml"
```

- [ ] **Step 10: 更新 memory**

修改 `C:\Users\goodb\.claude\projects\C--Users-goodb\memory\daodichishayou_progress.md`：

1. 最新更新日期改为 2026-04-24
2. 版本记录表新增 1.9.0 行
3. 已完成功能区新增本次 4 项 AI 改进 + 2 项 endpoint bug 修复的 section
4. 当前运行 SHA 更新为 Task 10 Step 1 记下的 SHA
5. 后端 v1.8.1 的引用改为 v1.9.0

无需 commit（memory 在独立 repo `.claude`）。

---

## 成功验收清单

- [ ] 所有 244+ 后端测试通过
- [ ] Coverage ≥ 97%
- [ ] `curl https://food.zuitian.ai/api/health` → `version=1.9.0`
- [ ] `curl "https://food.zuitian.ai/api/trending?limit=20"` → 无同食物重复，items 有 `canonical_name` / `aliases` / `trend_type`
- [ ] `curl https://food.zuitian.ai/api/trending/digest` → 返回非 null（凌晨访问也有 fallback）
- [ ] `food_aliases` 表 ≥ 10 条 AI 生成记录
- [ ] `food_trends` 所有行 `canonical_name` 非空
- [ ] 前端小程序在真机上打开无异常（向后兼容验证）
- [ ] memory `daodichishayou_progress.md` 已更新

---

## 变更记录

- 2026-04-24 — 初稿，基于 spec `2026-04-24-food-data-quality-ai-design.md` 产出
