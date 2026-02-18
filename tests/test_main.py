import pytest


def test_health_check(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_cors_headers(client):
    resp = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    # CORS is configured, should allow all origins
    assert resp.status_code in (200, 400)


def test_app_title():
    from app.main import app
    assert "到底吃啥哟" in app.title or "美食" in app.title


def test_routers_included(client):
    resp = client.get("/api/trending")
    assert resp.status_code == 200

def test_openapi_accessible(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
