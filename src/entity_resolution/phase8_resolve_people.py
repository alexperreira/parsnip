import argparse
import json
import re
import time

from loaders.store import connect_db, ensure_schema


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

_NICKNAME_TO_CANONICAL = {
    "bob": "robert",
    "bobby": "robert",
    "rob": "robert",
    "robbie": "robert",
    "bill": "william",
    "billy": "william",
    "will": "william",
    "willy": "william",
    "liz": "elizabeth",
    "beth": "elizabeth",
    "lizzy": "elizabeth",
    "jim": "james",
    "jimmy": "james",
    "mike": "michael",
    "kate": "katherine",
    "katie": "katherine",
    "tom": "thomas",
    "dan": "daniel",
    "danny": "daniel",
    "joe": "joseph",
}


def _parse_args():
    parser = argparse.ArgumentParser(description="Resolve people in SQLite (Phase 8).")
    parser.add_argument(
        "--db",
        default="output/store.sqlite",
        help="SQLite DB path (default: output/store.sqlite).",
    )
    parser.add_argument(
        "--person-types",
        default="person",
        help="Comma-separated entity.type values treated as people (default: person).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear resolver tables before rebuilding (recommended).",
    )
    parser.add_argument(
        "--max-group-size",
        type=int,
        default=200,
        help="Skip candidate groups larger than this (default: 200).",
    )
    return parser.parse_args()


def _normalize_name(value):
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if not value:
        return None
    value = _NON_ALNUM_RE.sub(" ", value)
    value = " ".join(value.split())
    return value if value else None


def _name_parts(name_norm):
    tokens = name_norm.split()
    if not tokens:
        return None, None
    first = tokens[0]
    last = tokens[-1] if len(tokens) > 1 else None
    return first, last


def _name_keys(name_norm):
    first, last = _name_parts(name_norm)
    keys = []
    if last and first:
        keys.append(("last_first_initial", f"{last}:{first[0]}"))
        canonical = _NICKNAME_TO_CANONICAL.get(first, first)
        keys.append(("canonical_first_last", f"{canonical}:{last}"))
    keys.append(("exact", name_norm))
    return keys


class _UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}
        self.rank = {i: 0 for i in items}

    def find(self, x):
        parent = self.parent
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(self, a, b):
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            return
        rank = self.rank
        if rank[ra] < rank[rb]:
            self.parent[ra] = rb
        elif rank[ra] > rank[rb]:
            self.parent[rb] = ra
        else:
            self.parent[rb] = ra
            rank[ra] += 1


def _score_pair(left, right, signals):
    left_id, left_name, left_norm = left
    right_id, right_name, right_norm = right
    _ = left_name, right_name

    reasons = []
    score = 0.0

    left_s = signals.get(left_id, {})
    right_s = signals.get(right_id, {})

    left_dobs = left_s.get("dob") or set()
    right_dobs = right_s.get("dob") or set()
    if left_dobs and right_dobs and left_dobs.isdisjoint(right_dobs):
        return -100.0, ["dob_conflict"], "no_merge"

    dob_match = bool(left_dobs & right_dobs) if left_dobs and right_dobs else False
    if dob_match:
        score += 10.0
        reasons.append("dob_match")

    addr_match = False
    left_addr = left_s.get("address") or set()
    right_addr = right_s.get("address") or set()
    if left_addr and right_addr and (left_addr & right_addr):
        addr_match = True
        score += 6.0
        reasons.append("address_match")

    case_match = False
    left_case = left_s.get("case_id") or set()
    right_case = right_s.get("case_id") or set()
    if left_case and right_case and (left_case & right_case):
        case_match = True
        score += 2.0
        reasons.append("case_id_match")

    if left_norm == right_norm:
        score += 4.0
        reasons.append("name_exact")
    else:
        left_first, left_last = _name_parts(left_norm)
        right_first, right_last = _name_parts(right_norm)
        if left_last and right_last and left_last == right_last:
            score += 1.0
            reasons.append("last_name_match")
            if left_first and right_first and left_first[0] == right_first[0]:
                score += 1.0
                reasons.append("first_initial_match")
            if left_first and right_first:
                if _NICKNAME_TO_CANONICAL.get(left_first, left_first) == _NICKNAME_TO_CANONICAL.get(
                    right_first, right_first
                ):
                    score += 2.0
                    reasons.append("nickname_match")

    if dob_match:
        return score, reasons, "auto_merge"
    if addr_match and ("name_exact" in reasons or "nickname_match" in reasons) and score >= 9.0:
        return score, reasons, "auto_merge"
    if case_match and not dob_match and not addr_match:
        # Case IDs are useful linkage signals but not safe to auto-merge on.
        reasons.append("case_id_only")
        return score, reasons, "needs_review"
    if score >= 5.0:
        # Name-only or weak-signal matches should be reviewable, not auto merged.
        if not dob_match and not addr_match:
            reasons.append("weak_match")
        return score, reasons, "needs_review"
    return score, reasons, "no_merge"


