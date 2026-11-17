import os

# 测试不依赖本机环境：配置在导入 app 时读取，所以先清掉 Claude 相关环境变量
for _name in ("ANTHROPIC_API_KEY", "CLAUDE_MODEL", "CLAUDE_FAST_MODEL", "CLAUDE_EFFORT"):
    os.environ.pop(_name, None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

SQLALCHEMY_DATABASE_URL = "sqlite://"  # in-memory

# StaticPool ensures all connections share the same in-memory DB
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def forbid_real_claude_calls(monkeypatch):
    """任何测试都不许真连 Claude：没打桩就走到客户端工厂的，直接失败。"""

    def refuse_to_create_client():
        raise AssertionError(
            "测试里不允许真实调用 Claude 接口，请给 app.services.llm 打桩"
        )

    monkeypatch.setattr(
        "app.services.llm._create_client", refuse_to_create_client
    )


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db):
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def sample_trends(db):
    from app.models import FoodTrend
    trends = [
        FoodTrend(food_name="火锅", source="toutiao", heat_score=90, post_count=80000, category="正餐"),
        FoodTrend(food_name="奶茶", source="toutiao", heat_score=87, post_count=70000, category="饮品"),
        FoodTrend(food_name="披萨", source="baidu_suggest", heat_score=80, post_count=30000, category="西餐"),
        FoodTrend(food_name="寿司", source="manual", heat_score=83, post_count=35000, category="日料"),
        FoodTrend(food_name="火锅", source="baidu_suggest", heat_score=88, post_count=75000, category="正餐"),
    ]
    for t in trends:
        db.add(t)
    db.commit()
    for t in trends:
        db.refresh(t)
    return trends
