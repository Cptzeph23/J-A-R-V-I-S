from django.conf import settings

from apps.common.tasks import ping
from config.celery import app


def test_ping_task_is_registered_by_autodiscovery():
    app.loader.import_default_modules()
    assert "common.ping" in app.tasks


def test_ping_runs_eagerly_under_test_settings():
    assert ping.apply().get() == "pong"


def test_reliability_settings_are_applied():
    conf = app.conf
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert 0 < conf.task_soft_time_limit < conf.task_time_limit
    assert conf.broker_url == settings.CELERY_BROKER_URL
    assert isinstance(conf.beat_schedule, dict)


def test_visibility_timeout_exceeds_task_time_limit():
    timeout = app.conf.broker_transport_options["visibility_timeout"]
    assert timeout > app.conf.task_time_limit
