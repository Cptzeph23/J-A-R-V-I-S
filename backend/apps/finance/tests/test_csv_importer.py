from dataclasses import replace
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from apps.finance.importers.csv_importer import (
    AMOUNT_DEBIT_CREDIT,
    MAX_FILE_BYTES,
    MAX_ROWS,
    ColumnMapping,
    CsvFileError,
    CsvOptions,
    parse_amount_minor,
    parse_csv,
)

NAIROBI = ZoneInfo("Africa/Nairobi")


def opts(**changes):
    base = CsvOptions(account_currency="KES", timezone=NAIROBI, today=date(2026, 10, 20))
    return replace(base, **changes)


def csv_bytes(*lines):
    return ("\n".join(lines) + "\n").encode()


HEADER = "date,description,amount"


def only_reason(result):
    assert not result.rows, result.rows
    assert len(result.rejections) == 1, result.rejections
    return result.rejections[0].reason


def test_valid_file_parses_to_exact_minor_units():
    result = parse_csv(
        csv_bytes(HEADER, "2026-10-01,Naivas,-1250.50", "2026-10-02,Salary,85000"), opts()
    )
    assert result.rejections == []
    assert [r.amount_minor for r in result.rows] == [-125050, 8500000]
    assert [r.kind for r in result.rows] == ["expense", "income"]
    assert result.total_rows == 2


def test_date_only_rows_use_local_midnight_and_have_no_time():
    row = parse_csv(csv_bytes(HEADER, "2026-10-01,x,-1"), opts()).rows[0]
    assert row.has_time is False
    assert row.posted_on == date(2026, 10, 1)
    # 00:00 in Nairobi (UTC+3) is 21:00 UTC the previous day
    assert row.posted_at == datetime(2026, 9, 30, 21, 0, tzinfo=UTC)


def test_timed_rows_keep_the_local_calendar_date():
    result = parse_csv(
        csv_bytes(HEADER, "2026-10-01 23:30,late night,-5"), opts(date_format="%Y-%m-%d %H:%M")
    )
    row = result.rows[0]
    assert row.has_time is True
    assert row.posted_on == date(2026, 10, 1)  # 20:30 UTC but 23:30 local
    assert row.posted_at == datetime(2026, 10, 1, 20, 30, tzinfo=UTC)


def test_day_first_format_is_explicit_not_guessed():
    row = parse_csv(csv_bytes(HEADER, "03/04/2026,x,-1"), opts(date_format="%d/%m/%Y")).rows[0]
    assert row.posted_on == date(2026, 4, 3)
    assert only_reason(parse_csv(csv_bytes(HEADER, "03/04/2026,x,-1"), opts())) == "invalid_date"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,250.50", 125050),
        ("-1,250.50", -125050),
        ("(100.00)", -10000),
        ("+75", 7500),
        ("0.5", 50),
        ("12", 1200),
        (" 99.99 ", 9999),
    ],
)
def test_parse_amount_accepts_unambiguous_formats(text, expected):
    assert parse_amount_minor(text, "KES") == expected


@pytest.mark.parametrize(
    "text",
    [
        "abc",
        "1.234",
        "KES 100",
        "1,25",
        "1.2.3",
        "--5",
        "1e5",
        "NaN",
        "١٢٣",
        "99999999999999999999",
    ],
)
def test_invalid_amounts_are_rejected_not_guessed(text):
    result = parse_csv(csv_bytes(HEADER, f'2026-10-01,x,"{text}"'), opts())
    assert only_reason(result) == "invalid_amount"


def test_empty_and_zero_amounts():
    assert only_reason(parse_csv(csv_bytes(HEADER, "2026-10-01,x,"), opts())) == "missing_amount"
    assert only_reason(parse_csv(csv_bytes(HEADER, "2026-10-01,x,0.00"), opts())) == "zero_amount"


def test_debit_credit_layout():
    header = "date,description,debit,credit"
    result = parse_csv(
        csv_bytes(
            header,
            "2026-10-01,shop,100.00,0.00",
            "2026-10-02,pay,,5000.00",
            "2026-10-03,both,1.00,2.00",
            "2026-10-04,none,,",
            "2026-10-05,zeros,0.00,0.00",
            "2026-10-06,neg,-5.00,",
        ),
        opts(amount_mode=AMOUNT_DEBIT_CREDIT),
    )
    assert [(r.row_number, r.amount_minor) for r in result.rows] == [(2, -10000), (3, 500000)]
    assert {r.row_number: r.reason for r in result.rejections} == {
        4: "ambiguous_amount",
        5: "missing_amount",
        6: "zero_amount",
        7: "invalid_amount",
    }


