import json
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings

BACKEND_DIR = Path(__file__).resolve().parent.parent

PROBE = (
    "import json, django.conf as c; "
    "from config.settings import prod as s; "
    "print(json.dumps({k: getattr(s, k) for k in "
    "['DEBUG','SECURE_SSL_REDIRECT','SECURE_HSTS_SECONDS','X_FRAME_OPTIONS',"
    "'SESSION_COOKIE_SECURE','ALLOWED_HOSTS']}))"
)


def run_prod(
    extra_env: dict[str, str], drop: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update({"DJANGO_SETTINGS_MODULE": "config.settings.prod"})
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_drf_defaults_are_secure():
    rf = settings.REST_FRAMEWORK
    assert rf["DEFAULT_PERMISSION_CLASSES"] == ["rest_framework.permissions.IsAuthenticated"]
    assert rf["UNAUTHENTICATED_USER"] is None
    assert rf["DEFAULT_RENDERER_CLASSES"] == ["rest_framework.renderers.JSONRenderer"]


def test_requests_are_not_wrapped_in_a_transaction():
    assert settings.DATABASES["default"]["ATOMIC_REQUESTS"] is False


def test_prod_settings_are_hardened():
    result = run_prod({"DJANGO_SECRET_KEY": "x" * 60, "ALLOWED_HOSTS": "example.com"})
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["DEBUG"] is False
    assert data["SECURE_SSL_REDIRECT"] is True
    assert data["SECURE_HSTS_SECONDS"] > 0
    assert data["X_FRAME_OPTIONS"] == "DENY"
    assert data["SESSION_COOKIE_SECURE"] is True
    assert data["ALLOWED_HOSTS"] == ["example.com"]


def test_prod_requires_allowed_hosts():
    result = run_prod({"DJANGO_SECRET_KEY": "x" * 60}, drop=("ALLOWED_HOSTS",))
    assert result.returncode != 0
    assert "ALLOWED_HOSTS" in result.stderr


def test_prod_requires_secret_key():
    result = run_prod({"ALLOWED_HOSTS": "example.com"}, drop=("DJANGO_SECRET_KEY",))
    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY" in result.stderr
