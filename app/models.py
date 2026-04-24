from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FoodTrend(Base):
    __tablename__ = "food_trends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    food_name: Mapped[str] = mapped_column(String(100), index=True)
    source: Mapped[str] = mapped_column(String(50))
    heat_score: Mapped[int] = mapped_column(Integer, default=0)
    post_count: Mapped[int] = mapped_column(Integer, default=0)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    canonical_name: Mapped[str | None] = mapped_column(
        String(100), index=True, nullable=True
    )
    trend_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    trend_context: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    __table_args__ = (
        Index("ix_food_source", "food_name", "source", unique=True),
    )


class CrawlLog(Base):
    __tablename__ = "crawl_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20))
    items_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class AIDiscoveredFood(Base):
    """AI 发现的新食物记录 — 用于分析和未来词典扩充。"""
    __tablename__ = "ai_discovered_foods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    food_name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    discovery_count: Mapped[int] = mapped_column(Integer, default=1)
    promoted_to_trends: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    source_url: Mapped[str] = mapped_column(String(500), unique=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    made_count: Mapped[int] = mapped_column(Integer, default=0)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    author: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ingredients_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingredients_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    list_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class FoodDigest(Base):
    """每日美食趋势快报。"""
    __tablename__ = "food_digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    digest_date: Mapped[datetime] = mapped_column(DateTime, unique=True, index=True)
    summary: Mapped[str] = mapped_column(Text)
    top_foods: Mapped[str] = mapped_column(Text)  # JSON array
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class FoodTrendSnapshot(Base):
    """每日热度快照 — 记录历史趋势变化。"""
    __tablename__ = "food_trend_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    food_name: Mapped[str] = mapped_column(String(100), index=True)
    heat_score: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(50))
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    __table_args__ = (
        Index("ix_snapshot_date_food", "snapshot_date", "food_name", "source", unique=True),
    )


class AITitleCache(Base):
    """AI 标题提取缓存 — 避免重复调用 LLM。"""
    __tablename__ = "ai_title_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500))
    extracted_foods: Mapped[str] = mapped_column(Text)  # JSON array
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class FoodsCategoryCache(Base):
    __tablename__ = "foods_category_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    foods: Mapped[str] = mapped_column(String(10000))  # JSON array string
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FoodAlias(Base):
    """食物别名 → 规范名映射，支持同义归并。"""
    __tablename__ = "food_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alias_name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    canonical_name: Mapped[str] = mapped_column(String(100), index=True)
    created_by: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
