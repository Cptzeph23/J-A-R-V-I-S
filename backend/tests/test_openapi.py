"""The committed OpenAPI file must match what the code generates (contract drift guard)."""

from pathlib import Path

from django.core.management import call_command

COMMITTED = Path(__file__).resolve().parent.parent.parent / "docs" / "api" / "openapi.yaml"


def test_committed_schema_is_current_and_valid(tmp_path):
    generated = tmp_path / "openapi.yaml"
    call_command("spectacular", file=str(generated), validate=True, fail_on_warn=True)
    assert COMMITTED.read_text() == generated.read_text(), (
        "docs/api/openapi.yaml is out of date. Regenerate with: "
        "python manage.py spectacular --file ../docs/api/openapi.yaml"
    )


def test_schema_is_openapi_3_1():
    assert COMMITTED.read_text().startswith("openapi: 3.1.0")
