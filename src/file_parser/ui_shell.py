from dataclasses import dataclass
import re
from typing import Optional


_ALLOWED_ROLES = frozenset({"investigator", "analyst", "admin"})


@dataclass(frozen=True)
class RouteSpec:
    name: str
    template: str
    case_scoped: bool
    resource_param: Optional[str] = None


@dataclass(frozen=True)
class RouteMatch:
    name: str
    template: str
    path: str
    params: dict[str, str]
    case_scoped: bool
    resource_param: Optional[str]


@dataclass(frozen=True)
class RouteDecision:
    status: int
    code: str
    route: Optional[RouteMatch] = None


@dataclass(frozen=True)
class NavItem:
    label: str
    path: str
    enabled: bool


@dataclass(frozen=True)
class WidgetState:
    widget_id: str
    status: str
    correlation_id: Optional[str] = None
    error_code: Optional[str] = None


@dataclass(frozen=True)
class ShellHealth:
    total: int
    loading: int
    empty: int
    errors: int
    partial_failure: bool


_ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec("cases_index", "/cases", case_scoped=False),
    RouteSpec("case_viewer", "/cases/:case_id", case_scoped=True, resource_param="case_id"),
    RouteSpec("people_index", "/cases/:case_id/people", case_scoped=True, resource_param="case_id"),
    RouteSpec(
        "person_profile",
        "/cases/:case_id/people/:person_id",
        case_scoped=True,
        resource_param="person_id",
    ),
    RouteSpec("timeline", "/cases/:case_id/timeline", case_scoped=True, resource_param="case_id"),
    RouteSpec("evidence_index", "/cases/:case_id/evidence", case_scoped=True, resource_param="case_id"),
    RouteSpec(
        "evidence_detail",
        "/cases/:case_id/evidence/:evidence_id",
        case_scoped=True,
        resource_param="evidence_id",
    ),
)


def _normalize_path(path: str) -> str:
    value = path.split("?", 1)[0].strip()
    if not value:
        return "/"
    if not value.startswith("/"):
        value = "/" + value
    if len(value) > 1 and value.endswith("/"):
        value = value.rstrip("/")
    return value


def _compile_template(template: str) -> re.Pattern[str]:
    parts = [p for p in template.split("/") if p]
    pattern_parts = []
    for part in parts:
        if part.startswith(":"):
            key = part[1:]
            pattern_parts.append(f"(?P<{key}>[^/]+)")
        else:
            pattern_parts.append(re.escape(part))
    pattern = "^/" + "/".join(pattern_parts) + "$"
    return re.compile(pattern)


_COMPILED_ROUTES: tuple[tuple[RouteSpec, re.Pattern[str]], ...] = tuple(
    (spec, _compile_template(spec.template)) for spec in _ROUTES
)


def resolve_route(path: str) -> RouteDecision:
    normalized = _normalize_path(path)
    for spec, pattern in _COMPILED_ROUTES:
        match = pattern.match(normalized)
        if not match:
            continue
        params = {key: value for key, value in match.groupdict().items() if isinstance(value, str)}
        return RouteDecision(
            status=200,
            code="ok",
            route=RouteMatch(
                name=spec.name,
                template=spec.template,
                path=normalized,
                params=params,
                case_scoped=spec.case_scoped,
                resource_param=spec.resource_param,
            ),
        )
    return RouteDecision(status=404, code="not_found", route=None)


def enforce_route_guard(
    path: str,
    role: str,
    case_exists: bool = True,
    resource_exists: bool = True,
) -> RouteDecision:
    route_decision = resolve_route(path)
    if route_decision.status != 200:
        return route_decision
    if role not in _ALLOWED_ROLES:
        return RouteDecision(status=403, code="forbidden", route=route_decision.route)
    route = route_decision.route
    if route is None:
        return RouteDecision(status=404, code="not_found", route=None)
    if route.case_scoped and not case_exists:
        return RouteDecision(status=404, code="case_not_found", route=route)
    if route.resource_param and route.resource_param in route.params and not resource_exists:
        return RouteDecision(status=404, code="resource_not_found", route=route)
    return RouteDecision(status=200, code="ok", route=route)


def build_primary_navigation(selected_case_id: Optional[str]) -> list[NavItem]:
    case_ready = isinstance(selected_case_id, str) and bool(selected_case_id.strip())
    case_id = selected_case_id.strip() if case_ready else None
    return [
        NavItem(label="Cases", path="/cases", enabled=True),
        NavItem(
            label="People",
            path=f"/cases/{case_id}/people" if case_id else "/cases",
            enabled=case_ready,
        ),
        NavItem(
            label="Timeline",
            path=f"/cases/{case_id}/timeline" if case_id else "/cases",
            enabled=case_ready,
        ),
        NavItem(
            label="Evidence",
            path=f"/cases/{case_id}/evidence" if case_id else "/cases",
            enabled=case_ready,
        ),
    ]


def build_widget_error(widget_id: str, correlation_id: str) -> WidgetState:
    return WidgetState(
        widget_id=widget_id,
        status="error",
        correlation_id=correlation_id,
        error_code="widget_data_unavailable",
    )


def summarize_shell_health(states: list[WidgetState]) -> ShellHealth:
    total = len(states)
    loading = sum(1 for state in states if state.status == "loading")
    empty = sum(1 for state in states if state.status == "empty")
    errors = sum(1 for state in states if state.status == "error")
    partial_failure = errors > 0 and errors < total
    return ShellHealth(
        total=total,
        loading=loading,
        empty=empty,
        errors=errors,
        partial_failure=partial_failure,
    )
