#!/usr/bin/env python3
"""Print current provisioning jobs from the platform database."""
import os
from datetime import datetime

from dotenv import load_dotenv
from supabase import create_client


def main() -> None:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(repo_root, ".env"))

    supabase_url = os.getenv("SUPABASE_URL")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not service_key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")

    client = create_client(supabase_url, service_key)

    result = (
        client.table("client_provisioning_jobs")
        .select("*")
        .order("created_at")
        .execute()
    )

    jobs = result.data or []
    if not jobs:
        print("✅ No provisioning jobs queued")
        return

    def fmt_ts(ts: str | None) -> str:
        if not ts:
            return "-"
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return ts

    print(f"🧾 {len(jobs)} provisioning job(s):")
    for job in jobs:
        print("- job_id: {id}".format(**job))
        print(f"  client_id: {job.get('client_id')}")
        print(f"  job_type: {job.get('job_type')}")
        print(f"  attempts: {job.get('attempts', 0)}")
        print(f"  claimed_at: {fmt_ts(job.get('claimed_at'))}")
        print(f"  last_error: {job.get('last_error') or '-'}")
        print(f"  created_at: {fmt_ts(job.get('created_at'))}")
        print(f"  updated_at: {fmt_ts(job.get('updated_at'))}")
        print()


if __name__ == "__main__":
    main()
