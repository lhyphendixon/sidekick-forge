#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import urllib.request
import urllib.parse
from typing import Any, Dict, Optional

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

if not SUPABASE_URL or not SERVICE_ROLE_KEY:
    print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in the environment.")
    sys.exit(1)

HEADERS_JSON = {
    "apikey": SERVICE_ROLE_KEY,
    "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
    "Content-Type": "application/json",
}
HEADERS_UPSERT = {
    **HEADERS_JSON,
    "Prefer": "resolution=merge-duplicates",
}


def http_call(method: str, url: str, headers: Dict[str, str], data: Optional[Any] = None) -> (int, str):
    req = urllib.request.Request(url, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    payload = None
    if data is not None:
        payload = data if isinstance(data, (bytes, bytearray)) else json.dumps(data).encode("utf-8")
    try:
        with urllib.request.urlopen(req, payload) as resp:
            return resp.getcode(), resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def main():
    if len(sys.argv) < 3:
        print("Usage: inject_super_admin.py <email> <user_id> [full_name]")
        sys.exit(2)
    email = sys.argv[1].strip()
    user_id_fixed = sys.argv[2].strip()
    full_name = sys.argv[3].strip() if len(sys.argv) > 3 else ""

    # 1) Lookup existing user by email
    query = urllib.parse.urlencode({"email": email})
    code, body = http_call("GET", f"{SUPABASE_URL}/auth/v1/admin/users?{query}", HEADERS_JSON)
    user_id: Optional[str] = None
    if code == 200:
        try:
            data = json.loads(body)
            users = data.get("users") if isinstance(data, dict) else None
            if users is None and isinstance(data, list):
                users = data
            if users:
                match = [u for u in users if (u.get("email", "").lower() == email.lower())]
                if match:
                    user_id = match[0].get("id")
        except Exception:
            pass

    # 2) Create user if not found (with provided user_id if possible)
    if not user_id:
        payload = {
            "email": email,
            "email_confirm": True,
            "user_metadata": {
                "full_name": full_name,
                "platform_role": "super_admin"
            }
        }
        code, body = http_call("POST", f"{SUPABASE_URL}/auth/v1/admin/users", HEADERS_JSON, payload)
        if code not in (200, 201):
            # Try lookup again in case of duplicate
            code2, body2 = http_call("GET", f"{SUPABASE_URL}/auth/v1/admin/users?{query}", HEADERS_JSON)
            if code2 == 200:
                try:
                    data = json.loads(body2)
                    users = data.get("users") if isinstance(data, dict) else None
                    if users is None and isinstance(data, list):
                        users = data
                    if users:
                        match = [u for u in users if (u.get("email", "").lower() == email.lower())]
                        if match:
                            user_id = match[0].get("id")
                except Exception:
                    pass
            if not user_id:
                print(f"ERROR: Failed to create user: {body}")
                sys.exit(1)
        else:
            try:
                data = json.loads(body)
                user_id = data.get("id")
            except Exception:
                pass

    if not user_id:
        print("ERROR: Could not determine user id")
        sys.exit(1)

    print(f"Existing/created user id: {user_id}")

    # Note: Supabase Auth does not allow setting a custom user id directly via Admin API.
    # We ensure the email exists; the id may differ from the requested one.

    # 3) Ensure metadata has platform_role=super_admin
    meta_update = {"user_metadata": {"platform_role": "super_admin"}}
    code, body = http_call("PATCH", f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}", HEADERS_JSON, meta_update)
    if code not in (200, 201):
        # Try PUT as fallback
        code2, body2 = http_call("PUT", f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}", HEADERS_JSON, meta_update)
        if code2 not in (200, 201):
            print(f"WARN: Failed to update user metadata: {body or body2}")

    # 4) Seed super_admin role in roles table
    seed_roles = [{"key": "super_admin", "scope": "platform", "description": "Platform-wide administrator"}]
    code, body = http_call("POST", f"{SUPABASE_URL}/rest/v1/roles", HEADERS_UPSERT, seed_roles)
    if code not in (200, 201):
        print(f"WARN: Upsert roles returned {code}: {body}")

    # 5) Get role id
    code, body = http_call("GET", f"{SUPABASE_URL}/rest/v1/roles?key=eq.super_admin&select=id&limit=1", HEADERS_JSON)
    role_id: Optional[str] = None
    if code == 200:
        try:
            rows = json.loads(body)
            if isinstance(rows, list) and rows:
                role_id = rows[0].get("id")
        except Exception:
            pass

    # 6) Upsert platform_role_membership
    if role_id:
        payload = {"user_id": user_id, "role_id": role_id}
        code, body = http_call("POST", f"{SUPABASE_URL}/rest/v1/platform_role_memberships", HEADERS_UPSERT, payload)
        if code not in (200, 201):
            print(f"WARN: Upsert platform_role_memberships returned {code}: {body}")
        else:
            print("Assigned super_admin via platform_role_memberships")
    else:
        print("WARN: Could not determine role_id for super_admin; metadata fallback applied")

    print("Done.")


if __name__ == "__main__":
    main()


