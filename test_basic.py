from datetime import date

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_endpoint_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_invalid_month_is_rejected():
    response = client.get(
        "/analytics/departments/summary",
        params={"month": "2026-13"},
    )

    assert response.status_code == 422


def test_missing_required_month_is_rejected():
    response = client.get("/analytics/departments/summary")

    assert response.status_code == 422


def test_invalid_trend_date_is_rejected():
    response = client.get(
        "/analytics/departments/Engineering/trend",
        params={
            "from": "not-a-date",
            "to": "2026-10-12",
        },
    )

    assert response.status_code == 422


def test_reversed_trend_date_range_is_rejected():
    response = client.get(
        "/analytics/departments/Engineering/trend",
        params={
            "from": "2026-10-12",
            "to": "2026-10-11",
        },
    )

    assert response.status_code == 422


def test_trend_range_over_92_days_is_rejected():
    response = client.get(
        "/analytics/departments/Engineering/trend",
        params={
            "from": "2026-01-01",
            "to": "2026-04-04",
        },
    )

    assert response.status_code == 422