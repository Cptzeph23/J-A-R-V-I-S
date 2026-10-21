from celery import shared_task


@shared_task(name="common.ping")
def ping() -> str:
    """Diagnostic task used to verify worker wiring end to end."""
    return "pong"
