from datetime import date

from app.engine.calendar import (
    PayDayRule,
    easter_sunday,
    pay_day,
    scottish_bank_holidays,
)

PERSON_1 = PayDayRule("last_working_day")
PERSON_2 = PayDayRule("last_thursday", typical_offset_days=-1)


def test_scottish_bank_holidays_2026() -> None:
    assert sorted(scottish_bank_holidays(2026)) == [
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 4, 3),  # Good Friday (no Easter Monday in Scotland)
        date(2026, 5, 4),
        date(2026, 5, 25),
        date(2026, 6, 15),  # the one-off World Cup bank holiday (GOV.UK)
        date(2026, 8, 3),  # first Monday in August, not the last
        date(2026, 11, 30),
        date(2026, 12, 25),
        date(2026, 12, 28),  # Boxing Day is a Saturday
    ]


def test_substitute_days_and_one_offs() -> None:
    h2022 = scottish_bank_holidays(2022)
    assert date(2022, 1, 3) in h2022
    assert date(2022, 1, 4) in h2022  # 1st Sat, 2nd Sun
    assert date(2022, 12, 26) in h2022
    assert date(2022, 12, 27) in h2022
    assert date(2022, 5, 30) not in h2022  # Spring moved for the Jubilee
    assert date(2022, 6, 2) in h2022
    assert date(2022, 9, 19) in h2022
    assert date(2027, 11, 30) in scottish_bank_holidays(2027)
    assert date(2025, 12, 1) in scottish_bank_holidays(2025)  # St Andrew's on a Sunday


def test_easter() -> None:
    assert easter_sunday(2026) == date(2026, 4, 5)
    assert easter_sunday(2027) == date(2027, 3, 28)


def test_person_1_is_paid_on_the_last_working_day() -> None:
    assert pay_day(PERSON_1, 2026, 10).official == date(2026, 10, 30)  # 31st is a Saturday
    assert pay_day(PERSON_1, 2026, 12).official == date(2026, 12, 31)
    assert pay_day(PERSON_1, 2027, 4).official == date(2027, 4, 30)


def test_person_2_is_paid_last_thursday_but_it_lands_the_day_before() -> None:
    oct26 = pay_day(PERSON_2, 2026, 10)
    assert (oct26.official, oct26.expected) == (date(2026, 10, 29), date(2026, 10, 28))
    # St Andrew's Day 2023 was the last Thursday, so pay moves back a day.
    nov23 = pay_day(PERSON_2, 2023, 11)
    assert (nov23.official, nov23.expected) == (date(2023, 11, 29), date(2023, 11, 28))
