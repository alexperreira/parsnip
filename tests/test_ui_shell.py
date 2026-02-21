import unittest

from file_parser.ui_shell import (
    SharedFilterState,
    WidgetState,
    build_primary_navigation,
    build_primary_navigation_with_filters,
    build_widget_error,
    enforce_route_guard,
    parse_shared_filters,
    resolve_route,
    serialize_shared_filters,
    summarize_shell_health,
)


class TestUiShell(unittest.TestCase):
    def test_resolve_route_known_path(self):
        decision = resolve_route("/cases/case-123/people/42")
        self.assertEqual(decision.status, 200)
        self.assertIsNotNone(decision.route)
        self.assertEqual(decision.route.name, "person_profile")
        self.assertEqual(decision.route.params["case_id"], "case-123")
        self.assertEqual(decision.route.params["person_id"], "42")

    def test_resolve_route_unknown_path(self):
        decision = resolve_route("/unknown/path")
        self.assertEqual(decision.status, 404)
        self.assertEqual(decision.code, "not_found")

    def test_route_guard_forbidden_role(self):
        decision = enforce_route_guard("/cases/case-123", role="guest")
        self.assertEqual(decision.status, 403)
        self.assertEqual(decision.code, "forbidden")

    def test_route_guard_missing_case(self):
        decision = enforce_route_guard("/cases/case-123/timeline", role="investigator", case_exists=False)
        self.assertEqual(decision.status, 404)
        self.assertEqual(decision.code, "case_not_found")

    def test_primary_navigation_disabled_without_case(self):
        nav = build_primary_navigation(None)
        self.assertEqual([item.label for item in nav], ["Cases", "People", "Timeline", "Evidence"])
        self.assertTrue(nav[0].enabled)
        self.assertFalse(nav[1].enabled)
        self.assertFalse(nav[2].enabled)
        self.assertFalse(nav[3].enabled)

    def test_primary_navigation_enabled_with_case(self):
        nav = build_primary_navigation("case-123")
        self.assertTrue(all(item.enabled for item in nav))
        self.assertEqual(nav[1].path, "/cases/case-123/people")
        self.assertEqual(nav[2].path, "/cases/case-123/timeline")
        self.assertEqual(nav[3].path, "/cases/case-123/evidence")

    def test_shared_filters_round_trip(self):
        parsed = parse_shared_filters(
            "/cases/case-123/timeline?case_id_norm=case-123&person_id=7&date_start=2026-01-01&date_end=2026-02-01&confidence_min=0.7"
        )
        self.assertEqual(parsed.case_id_norm, "case-123")
        self.assertEqual(parsed.person_id, 7)
        self.assertEqual(parsed.date_start, "2026-01-01")
        self.assertEqual(parsed.date_end, "2026-02-01")
        self.assertEqual(parsed.confidence_min, 0.7)

        query = serialize_shared_filters(parsed)
        self.assertEqual(
            query,
            "case_id_norm=case-123&person_id=7&date_start=2026-01-01&date_end=2026-02-01&confidence_min=0.7",
        )

    def test_primary_navigation_persists_shared_filters(self):
        nav = build_primary_navigation_with_filters(
            "case-123",
            SharedFilterState(person_id=7, date_start="2026-01-01", date_end="2026-02-01", confidence_min=0.6),
        )
        self.assertEqual(
            nav[2].path,
            "/cases/case-123/timeline?case_id_norm=case-123&person_id=7&date_start=2026-01-01&date_end=2026-02-01&confidence_min=0.6",
        )

    def test_widget_error_and_shell_health(self):
        states = [
            WidgetState(widget_id="summary", status="ready"),
            build_widget_error(widget_id="timeline", correlation_id="cid-001"),
            WidgetState(widget_id="evidence", status="loading"),
        ]
        health = summarize_shell_health(states)
        self.assertEqual(health.total, 3)
        self.assertEqual(health.errors, 1)
        self.assertEqual(health.loading, 1)
        self.assertTrue(health.partial_failure)


if __name__ == "__main__":
    unittest.main()
