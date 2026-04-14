"""End-to-end test suite for Lore MCP server."""

import asyncio

from mcp import ClientSession
from mcp.client.sse import sse_client

LORE_URL = "http://localhost:8082/mcp/sse"

ALL_CATEGORIES = [
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

SAMPLE_DATA = {
    "identity": (
        "# Identity\n\n"
        "- **Name**: Leandrew Dixon\n"
        "- **Role**: Founder & CEO\n"
        "- **Organization**: Autonomite\n"
        "- **Philosophy**: AI should amplify human agency, not replace it\n"
        "- **Personal Context**: Technical founder building Sidekick Forge"
    ),
    "roles_and_responsibilities": (
        "# Roles and Responsibilities\n\n"
        "- **Day-to-day work**: Architecture decisions, agent pipeline development, deploy management\n"
        "- **Key outputs**: Working production code, system design docs, deployment scripts\n"
        "- **Decisions I own**: Tech stack, agent architecture, infrastructure, hiring priorities\n"
        "- **Who I serve**: Autonomite customers, sidekick end-users, future team members"
    ),
    "current_projects": (
        "# Current Projects\n\n"
        "## Active Workstreams\n\n"
        "| Project | Status | Priority |\n"
        "|---------|--------|----------|\n"
        "| Lore MCP | In Progress | P0 |\n"
        "| Semrush Ability | In Progress | P1 |\n"
        "| Video Chat Mode | Stabilizing | P1 |"
    ),
    "team_and_relationships": (
        "# Team and Relationships\n\n"
        "## Key People\n\n"
        "| Person | Role | What the Relationship Requires |\n"
        "|--------|------|-------------------------------|\n"
        "| Franca | First customer / content marketer | Deep empathy for non-technical users |\n"
        "| Claude | AI coding partner | Clear specs, good context, trust the output |"
    ),
    "tools_and_systems": (
        "# Tools and Systems\n\n"
        "- **Stack**: Python (FastAPI), Docker, Supabase (PostgreSQL + pgvector), LiveKit, Nginx\n"
        "- **Architecture patterns**: Multi-tenant SaaS, stateless agent workers, MCP for tool integration\n"
        "- **Constraints**: No Redis, no local vector stores, no hardcoded API keys\n"
        "- **Design systems**: Sidekick embed (iframe-based), admin dashboard, agent wizard"
    ),
    "communication_style": (
        "# Communication Style\n\n"
        "- **Tone**: Direct, technical, no fluff. Respects conciseness.\n"
        "- **Formatting preferences**: Markdown, tables for structured data, code blocks for code\n"
        "- **Editing preferences**: Ship fast, iterate. Prefers working code over design docs.\n"
        '- **Voice matching notes**: Speaks in systems thinking. Uses "wire up", "hook into".'
    ),
    "goals_and_priorities": (
        "# Goals and Priorities\n\n"
        "- **This week**: Ship Lore MCP Phase 1, stabilize video chat\n"
        "- **This quarter**: Launch Sidekick Forge publicly, onboard 5 paying customers\n"
        "- **This year**: Reach 50 active sidekicks across 20 clients\n"
        "- **Career**: Build a company that makes AI accessible and personal for everyone"
    ),
    "preferences_and_constraints": (
        "# Preferences and Constraints\n\n"
        "- **Always**: Fix root causes, use dynamic API keys from Supabase, fail fast with clear errors\n"
        "- **Never**: Hardcode secrets, add Redis back, use ChromaDB or sentence-transformers\n"
        "- **Tool preferences**: Docker Compose for orchestration, Supabase for everything DB\n"
        "- **Hard constraints**: Multi-tenant isolation at all layers, stateless workers"
    ),
    "domain_knowledge": (
        "# Domain Knowledge\n\n"
        "- **Expertise areas**: Multi-tenant SaaS architecture, real-time voice/video AI agents, MCP protocol\n"
        "- **Frameworks used**: LiveKit Agents 1.5.0, FastMCP, Supabase client libraries, Pydantic v2\n"
        "- **What NOT to explain**: Python async patterns, Docker basics, git workflows, REST API design"
    ),
    "decision_log": (
        "# Decision Log\n\n"
        "| Date | Decision | Reasoning | Outcome |\n"
        "|------|----------|-----------|----------|\n"
        "| 2026-04-12 | Lore uses filesystem over RAG | Deterministic category retrieval, no embedding overhead | TBD |\n"
        "| 2026-04-12 | Lore MCP in Python with FastMCP | Matches existing stack, minimal dependencies | Working |\n"
        "| 2026-04-10 | Phased out Redis entirely | Single-instance, in-process dedupe is sufficient | Simplified |"
    ),
}

passed = 0
failed = 0


def ok(n, label):
    global passed
    passed += 1
    print(f"PASS  [{n}/14] {label}")


def fail(n, label, detail):
    global failed
    failed += 1
    print(f"FAIL  [{n}/14] {label}: {detail}")


async def run_tests():
    async with sse_client(LORE_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1 — All 5 tools registered
            tools = await session.list_tools()
            tool_names = sorted(t.name for t in tools.tools)
            expected = sorted([
                "get_lore_summary", "get_lore_category",
                "update_lore_category", "list_lore_categories", "search_lore",
            ])
            if tool_names == expected:
                ok(1, "All 5 tools registered")
            else:
                fail(1, "Tool registration", f"{tool_names}")

            # 2 — All 10 categories listed
            result = await session.call_tool("list_lore_categories", {})
            text = result.content[0].text
            missing = [c for c in ALL_CATEGORIES if f"**{c}**" not in text]
            if not missing:
                ok(2, "All 10 categories listed")
            else:
                fail(2, "Category listing", f"missing: {missing}")

            # 3 — Read empty template
            result = await session.call_tool("get_lore_category", {"category": "identity"})
            text = result.content[0].text
            if "Identity" in text:
                ok(3, "Read empty template file")
            else:
                fail(3, "Read template", text[:100])

            # 4 — Invalid category returns error
            result = await session.call_tool("get_lore_category", {"category": "nonexistent"})
            text = result.content[0].text
            if "Unknown category" in text:
                ok(4, "Invalid category returns error")
            else:
                fail(4, "Invalid category", text[:100])

            # 5 — Populate all 10 categories
            update_failures = []
            for cat, content in SAMPLE_DATA.items():
                result = await session.call_tool(
                    "update_lore_category", {"category": cat, "content": content}
                )
                text = result.content[0].text
                if "Successfully updated" not in text:
                    update_failures.append(f"{cat}: {text[:80]}")
            if not update_failures:
                ok(5, f"All {len(SAMPLE_DATA)} categories populated")
            else:
                fail(5, "Populate categories", "; ".join(update_failures))

            # 6 — Persistence: re-read each category
            persistence_failures = []
            for cat, original in SAMPLE_DATA.items():
                result = await session.call_tool("get_lore_category", {"category": cat})
                text = result.content[0].text
                if text.strip() != original.strip():
                    persistence_failures.append(cat)
            if not persistence_failures:
                ok(6, "All categories persist correctly on re-read")
            else:
                fail(6, "Persistence", f"mismatch in: {persistence_failures}")

            # 7 — Summary auto-generated
            result = await session.call_tool("get_lore_summary", {})
            summary = result.content[0].text
            checks = {
                "header": "# Lore Summary" in summary,
                "identity": "Leandrew Dixon" in summary,
                "projects": "Lore MCP" in summary,
                "style": "Direct, technical" in summary,
            }
            failures = [k for k, v in checks.items() if not v]
            if not failures:
                ok(7, "Summary auto-generated with content from all categories")
            else:
                fail(7, "Summary content", f"missing: {failures}")

            # 8 — Search across multiple categories
            result = await session.call_tool("search_lore", {"query": "Autonomite"})
            text = result.content[0].text
            if "identity" in text and "roles_and_responsibilities" in text:
                ok(8, "Search finds keyword across multiple categories")
            else:
                fail(8, "Cross-category search", text[:200])

            # 9 — Search is case-insensitive
            result = await session.call_tool("search_lore", {"query": "fastapi"})
            text = result.content[0].text
            if "tools_and_systems" in text:
                ok(9, "Search is case-insensitive")
            else:
                fail(9, "Case-insensitive search", text[:200])

            # 10 — Search no results
            result = await session.call_tool("search_lore", {"query": "xyznonexistent123"})
            text = result.content[0].text
            if "No results found" in text:
                ok(10, "Search returns clean message for no matches")
            else:
                fail(10, "No-results search", text[:100])

            # 11 — Empty content rejected
            result = await session.call_tool(
                "update_lore_category", {"category": "identity", "content": "   "}
            )
            text = result.content[0].text
            if "empty" in text.lower():
                ok(11, "Empty content rejected on update")
            else:
                fail(11, "Empty content guard", text[:100])

            # 12 — Empty search query rejected
            result = await session.call_tool("search_lore", {"query": "   "})
            text = result.content[0].text
            if "empty" in text.lower():
                ok(12, "Empty search query rejected")
            else:
                fail(12, "Empty query guard", text[:100])

            # 13 — Update does not corrupt other categories
            result = await session.call_tool("get_lore_category", {"category": "domain_knowledge"})
            text = result.content[0].text
            if "Multi-tenant SaaS architecture" in text:
                ok(13, "Update to one category does not corrupt others")
            else:
                fail(13, "Cross-corruption check", text[:100])

            # 14 — Summary regeneration after partial update
            result = await session.call_tool(
                "update_lore_category",
                {"category": "identity", "content": "# Identity\n\n- **Name**: Test User\n- **Role**: QA Engineer"},
            )
            result = await session.call_tool("get_lore_summary", {})
            summary = result.content[0].text
            if "Test User" in summary and "Leandrew Dixon" not in summary:
                ok(14, "Summary regenerates correctly after partial update")
            else:
                fail(14, "Summary regeneration", "stale data in summary")

            # Restore identity after test 14
            await session.call_tool(
                "update_lore_category",
                {"category": "identity", "content": SAMPLE_DATA["identity"]},
            )

    print(f"\n{'=' * 40}")
    print(f"Results: {passed} passed, {failed} failed out of 14")
    if failed == 0:
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
        exit(1)


if __name__ == "__main__":
    asyncio.run(run_tests())
