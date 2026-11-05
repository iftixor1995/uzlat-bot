from bot.admin import parse_ids


def test_parse_ids_from_sheet_column_and_csv():
    pasted = "Telegram ID\n144217530\n987654321\n"
    assert parse_ids(pasted) == {144217530, 987654321}
    csv = "2026-11-05 14:00:00,144217530,@user123456,Ali,,uz,,inline,Yangi\n"
    assert parse_ids(csv) == {144217530}
    assert parse_ids("12.3456789 1234 2026-11-05") == set()
