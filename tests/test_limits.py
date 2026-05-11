from bot.limits import InFlightGuard


def test_guard_starts_idle() -> None:
    g = InFlightGuard()
    assert g.is_busy(1) is False


def test_guard_lock_acquires_and_releases() -> None:
    g = InFlightGuard()
    with g.lock(1) as acquired:
        assert acquired is True
        assert g.is_busy(1) is True
    assert g.is_busy(1) is False


def test_guard_lock_rejects_when_busy() -> None:
    g = InFlightGuard()
    with g.lock(1) as outer:
        assert outer is True
        with g.lock(1) as inner:
            assert inner is False
        # Inner did NOT acquire, so it must not release the outer lock.
        assert g.is_busy(1) is True
    assert g.is_busy(1) is False


def test_guard_per_user() -> None:
    g = InFlightGuard()
    with g.lock(1) as a:
        assert a is True
        with g.lock(2) as b:
            assert b is True
            assert g.is_busy(1) and g.is_busy(2)


def test_guard_releases_on_exception() -> None:
    g = InFlightGuard()
    try:
        with g.lock(1) as acquired:
            assert acquired is True
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert g.is_busy(1) is False
