import unittest

from timeline.date_parse import (
    find_first_absolute_anchor,
    parse_absolute_date_raw,
    parse_relative_spec,
    resolve_relative,
)


class TimelineDateParseTest(unittest.TestCase):
    def test_absolute_day_iso(self):
        parsed, status = parse_absolute_date_raw("2024-01-05")
        self.assertIsNone(status)
        self.assertEqual(parsed.date_start, "2024-01-05")
        self.assertEqual(parsed.date_end, "2024-01-05")
        self.assertEqual(parsed.precision, "day")

    def test_absolute_month_iso(self):
        parsed, status = parse_absolute_date_raw("2024-01")
        self.assertIsNone(status)
        self.assertEqual(parsed.date_start, "2024-01-01")
        self.assertEqual(parsed.date_end, "2024-01-31")
        self.assertEqual(parsed.precision, "month")

    def test_absolute_year(self):
        parsed, status = parse_absolute_date_raw("2024")
        self.assertIsNone(status)
        self.assertEqual(parsed.date_start, "2024-01-01")
        self.assertEqual(parsed.date_end, "2024-12-31")
        self.assertEqual(parsed.precision, "year")

    def test_absolute_mdy(self):
        parsed, status = parse_absolute_date_raw("01/05/2024")
        self.assertIsNone(status)
        self.assertEqual(parsed.date_start, "2024-01-05")
        self.assertEqual(parsed.precision, "day")

    def test_absolute_month_name_forms(self):
        for value in ("Jan 2, 2024", "2 Jan 2024"):
            parsed, status = parse_absolute_date_raw(value)
            self.assertIsNone(status, msg=value)
            self.assertEqual(parsed.date_start, "2024-01-02", msg=value)
            self.assertEqual(parsed.precision, "day", msg=value)

        parsed, status = parse_absolute_date_raw("January 2024")
        self.assertIsNone(status)
        self.assertEqual(parsed.date_start, "2024-01-01")
        self.assertEqual(parsed.date_end, "2024-01-31")
        self.assertEqual(parsed.precision, "month")

    def test_absolute_ranges(self):
        parsed, status = parse_absolute_date_raw("2024-01-05 to 2024-01-06")
        self.assertIsNone(status)
        self.assertEqual(parsed.date_start, "2024-01-05")
        self.assertEqual(parsed.date_end, "2024-01-06")
        self.assertEqual(parsed.precision, "range")

        parsed, status = parse_absolute_date_raw("Jan 2-5, 2024")
        self.assertIsNone(status)
        self.assertEqual(parsed.date_start, "2024-01-02")
        self.assertEqual(parsed.date_end, "2024-01-05")
        self.assertEqual(parsed.precision, "range")

    def test_absolute_ambiguity(self):
        parsed, status = parse_absolute_date_raw("2024-01-05 and 2024-01-06")
        self.assertIsNone(parsed)
        self.assertEqual(status, "unresolved_ambiguous")

    def test_relative_weekday_resolution(self):
        anchor = "2026-02-20"  # Friday
        spec = parse_relative_spec("last Tuesday")
        resolved, status = resolve_relative(spec, anchor)
        self.assertIsNone(status)
        self.assertEqual(resolved.date_start, "2026-02-17")

        spec = parse_relative_spec("next Tuesday")
        resolved, status = resolve_relative(spec, anchor)
        self.assertIsNone(status)
        self.assertEqual(resolved.date_start, "2026-02-24")

        spec = parse_relative_spec("this Tuesday")
        resolved, status = resolve_relative(spec, anchor)
        self.assertIsNone(status)
        self.assertEqual(resolved.date_start, "2026-02-24")

    def test_relative_keywords_and_deltas(self):
        spec = parse_relative_spec("today")
        resolved, status = resolve_relative(spec, "2026-02-20")
        self.assertIsNone(status)
        self.assertEqual(resolved.date_start, "2026-02-20")

        spec = parse_relative_spec("3 days ago")
        resolved, status = resolve_relative(spec, "2026-02-20")
        self.assertIsNone(status)
        self.assertEqual(resolved.date_start, "2026-02-17")

        spec = parse_relative_spec("in 2 weeks")
        resolved, status = resolve_relative(spec, "2026-02-20")
        self.assertIsNone(status)
        self.assertEqual(resolved.date_start, "2026-03-06")

    def test_relative_month_year_clamps(self):
        spec = parse_relative_spec("in 1 month")
        resolved, status = resolve_relative(spec, "2026-01-31")
        self.assertIsNone(status)
        self.assertEqual(resolved.date_start, "2026-02-28")

        spec = parse_relative_spec("1 year ago")
        resolved, status = resolve_relative(spec, "2024-02-29")
        self.assertIsNone(status)
        self.assertEqual(resolved.date_start, "2023-02-28")

    def test_find_first_absolute_anchor(self):
        text = "No date here."
        self.assertIsNone(find_first_absolute_anchor(text))

        text = "As of 2026-02-20, the report states..."
        self.assertEqual(find_first_absolute_anchor(text), "2026-02-20")


if __name__ == "__main__":
    unittest.main()

