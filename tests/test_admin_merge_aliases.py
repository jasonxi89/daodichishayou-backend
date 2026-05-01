from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import Base, engine as app_engine
from app.main import app
from app.models import FoodAlias, FoodTrend


@pytest.fixture
def client():
    Base.metadata.create_all(app_engine)
    with TestClient(app) as c:
        # Clear after lifespan/seed_data runs so test starts with empty tables
        with Session(app_engine) as s:
            s.query(FoodAlias).delete()
            s.query(FoodTrend).delete()
            s.commit()
        yield c
    with Session(app_engine) as s:
        s.query(FoodAlias).delete()
        s.query(FoodTrend).delete()
        s.commit()


def test_merge_aliases_endpoint_returns_200(client):
    with patch("app.routers.admin.Anthropic") as mock_anth, \
         patch("app.routers.admin.DEEPSEEK_API_KEY", "fake-key"):
        mock_client = MagicMock()
        mock_anth.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(type='text', text='{"groups":[]}')]
        mock_client.messages.create.return_value = mock_resp

        resp = client.post("/api/admin/merge-aliases")
        assert resp.status_code == 200


def test_merge_aliases_writes_alias_and_updates_canonical(client):
    with Session(app_engine) as s:
        s.add(FoodTrend(food_name="川式火锅", source="toutiao", heat_score=90, post_count=0, canonical_name="川式火锅"))
        s.add(FoodTrend(food_name="重庆火锅", source="dailyhot", heat_score=88, post_count=0, canonical_name="重庆火锅"))
        s.add(FoodTrend(food_name="火锅", source="manual", heat_score=80, post_count=0, canonical_name="火锅"))
        s.commit()

    ai_response = '{"groups":[{"canonical":"火锅","aliases":["川式火锅","重庆火锅"]}]}'
    with patch("app.routers.admin.Anthropic") as mock_anth, \
         patch("app.routers.admin.DEEPSEEK_API_KEY", "fake-key"):
        mock_client = MagicMock()
        mock_anth.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(type='text', text=ai_response)]
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
    with patch("app.routers.admin.DEEPSEEK_API_KEY", ""):
        resp = client.post("/api/admin/merge-aliases")
        assert resp.status_code == 503
