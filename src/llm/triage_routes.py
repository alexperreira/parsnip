import json
from pathlib import Path
from typing import Dict, Optional, Set


DEFAULT_LLM_ROUTES: tuple[str, ...] = ("llm_small", "llm_large")


def parse_allowed_routes(routes_csv: Optional[str]) -> Set[str]:
    if routes_csv is None:
        return set(DEFAULT_LLM_ROUTES)
    routes = {part.strip() for part in routes_csv.split(",") if part.strip()}
    if not routes:
        raise SystemExit("Invalid --triage-routes value: expected at least one route name.")
    return routes


def load_triage_routes(path: Optional[str]) -> Optional[Dict[str, str]]:
    if not path:
        return None
    triage_path = Path(path)
    if not triage_path.exists():
        raise SystemExit(f"Triage not found: {triage_path}")
    route_by_chunk_id: Dict[str, str] = {}
    with triage_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            chunk_id = record.get("chunk_id")
            route = record.get("route")
            if isinstance(chunk_id, str) and chunk_id and isinstance(route, str) and route:
                route_by_chunk_id[chunk_id] = route
    return route_by_chunk_id


def chunk_is_selected(
    chunk_id: object,
    *,
    route_by_chunk_id: Optional[Dict[str, str]],
    allowed_routes: Set[str],
) -> bool:
    if route_by_chunk_id is None:
        return True
    if not isinstance(chunk_id, str) or not chunk_id:
        return False
    route = route_by_chunk_id.get(chunk_id)
    if route is None:
        return False
    return route in allowed_routes
