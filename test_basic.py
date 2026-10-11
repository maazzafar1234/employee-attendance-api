from datetime import date


def test_date_range_limit_is_92_days():
    start = date(2026, 1, 1)
    end = date(2026, 4, 3)

    assert (end - start).days == 92


def test_date_range_over_92_days_is_rejected():
    start = date(2026, 1, 1)
    end = date(2026, 4, 4)

    assert (end - start).days > 92