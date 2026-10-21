from unittest import mock

import pytest

LIVE = "/api/v1/health/live"
READY = "/api/v1/health/ready"


def test_live_needs_no_auth_and_no_db(api_client):
    response = api_client.get(LIVE)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.integration
@pytest.mark.django_db
def test_ready_ok_with_real_services(api_client):
    response = api_client.get(READY)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("db_ok", "redis_ok", "expected"),
    [
        (True, False, {"database": "ok", "redis": "unavailable"}),
        (False, True, {"database": "unavailable", "redis": "ok"}),
        (False, False, {"database": "unavailable", "redis": "unavailable"}),
    ],
)
def test_ready_reports_degraded(api_client, db_ok, redis_ok, expected):
    with (
        mock.patch("apps.common.health.views.check_database", return_value=db_ok),
        mock.patch("apps.common.health.views.check_redis", return_value=redis_ok),
    ):
        response = api_client.get(READY)
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "checks": expected}


@pytest.mark.django_db
def test_ready_never_leaks_exception_text(api_client):
    with mock.patch(
        "apps.common.health.views.redis.Redis.from_url",
        side_effect=ConnectionError("boom-secret-host:6379"),
    ):
        response = api_client.get(READY)
    assert response.status_code == 503
    assert "boom-secret" not in response.content.decode()
    assert response.json()["checks"]["redis"] == "unavailable"


@pytest.mark.django_db
def test_ready_survives_database_exception(api_client):
    with mock.patch(
        "apps.common.health.views.connection.cursor", side_effect=RuntimeError("db-secret")
    ):
        response = api_client.get(READY)
    assert response.status_code == 503
    assert "db-secret" not in response.content.decode()
    assert response.json()["checks"]["database"] == "unavailable"
