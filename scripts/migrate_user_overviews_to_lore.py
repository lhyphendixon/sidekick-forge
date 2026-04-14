#!/usr/bin/env python3
"""
One-shot migration: user_overviews (legacy) → lore_files (new).

Iterates every client Supabase instance, finds all populated user_overviews
rows, transforms them into Lore category markdown, and writes them into each
user's HOME client Supabase (per the same resolver logic used at runtime).

Safety rules:
  - Only writes to a Lore category if the target is currently empty or just a
    template (avoids clobbering rich content the user already built).
  - Skips overviews with no meaningful data.
  - Dry-run mode prints what would be migrated without writing.
  - Logs every migration with (user_id, source_client, target_client, categories).

Usage:
    python3 scripts/migrate_user_overviews_to_lore.py            # apply
    python3 scripts/migrate_user_overviews_to_lore.py --dry-run  # preview
"""

import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def has_real_content(value: Any) -> bool:
    """Return True if value carries at least one non-empty field."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return any(has_real_content(v) for v in value)
    if isinstance(value, dict):
        return any(has_real_content(v) for v in value.values())
    return True


def _bullet(label: str, value: Any) -> Optional[str]:
    """Format a single key/value as a markdown bullet, or None if empty."""
    if not has_real_content(value):
        return None
    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value if has_real_content(v)]
        if not items:
            return None
        return f"- **{label}**: " + "; ".join(items)
    if isinstance(value, dict):
        # Flatten one level of nesting for rendering
        parts = []
        for k, v in value.items():
            if has_real_content(v):
                k_label = k.replace("_", " ").title()
                if isinstance(v, (list, tuple)):
                    parts.append(f"{k_label}: " + "; ".join(str(x) for x in v if has_real_content(x)))
                else:
                    parts.append(f"{k_label}: {v}")
        if not parts:
            return None
        return f"- **{label}**: " + " | ".join(parts)
    return f"- **{label}**: {value}"


def _section(header: str, lines: List[Optional[str]]) -> str:
    kept = [l for l in lines if l]
    if not kept:
        return ""
    return f"## {header}\n" + "\n".join(kept)


def _existing_is_empty_or_template(content: str) -> bool:
    """Match the Lore MCP's scoring: return True if the content is empty
    or just a template with no real filled-in fields."""
    if not content or not content.strip():
        return True
    text = content.strip()
    lines = [l for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
    non_header = []
    for line in lines:
        stripped = line.strip().rstrip("|").strip()
        if re.match(r"^[\-\|:\s]+$", stripped):
            continue
        if stripped in ("-", "|", "- ", "| |"):
            continue
        if re.match(r"^-\s+\*\*[^*]+\*\*:\s*$", stripped):
            continue
        if re.match(r"^\|(\s*\|)+\s*$", stripped):
            continue
        non_header.append(stripped)
    return len(non_header) == 0


# ---------------------------------------------------------------------------
# Transformers — legacy overview → Lore category markdown
# ---------------------------------------------------------------------------

def overview_to_identity(ov: Dict[str, Any]) -> str:
    identity = ov.get("identity", {}) or {}
    biography = ov.get("biography", {}) or {}
    lines = [
        _bullet("Role", identity.get("role")),
        _bullet("Team", identity.get("team")),
        _bullet("Background", identity.get("background")),
        _bullet("Domains", identity.get("domains")),
        _bullet("Personality", identity.get("personality")),
        _bullet("Creative Voice", identity.get("creative_voice")),
    ]
    # Capture any other identity fields we didn't explicitly name
    known = {"role", "team", "background", "domains", "personality", "creative_voice"}
    for k, v in identity.items():
        if k not in known and has_real_content(v):
            lines.append(_bullet(k.replace("_", " ").title(), v))
    # Biography fields
    if has_real_content(biography):
        if biography.get("essence"):
            lines.append(f"- **Essence**: {biography['essence']}")
        if biography.get("mission"):
            lines.append(f"- **Mission**: {biography['mission']}")
        if biography.get("summary"):
            lines.append(f"- **Summary**: {biography['summary']}")
        for k, v in biography.items():
            if k not in {"essence", "mission", "summary"} and has_real_content(v):
                lines.append(_bullet(k.replace("_", " ").title(), v))

    kept = [l for l in lines if l]
    if not kept:
        return ""
    return "# Identity\n\n" + "\n".join(kept)


def overview_to_goals(ov: Dict[str, Any]) -> str:
    goals = ov.get("goals", {}) or {}
    if not has_real_content(goals):
        return ""
    lines = []
    if goals.get("vision"):
        lines.append(f"- **Vision**: {goals['vision']}")
    if goals.get("primary"):
        lines.append(f"- **Primary**: {goals['primary']}")
    secondary = goals.get("secondary")
    if has_real_content(secondary):
        if isinstance(secondary, (list, tuple)):
            for item in secondary:
                if has_real_content(item):
                    lines.append(f"- {item}")
        else:
            lines.append(f"- **Secondary**: {secondary}")
    if goals.get("blockers"):
        blockers = goals["blockers"]
        if isinstance(blockers, (list, tuple)):
            for b in blockers:
                if has_real_content(b):
                    lines.append(f"- **Blocker**: {b}")
        else:
            lines.append(f"- **Blockers**: {blockers}")
    # Catch-all for other goal fields
    known = {"vision", "primary", "secondary", "blockers"}
    for k, v in goals.items():
        if k not in known and has_real_content(v):
            lines.append(_bullet(k.replace("_", " ").title(), v))
    if not lines:
        return ""
    return "# Goals and Priorities\n\n" + "\n".join(lines)


def overview_to_communication_style(ov: Dict[str, Any]) -> str:
    ws = ov.get("working_style", {}) or {}
    if not has_real_content(ws):
        return ""
    lines = []
    if ws.get("communication"):
        lines.append(f"- **Communication**: {ws['communication']}")
    if ws.get("decision_making"):
        lines.append(f"- **Decision-making**: {ws['decision_making']}")
    if ws.get("notes"):
        lines.append(f"- **Notes**: {ws['notes']}")
    if ws.get("tone"):
        lines.append(f"- **Tone**: {ws['tone']}")
    known = {"communication", "decision_making", "notes", "tone"}
    for k, v in ws.items():
        if k not in known and has_real_content(v):
            lines.append(_bullet(k.replace("_", " ").title(), v))
    if not lines:
        return ""
    return "# Communication Style\n\n" + "\n".join(lines)


def overview_to_preferences(ov: Dict[str, Any]) -> str:
    ic = ov.get("important_context")
    if not has_real_content(ic):
        return ""
    lines = []
    if isinstance(ic, (list, tuple)):
        for item in ic:
            if has_real_content(item):
                lines.append(f"- {item}")
    elif isinstance(ic, dict):
        for k, v in ic.items():
            if has_real_content(v):
                lines.append(_bullet(k.replace("_", " ").title(), v))
    else:
        lines.append(f"- {ic}")
    if not lines:
        return ""
    return "# Preferences and Constraints\n\n" + "\n".join(lines)


def overview_to_team(ov: Dict[str, Any]) -> str:
    rh = ov.get("relationship_history", {}) or {}
    if not has_real_content(rh):
        return ""
    lines = []
    key_wins = rh.get("key_wins")
    if has_real_content(key_wins):
        if isinstance(key_wins, (list, tuple)):
            for w in key_wins:
                if has_real_content(w):
                    lines.append(f"- **Key win**: {w}")
        else:
            lines.append(f"- **Key wins**: {key_wins}")
    threads = rh.get("ongoing_threads")
    if has_real_content(threads):
        if isinstance(threads, (list, tuple)):
            for t in threads:
                if has_real_content(t):
                    lines.append(f"- **Ongoing**: {t}")
        else:
            lines.append(f"- **Ongoing threads**: {threads}")
    known = {"key_wins", "ongoing_threads"}
    for k, v in rh.items():
        if k not in known and has_real_content(v):
            lines.append(_bullet(k.replace("_", " ").title(), v))
    if not lines:
        return ""
    return "# Team and Relationships\n\n" + "\n".join(lines)


CATEGORY_TRANSFORMERS = {
    "identity":                    overview_to_identity,
    "goals_and_priorities":        overview_to_goals,
    "communication_style":         overview_to_communication_style,
    "preferences_and_constraints": overview_to_preferences,
    "team_and_relationships":      overview_to_team,
}


# ---------------------------------------------------------------------------
# Home client resolver (matches trigger_multitenant.py logic)
# ---------------------------------------------------------------------------

def resolve_home_client_id(
    platform_sb,
    user_id: str,
    platform_url: str,
    fallback_client_id: Optional[str] = None,
) -> Optional[Tuple[str, Optional[str], Optional[str]]]:
    """Return (home_client_id, target_url, target_key) for a user.
    target_url/key are None if the user's home is the platform DB (Adventurer
    tier). Returns None only if nothing can be resolved.

    Resolution order:
    1. tenant_assignments.admin_client_ids (from user_metadata)
    2. Superadmin → Leandrew Dixon
    3. fallback_client_id (source client where overview was found — for end-users)
    """
    home_client_id: Optional[str] = None

    try:
        u = platform_sb.auth.admin.get_user_by_id(user_id)
        if u and u.user:
            user_meta = getattr(u.user, "user_metadata", {}) or {}
            app_meta = getattr(u.user, "app_metadata", {}) or {}

            admin_client_ids = (user_meta.get("tenant_assignments") or {}).get("admin_client_ids") or []
            if admin_client_ids:
                home_client_id = admin_client_ids[0]

            if not home_client_id:
                platform_role = (
                    user_meta.get("platform_role")
                    or app_meta.get("platform_role")
                    or ""
                ).lower()
                if platform_role in ("super_admin", "superadmin"):
                    try:
                        fb = (
                            platform_sb.table("clients")
                            .select("id")
                            .eq("name", "Leandrew Dixon")
                            .maybe_single()
                            .execute()
                        )
                        if fb and fb.data:
                            home_client_id = fb.data["id"]
                    except Exception:
                        pass
    except Exception:
        pass

    # Final fallback: use the source client (where we found the overview)
    if not home_client_id and fallback_client_id:
        home_client_id = fallback_client_id

    if not home_client_id:
        return None

    try:
        row = (
            platform_sb.table("clients")
            .select("supabase_url,supabase_service_role_key")
            .eq("id", home_client_id)
            .single()
            .execute()
        )
        if not row.data:
            return home_client_id, None, None
        t_url = row.data.get("supabase_url")
        t_key = row.data.get("supabase_service_role_key")
        if t_url and t_key and t_url != platform_url:
            return home_client_id, t_url, t_key
    except Exception:
        pass
    return home_client_id, None, None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--user", help="Limit migration to a single user_id")
    args = parser.parse_args()

    platform_url = os.getenv("SUPABASE_URL")
    platform_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    platform_sb = create_client(platform_url, platform_key)

    # 1. Enumerate all clients with dedicated Supabase
    clients = platform_sb.table("clients").select(
        "id,name,tier,hosting_type,supabase_url,supabase_service_role_key"
    ).execute().data

    dedicated = [
        c for c in clients
        if c.get("supabase_url") and c.get("supabase_service_role_key")
    ]
    print(f"Scanning {len(dedicated)} client instances for user_overviews...")

    # 2. Collect all user_overviews rows, keyed by user_id
    # (same user may have multiple overviews across different clients;
    # we merge them and remember each source client for fallback routing)
    collected: Dict[str, Dict[str, Any]] = {}
    # Structure: user_id -> {"source_names": [...], "source_client_ids": [...], "merged_overview": {}}
    row_counts: Dict[str, int] = defaultdict(int)

    for client in dedicated:
        try:
            sb = create_client(client["supabase_url"], client["supabase_service_role_key"])
            rows = sb.table("user_overviews").select("user_id,overview,updated_at").execute().data
        except Exception as exc:
            print(f"  ⚠️  {client['name']}: skipped ({exc})")
            continue

        client_populated = 0
        for r in rows:
            uid = r.get("user_id")
            ov = r.get("overview") or {}
            if not uid or not has_real_content(ov):
                continue
            if args.user and uid != args.user:
                continue

            client_populated += 1
            row_counts[uid] += 1

            if uid not in collected:
                collected[uid] = {
                    "source_names": [client["name"]],
                    "source_client_ids": [client["id"]],
                    "merged_overview": ov,
                }
            else:
                entry = collected[uid]
                entry["source_names"].append(client["name"])
                entry["source_client_ids"].append(client["id"])
                merged = dict(entry["merged_overview"])
                for k, v in ov.items():
                    if has_real_content(v) and not has_real_content(merged.get(k)):
                        merged[k] = v
                entry["merged_overview"] = merged

        if client_populated:
            print(f"  • {client['name']:30s} {client_populated} populated overview(s)")

    print(f"\nFound {len(collected)} users with non-empty user_overviews")
    print(f"Total overview rows scanned: {sum(row_counts.values())}")

    if not collected:
        print("Nothing to migrate.")
        return

    # 3. For each user, resolve home Lore target and migrate
    migrated = 0
    skipped_no_home = 0
    skipped_existing = 0
    target_written: Dict[str, int] = defaultdict(int)

    for user_id, entry in collected.items():
        overview = entry["merged_overview"]
        source_name = " + ".join(entry["source_names"])
        fallback_cid = entry["source_client_ids"][0] if entry["source_client_ids"] else None

        home = resolve_home_client_id(platform_sb, user_id, platform_url, fallback_client_id=fallback_cid)
        if not home:
            skipped_no_home += 1
            continue

        home_client_id, target_url, target_key = home

        # Connect to home Supabase
        if target_url and target_key:
            home_sb = create_client(target_url, target_key)
            target_label = target_url.split("//")[1].split(".")[0]
        else:
            home_sb = platform_sb
            target_label = "platform"

        # Build category content from this user's overview
        category_contents: Dict[str, str] = {}
        for category, transform in CATEGORY_TRANSFORMERS.items():
            content = transform(overview)
            if content:
                category_contents[category] = content

        if not category_contents:
            continue

        # Check each target; only write if empty or template
        existing_rows = home_sb.table("lore_files").select(
            "category,content"
        ).eq("user_id", user_id).in_("category", list(category_contents.keys())).execute()

        existing_map = {r["category"]: r.get("content") or "" for r in (existing_rows.data or [])}

        categories_to_write = []
        for category, new_content in category_contents.items():
            existing = existing_map.get(category, "")
            if _existing_is_empty_or_template(existing):
                categories_to_write.append((category, new_content))
            else:
                skipped_existing += 1

        if not categories_to_write:
            continue

        print(f"→ {user_id[:8]}  source={source_name[:40]:40s}  target={target_label:25s}  cats={[c for c,_ in categories_to_write]}")

        if args.dry_run:
            continue

        for category, new_content in categories_to_write:
            try:
                home_sb.table("lore_files").upsert(
                    {"user_id": user_id, "category": category, "content": new_content},
                    on_conflict="user_id,category",
                ).execute()
                target_written[target_label] += 1
            except Exception as exc:
                print(f"   ✗ {category}: {exc}")
        migrated += 1

    print()
    print("=" * 60)
    print(f"Users processed:   {len(collected)}")
    print(f"Users migrated:    {migrated}")
    print(f"Skipped (no home): {skipped_no_home}")
    print(f"Categories left untouched (already populated): {skipped_existing}")
    if target_written:
        print(f"Rows written per target:")
        for t, n in target_written.items():
            print(f"  {t:25s} {n}")
    if args.dry_run:
        print("\n(dry run — no changes made)")


if __name__ == "__main__":
    main()
