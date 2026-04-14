"""End-to-end test for the Supabase-backed Lore MCP server.

Runs against the live lore-mcp container on localhost:8082. Uses a throwaway
UUID as test user_id. Exercises every tool via both SSE (MCP protocol) and
the REST admin API. Asserts tenant isolation and cleans up after itself.
"""

import asyncio
import sys
import uuid

import httpx

try:
    from mcp import ClientSession
    from mcp.client.sse import sse_client
except ImportError:
    print("mcp package not installed — skipping SSE tests")
    sse_client = None

MCP_URL = "http://localhost:8082/mcp/sse"
ADMIN_API = "http://localhost:8082/admin-api"

passed = 0
failed = 0


def ok(n, label):
    global passed
    passed += 1
    print(f"PASS  [{n}] {label}")


def fail(n, label, detail):
    global failed
    failed += 1
    print(f"FAIL  [{n}] {label}: {detail}")


async def run_rest_tests(user_a: str, user_b: str) -> None:
    """Test the REST admin API."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        # 1. List categories for user_a
        r = await client.get(f"{ADMIN_API}/categories", params={"user_id": user_a})
        if r.status_code == 200 and len(r.json()) == 10:
            ok("REST-01", "list_categories returns 10 categories")
        else:
            fail("REST-01", "list_categories", f"{r.status_code}: {r.text[:100]}")

        # 2. Get empty category
        r = await client.get(f"{ADMIN_API}/category/identity", params={"user_id": user_a})
        if r.status_code == 200 and r.json()["content"] == "":
            ok("REST-02", "get empty category returns empty string")
        else:
            fail("REST-02", "get empty category", r.text[:200])

        # 3. Write a category
        content = "# Identity\n\n- **Name**: User A Test\n- **Role**: QA\n- **Organization**: TestCorp\n- **Philosophy**: Automate everything\n- **Personal Context**: Running tenancy tests"
        r = await client.put(
            f"{ADMIN_API}/category/identity",
            params={"user_id": user_a},
            json={"content": content},
        )
        if r.status_code == 200:
            ok("REST-03", "write identity for user_a")
        else:
            fail("REST-03", "write identity", r.text[:200])

        # 4. Read it back
        r = await client.get(f"{ADMIN_API}/category/identity", params={"user_id": user_a})
        if r.status_code == 200 and "User A Test" in r.json()["content"]:
            ok("REST-04", "read identity back")
        else:
            fail("REST-04", "read identity back", r.text[:200])

        # 5. Depth score reflects the write
        r = await client.get(f"{ADMIN_API}/depth-score", params={"user_id": user_a})
        score = r.json()
        identity_layer = next((l for l in score["layers"] if l["key"] == "identity"), None)
        if identity_layer and identity_layer["level"] in ("growing", "strong"):
            ok("REST-05", f"depth score: identity = {identity_layer['level']}")
        else:
            fail("REST-05", "depth score", str(identity_layer))

        # 6. Summary auto-regenerates
        r = await client.get(f"{ADMIN_API}/summary", params={"user_id": user_a})
        if r.status_code == 200 and "User A Test" in r.json()["content"]:
            ok("REST-06", "summary auto-regenerated with identity content")
        else:
            fail("REST-06", "summary regeneration", r.text[:200])

        # 7. Tenant isolation: user_b sees nothing
        r = await client.get(f"{ADMIN_API}/category/identity", params={"user_id": user_b})
        if r.status_code == 200 and r.json()["content"] == "":
            ok("REST-07", "user_b sees empty identity (tenant isolation)")
        else:
            fail("REST-07", "tenant isolation", r.text[:200])

        # 8. Categories list shows has_content for user_a
        r = await client.get(f"{ADMIN_API}/categories", params={"user_id": user_a})
        identity = next((c for c in r.json() if c["key"] == "identity"), None)
        if identity and identity["has_content"]:
            ok("REST-08", "categories list shows identity as populated for user_a")
        else:
            fail("REST-08", "categories has_content", str(identity))

        # 9. Categories list for user_b shows all empty
        r = await client.get(f"{ADMIN_API}/categories", params={"user_id": user_b})
        if all(not c["has_content"] for c in r.json()):
            ok("REST-09", "user_b sees all categories empty")
        else:
            fail("REST-09", "user_b has_content", "user_b has unexpected content")

        # 10. Invalid category returns 400
        r = await client.get(f"{ADMIN_API}/category/invalid_name", params={"user_id": user_a})
        if r.status_code == 400:
            ok("REST-10", "invalid category returns 400")
        else:
            fail("REST-10", "invalid category", f"{r.status_code}")


async def run_sse_tests(user_a: str) -> None:
    """Test the MCP SSE protocol tool surface."""
    if sse_client is None:
        return

    async with sse_client(MCP_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Tool list
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            expected = sorted([
                "get_lore_summary", "get_lore_category",
                "update_lore_category", "list_lore_categories", "search_lore",
            ])
            if names == expected:
                ok("MCP-01", "all 5 tools registered")
            else:
                fail("MCP-01", "tool list", str(names))

            # get_lore_summary (should return existing content from REST test above)
            result = await session.call_tool("get_lore_summary", {"user_id": user_a})
            text = result.content[0].text
            if "User A Test" in text:
                ok("MCP-02", "get_lore_summary returns generated summary")
            else:
                fail("MCP-02", "get_lore_summary", text[:200])

            # get_lore_category
            result = await session.call_tool("get_lore_category", {"user_id": user_a, "category": "identity"})
            text = result.content[0].text
            if "User A Test" in text:
                ok("MCP-03", "get_lore_category returns identity content")
            else:
                fail("MCP-03", "get_lore_category", text[:200])

            # update_lore_category via MCP tool
            result = await session.call_tool("update_lore_category", {
                "user_id": user_a,
                "category": "goals_and_priorities",
                "content": "# Goals\n\n- Test the MCP via SSE protocol",
            })
            text = result.content[0].text
            if "Successfully updated" in text:
                ok("MCP-04", "update_lore_category via SSE")
            else:
                fail("MCP-04", "update via SSE", text[:200])

            # search_lore
            result = await session.call_tool("search_lore", {"user_id": user_a, "query": "User A"})
            text = result.content[0].text
            if "identity" in text:
                ok("MCP-05", "search_lore finds identity content")
            else:
                fail("MCP-05", "search_lore", text[:200])

            # list_lore_categories
            result = await session.call_tool("list_lore_categories", {"user_id": user_a})
            text = result.content[0].text
            if text.count("**") >= 20:  # 10 categories, 2 bolds each
                ok("MCP-06", "list_lore_categories returns all 10")
            else:
                fail("MCP-06", "list_lore_categories", text[:200])


def cleanup(user_a: str, user_b: str) -> None:
    import os
    from dotenv import load_dotenv
    load_dotenv(".env")
    from supabase import create_client
    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
    for uid in [user_a, user_b]:
        sb.table("lore_files").delete().eq("user_id", uid).execute()
        sb.table("lore_summary").delete().eq("user_id", uid).execute()


async def main():
    user_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    print(f"Test user A: {user_a[:8]}")
    print(f"Test user B: {user_b[:8]}")
    print()

    try:
        await run_rest_tests(user_a, user_b)
        print()
        await run_sse_tests(user_a)
    finally:
        cleanup(user_a, user_b)

    print()
    print(f"Results: {passed} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
