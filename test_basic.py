
from datetime import date

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_date_range_limit_is_92_days():
    start = date(2026, 1, 1)
    end = date(2026, 4, 3)

    assert (end - start).days == 92


def test_date_range_over_92_days_is_rejected():
    start = date(2026, 1, 1)
    end = date(2026, 4, 4)

    assert (end - start).days > 92


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