def test_type_column_must_agree_with_the_sign():
    header = "date,description,amount,type"
    result = parse_csv(
        csv_bytes(
            header,
            "2026-10-01,fee,-30,fee",
            "2026-10-02,move,-500,transfer",
            "2026-10-03,bad,500,expense",
            "2026-10-04,odd,5,gift",
        ),
        opts(),
    )
    assert [r.kind for r in result.rows] == ["fee", "transfer_out"]
    assert {r.row_number: r.reason for r in result.rejections} == {
        4: "sign_mismatch",
        5: "invalid_type",
    }


def test_date_range_rules():
    result = parse_csv(
        csv_bytes(
            HEADER,
            "2026-10-21,tomorrow is ok,-1",
            "2026-10-22,too far ahead,-1",
            "1999-12-31,too old,-1",
        ),
        opts(),
    )
    assert [r.row_number for r in result.rows] == [2]
    assert {r.row_number: r.reason for r in result.rejections} == {
        3: "future_date",
        4: "date_out_of_range",
    }


def test_currency_column_must_match_the_account():
    header = "date,description,amount,currency"
    result = parse_csv(
        csv_bytes(header, "2026-10-01,a,-1,kes", "2026-10-02,b,-1,USD", "2026-10-03,c,-1,XXX"),
        opts(),
    )
    assert [r.currency for r in result.rows] == ["KES"]
    assert {r.row_number: r.reason for r in result.rejections} == {
        3: "currency_mismatch",
        4: "unknown_currency",
    }


def test_text_fields_are_cleaned_and_bounded():
    header = "date,description,amount,reference,merchant"
    long_desc = "x" * 501
    result = parse_csv(
        csv_bytes(
            header,
            '2026-10-01,"two\nlines   here",-1,REF1,Shop',
            f"2026-10-02,{long_desc},-1,,",
            f"2026-10-03,ok,-1,{'r' * 65},",
            f"2026-10-04,ok,-1,,{'m' * 201}",
            "2026-10-05,bell\x07char,-1,,",
        ),
        opts(),
    )
    assert result.rows[0].description == "two lines here"
    assert result.rows[0].provider_ref == "REF1"
    assert {r.row_number: r.reason for r in result.rejections} == {
        4: "description_too_long",
        5: "reference_too_long",
        6: "merchant_too_long",
        7: "invalid_characters",
    }


def test_row_shape_errors_do_not_stop_the_file():
    result = parse_csv(
        csv_bytes(HEADER, "2026-10-01,short", "2026-10-02,ok,-1,extra", "2026-10-03,fine,-2"),
        opts(),
    )
    assert [r.row_number for r in result.rows] == [4]
    assert {r.reason for r in result.rejections} == {"malformed_row"}


def test_blank_lines_are_ignored_and_not_counted():
    result = parse_csv(csv_bytes(HEADER, "", "2026-10-01,a,-1", ",,", ""), opts())
    assert result.total_rows == 1


def test_header_matching_is_case_insensitive_and_trims_and_bom_is_ok():
    content = "﻿ Date , DESCRIPTION ,Amount\n2026-10-01,a,-1\n".encode()
    assert len(parse_csv(content, opts()).rows) == 1


def test_custom_column_mapping():
    mapping = ColumnMapping(
        date_column="Posted", description_column="Narrative", amount_column="Amt"
    )
    content = csv_bytes("Posted,Narrative,Amt", "2026-10-01,thing,-9")
    assert parse_csv(content, opts(mapping=mapping)).rows[0].amount_minor == -900


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"", "empty_file"),
        (b"   \n\n", "empty_file"),
        (b"date,description,amount\n2026-10-01,a\x00,-1\n", "invalid_file"),
        (b"date,description,amount\n2026-10-01,\xff\xfe,-1\n", "invalid_encoding"),
        (b"date,description\n2026-10-01,a\n", "missing_columns"),
        (b"date,amount,amount\n2026-10-01,a,-1\n", "duplicate_columns"),
        (b'date,description,amount\n2026-10-01,"unterminated,-1\n', "malformed_csv"),
        (b"x" * (MAX_FILE_BYTES + 1), "file_too_large"),
    ],
)
def test_file_level_errors(content, code):
    with pytest.raises(CsvFileError) as info:
        parse_csv(content, opts())
    assert info.value.code == code


def test_too_many_rows():
    body = [HEADER] + [f"2026-10-01,r{i},-1" for i in range(MAX_ROWS + 1)]
    with pytest.raises(CsvFileError) as info:
        parse_csv(csv_bytes(*body), opts())
    assert info.value.code == "too_many_rows"


def test_invalid_amount_mode_is_a_file_error():
    with pytest.raises(CsvFileError) as info:
        parse_csv(csv_bytes(HEADER, "2026-10-01,a,-1"), opts(amount_mode="bogus"))
    assert info.value.code == "invalid_options"


def test_row_numbers_are_file_line_numbers():
    result = parse_csv(csv_bytes(HEADER, "2026-10-01,a,-1", "bad,b,-1", "2026-10-03,c,-1"), opts())
    assert [r.row_number for r in result.rows] == [2, 4]
    assert result.rejections[0].row_number == 3
