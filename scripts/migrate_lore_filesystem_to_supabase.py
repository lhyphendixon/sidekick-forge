#!/usr/bin/env python3
"""
One-shot migration: move existing filesystem Lore into a target user's
home Supabase instance.

Reads markdown files from /root/sidekick-forge/lore_mcp/lore_data/ and
upserts them into lore_files keyed by (user_id, category). Also regenerates
lore_summary.

Usage:
    python3 scripts/migrate_lore_filesystem_to_supabase.py \
        --user-email l-dixon@autonomite.net \
        --client-name "Leandrew Dixon"

The script resolves the user's auth.uid and the client's dedicated
Supabase credentials from the platform database.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client

LORE_DATA_DIR = Path("/root/sidekick-forge/lore_mcp/lore_data")

VALID_CATEGORIES = [
    "identity",
    "roles_and_responsibilities",
    "current_projects",
    "team_and_relationships",
    "tools_and_systems",
    "communication_style",
    "goals_and_priorities",
    "preferences_and_constraints",
    "domain_knowledge",
    "decision_log",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-email", required=True, help="Email of the user to own this Lore")
    parser.add_argument("--client-name", required=True, help="Name of the home client in the platform DB")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be migrated without writing")
    args = parser.parse_args()

    platform_url = os.getenv("SUPABASE_URL")
    platform_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not platform_url or not platform_key:
        print("❌ SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")
        sys.exit(1)

    platform = create_client(platform_url, platform_key)

    # Find the user by email
    print(f"🔍 Looking up user by email: {args.user_email}")
    try:
        users_response = platform.auth.admin.list_users()
        users = users_response if isinstance(users_response, list) else []
        matching = [u for u in users if getattr(u, "email", "").lower() == args.user_email.lower()]
        if not matching:
            print(f"❌ No user found with email {args.user_email}")
            sys.exit(1)
        if len(matching) > 1:
            print(f"⚠️  Multiple users match {args.user_email}, using first")
        user = matching[0]
        user_id = user.id
        print(f"  ✅ Resolved user_id: {user_id}")
    except Exception as exc:
        print(f"❌ Failed to look up user: {exc}")
        sys.exit(1)

    # Find the client and its Supabase credentials
    print(f"🔍 Looking up client: {args.client_name}")
    client_result = platform.table("clients").select(
        "id,name,tier,hosting_type,supabase_url,supabase_service_role_key"
    ).eq("name", args.client_name).execute()
    if not client_result.data:
        print(f"❌ No client found named '{args.client_name}'")
        sys.exit(1)
    client = client_result.data[0]
    target_url = client.get("supabase_url")
    target_key = client.get("supabase_service_role_key")
    if not target_url or not target_key:
        print(f"⚠️  Client {args.client_name} has no dedicated Supabase — will use platform DB")
        target_url = platform_url
        target_key = platform_key
    else:
        print(f"  ✅ Target: {target_url.split('//')[1].split('.')[0]} ({client.get('tier')})")

    # Read filesystem data
    if not LORE_DATA_DIR.exists():
        print(f"❌ Lore data dir not found: {LORE_DATA_DIR}")
        sys.exit(1)

    categories_to_migrate = {}
    for category in VALID_CATEGORIES:
        path = LORE_DATA_DIR / f"{category}.md"
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                categories_to_migrate[category] = content

    if not categories_to_migrate:
        print("⚠️  No category files with content found in filesystem")
        sys.exit(0)

    print(f"\n📦 Found {len(categories_to_migrate)} categories to migrate:")
    for cat, content in categories_to_migrate.items():
        print(f"  • {cat:30s} ({len(content)} chars)")

    if args.dry_run:
        print("\n(dry run — no writes)")
        sys.exit(0)

    # Write to target Supabase
    print(f"\n✍️  Writing to {target_url.split('//')[1].split('.')[0]}...")
    target = create_client(target_url, target_key)

    migrated = 0
    for category, content in categories_to_migrate.items():
        try:
            target.table("lore_files").upsert(
                {"user_id": user_id, "category": category, "content": content},
                on_conflict="user_id,category",
            ).execute()
            print(f"  ✅ {category}")
            migrated += 1
        except Exception as exc:
            print(f"  ❌ {category}: {exc}")

    # Regenerate summary
    summary_path = LORE_DATA_DIR / "lore_summary.md"
    if summary_path.exists():
        summary_content = summary_path.read_text(encoding="utf-8").strip()
        if summary_content:
            target.table("lore_summary").upsert(
                {"user_id": user_id, "content": summary_content},
                on_conflict="user_id",
            ).execute()
            print(f"  ✅ lore_summary")

    print(f"\n✨ Migration complete: {migrated}/{len(categories_to_migrate)} categories migrated")
    print(f"   User: {args.user_email} ({user_id})")
    print(f"   Instance: {target_url.split('//')[1].split('.')[0]}")


if __name__ == "__main__":
    main()