def build_resolve_people(db_path, person_types, reset=False, max_group_size=200):
    started = time.monotonic()
    conn = connect_db(db_path)
    ensure_schema(conn, overwrite=False)

    config = {
        "person_types": person_types,
        "reset": bool(reset),
        "max_group_size": int(max_group_size),
    }

    if reset:
        conn.execute("DELETE FROM person_resolution_edges")
        conn.execute("DELETE FROM person_cluster_members")
        conn.execute("DELETE FROM person_clusters")
        conn.execute("DELETE FROM person_observations")
        conn.commit()

    types_norm = {t.strip().lower() for t in person_types.split(",") if t.strip()}
    types_norm = types_norm or {"person"}

    obs_rows_attempted = 0
    obs_rows_inserted = 0

    for entity, ent_type, file_id, chunk_id, page_start, page_end in conn.execute(
        "SELECT entity, type, file_id, chunk_id, page_start, page_end FROM entities WHERE type IS NOT NULL"
    ):
        if not isinstance(ent_type, str) or ent_type.strip().lower() not in types_norm:
            continue
        name_norm = _normalize_name(entity)
        if not name_norm:
            continue
        obs_rows_attempted += 1
        result = conn.execute(
            "INSERT OR IGNORE INTO person_observations("
            "name, name_norm, file_id, chunk_id, page_start, page_end"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (entity, name_norm, file_id, chunk_id, page_start, page_end),
        )
        if result.rowcount == 1:
            obs_rows_inserted += 1

    conn.commit()

    observations = list(
        conn.execute("SELECT obs_id, name, name_norm, file_id, chunk_id FROM person_observations")
    )
    obs_lookup = {obs_id: (obs_id, name, name_norm) for obs_id, name, name_norm, _, _ in observations}

    obs_by_chunk_and_name = {}
    for obs_id, _, name_norm, file_id, chunk_id in observations:
        obs_by_chunk_and_name.setdefault((file_id, chunk_id, name_norm), []).append(obs_id)

    signals_by_obs = {}
    signals_attached = 0
    for person_text, attribute, value_norm, file_id, chunk_id in conn.execute(
        "SELECT person_text, attribute, value_norm, file_id, chunk_id "
        "FROM identity_signals "
        "WHERE value_norm IS NOT NULL AND attribute IS NOT NULL"
    ):
        person_norm = _normalize_name(person_text)
        if not person_norm:
            continue
        if not isinstance(attribute, str):
            continue
        attr = attribute.strip().lower()
        if attr not in ("dob", "address", "case_id"):
            continue
        obs_ids = obs_by_chunk_and_name.get((file_id, chunk_id, person_norm))
        if not obs_ids:
            continue
        for obs_id in obs_ids:
            per = signals_by_obs.setdefault(obs_id, {})
            per.setdefault(attr, set()).add(value_norm)
            signals_attached += 1

    # Candidate generation: group by attribute values and name keys.
    groups = {}
    groups_skipped = 0

    for obs_id, _, name_norm, _, _ in observations:
        per = signals_by_obs.get(obs_id) or {}
        for attr in ("dob", "address", "case_id"):
            for v in per.get(attr) or set():
                groups.setdefault((f"{attr}_exact", v), set()).add(obs_id)
        for key_type, key_value in _name_keys(name_norm):
            groups.setdefault((f"name_{key_type}", key_value), set()).add(obs_id)

    uf = _UnionFind([obs_id for obs_id, *_ in observations])

    edges_attempted = 0
    edges_inserted = 0
    decisions = {"auto_merge": 0, "needs_review": 0, "no_merge": 0}

    def _emit_pairs(obs_ids):
        obs_ids = sorted(obs_ids)
        for i in range(len(obs_ids)):
            a = obs_ids[i]
            for j in range(i + 1, len(obs_ids)):
                b = obs_ids[j]
                yield a, b

    for (_, _), obs_ids in groups.items():
        if len(obs_ids) > max_group_size:
            groups_skipped += 1
            continue
        for left_id, right_id in _emit_pairs(obs_ids):
            edges_attempted += 1
            left = obs_lookup[left_id]
            right = obs_lookup[right_id]
            score, reasons, decision = _score_pair(left, right, signals_by_obs)
            decisions[decision] += 1
            if decision == "auto_merge":
                uf.union(left_id, right_id)
            reasons_json = json.dumps(reasons, ensure_ascii=True, separators=(",", ":"))
            result = conn.execute(
                "INSERT OR IGNORE INTO person_resolution_edges("
                "left_obs_id, right_obs_id, decision, score, reasons_json"
                ") VALUES (?, ?, ?, ?, ?)",
                (left_id, right_id, decision, float(score), reasons_json),
            )
            if result.rowcount == 1:
                edges_inserted += 1

    conn.commit()

    components = {}
    for obs_id, *_ in observations:
        root = uf.find(obs_id)
        components.setdefault(root, []).append(obs_id)

    cluster_rows_inserted = 0
    member_rows_inserted = 0

    cluster_specs = []
    for root, obs_ids in components.items():
        obs_ids = sorted(obs_ids)
        name_norms = [obs_lookup[oid][2] for oid in obs_ids]
        counts = {}
        for n in name_norms:
            counts[n] = counts.get(n, 0) + 1
        best_norm = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]

        candidates = [obs_lookup[oid][1] for oid in obs_ids if obs_lookup[oid][2] == best_norm]
        display_name = sorted(candidates, key=lambda s: (len(s), s.lower(), s))[0]

        dobs = set()
        for oid in obs_ids:
            dobs |= (signals_by_obs.get(oid, {}).get("dob") or set())
        dob = sorted(dobs)[0] if len(dobs) == 1 else None

        cluster_specs.append((best_norm, "" if dob is None else dob, min(obs_ids), display_name, dob, obs_ids))

    cluster_specs.sort()

    for _, __, ___, display_name, dob, obs_ids in cluster_specs:
        display_name_norm = _normalize_name(display_name) or display_name.lower()
        cur = conn.execute(
            "INSERT INTO person_clusters(display_name, display_name_norm, dob) VALUES (?, ?, ?)",
            (display_name, display_name_norm, dob),
        )
        person_id = cur.lastrowid
        cluster_rows_inserted += 1
        for oid in obs_ids:
            m = conn.execute(
                "INSERT OR IGNORE INTO person_cluster_members(person_id, obs_id) VALUES (?, ?)",
                (person_id, oid),
            )
            if m.rowcount == 1:
                member_rows_inserted += 1

    conn.commit()
    conn.close()

    elapsed = round(time.monotonic() - started, 3)
    summary = {
        "observations_total": len(observations),
        "observations_attempted": obs_rows_attempted,
        "observations_inserted": obs_rows_inserted,
        "signals_attached": signals_attached,
        "groups_total": len(groups),
        "groups_skipped": groups_skipped,
        "edges_attempted": edges_attempted,
        "edges_inserted": edges_inserted,
        "decisions": decisions,
        "clusters_total": len(components),
        "clusters_inserted": cluster_rows_inserted,
        "members_inserted": member_rows_inserted,
        "elapsed_seconds": elapsed,
    }

    # Persist a compact run record for later debugging without scraping stdout.
    conn = connect_db(db_path)
    ensure_schema(conn, overwrite=False)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("resolver.people.last_run_utc",),
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (
            "resolver.people.config_json",
            json.dumps(config, ensure_ascii=True, separators=(",", ":")),
        ),
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (
            "resolver.people.summary_json",
            json.dumps(summary, ensure_ascii=True, separators=(",", ":")),
        ),
    )
    conn.commit()
    conn.close()

    return summary


def main():
    args = _parse_args()
    summary = build_resolve_people(
        db_path=args.db,
        person_types=args.person_types,
        reset=args.reset,
        max_group_size=args.max_group_size,
    )
    print("Resolve people summary")
    for key in (
        "observations_total",
        "observations_attempted",
        "observations_inserted",
        "signals_attached",
        "groups_total",
        "groups_skipped",
        "edges_attempted",
        "edges_inserted",
        "clusters_total",
        "clusters_inserted",
        "members_inserted",
        "elapsed_seconds",
    ):
        print(f"  {key}: {summary[key]}")
    decisions = summary["decisions"]
    print("  decisions:")
    print(f"    auto_merge: {decisions['auto_merge']}")
    print(f"    needs_review: {decisions['needs_review']}")
    print(f"    no_merge: {decisions['no_merge']}")


if __name__ == "__main__":
    main()
