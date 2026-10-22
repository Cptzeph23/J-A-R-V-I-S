from apps.accounts import lockout


def test_unlocked_by_default():
    assert lockout.locked_seconds_remaining("a@example.com") == 0


def test_locks_after_threshold(settings):
    settings.LOGIN_MAX_FAILURES = 3
    for _ in range(2):
        lockout.register_failure("a@example.com")
    assert lockout.locked_seconds_remaining("a@example.com") == 0
    lockout.register_failure("a@example.com")
    remaining = lockout.locked_seconds_remaining("a@example.com")
    assert 0 < remaining <= settings.LOGIN_LOCKOUT_SECONDS + 1


def test_failures_are_counted_per_email(settings):
    settings.LOGIN_MAX_FAILURES = 2
    lockout.register_failure("a@example.com")
    lockout.register_failure("a@example.com")
    assert lockout.locked_seconds_remaining("a@example.com") > 0
    assert lockout.locked_seconds_remaining("b@example.com") == 0


def test_reset_clears_counter_and_lock(settings):
    settings.LOGIN_MAX_FAILURES = 1
    lockout.register_failure("a@example.com")
    assert lockout.locked_seconds_remaining("a@example.com") > 0
    lockout.reset("a@example.com")
    assert lockout.locked_seconds_remaining("a@example.com") == 0
    assert lockout.register_failure("a@example.com") == 1


def test_register_failure_returns_running_count():
    assert [lockout.register_failure("a@example.com") for _ in range(3)] == [1, 2, 3]
