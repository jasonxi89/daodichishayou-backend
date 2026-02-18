import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app

SQLALCHEMY_DATABASE_URL = "sqlite://"  # in-memory

# StaticPool ensures all connections share the same in-memory DB
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


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
