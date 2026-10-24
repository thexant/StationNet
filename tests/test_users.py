import pytest

from stationnet.users import _valid_birthday


@pytest.mark.parametrize("value", ["02/29", "01/01", "2024-02-29", "2001-12-31"])
def test_birthday_accepts_full_dates_and_month_day_only(value):
    assert _valid_birthday(value)


@pytest.mark.parametrize("value", ["02/30", "13/01", "2/29", "02-29", "2023-02-29", "2001/01/01"])
def test_birthday_rejects_invalid_or_unsupported_formats(value):
    assert not _valid_birthday(value)
