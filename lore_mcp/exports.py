"""
Lore export builders — markdown, JSON, self-host ZIP.

Each builder takes the same `payload` dict (user_id, summary, categories,
category_descriptions, exported_at, schema_version) and returns a tuple of
(bytes, filename). The handler in app.py is a thin shim that loads the
payload from storage and picks the right builder.

The self-host ZIP bundles a ready-to-run Node.js MCP server from
`selfhost_template/` alongside the user's markdown data, with a README that
walks through three deployment paths (local / Railway / Render) and a
Dockerfile for the technically inclined.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path
from typing import Any, Dict, Tuple

TEMPLATE_DIR = Path(__file__).parent / "selfhost_template"


def _ts_slug(payload: Dict[str, Any]) -> str:
    return payload.get("exported_at", "export").replace(":", "").replace("-", "")[:15]


# ---------------------------------------------------------------------------
# Markdown package — one .md file per category plus summary + manifest
# ---------------------------------------------------------------------------

def build_markdown_zip(payload: Dict[str, Any]) -> Tuple[bytes, str]:
    """Returns (zip_bytes, filename). Structure:

        lore/
            README.md              — explains the package + how to use
            SUMMARY.md             — compressed summary
            categories/
                identity.md
                goals_and_priorities.md
                ...
            lore.json              — machine-readable mirror for round-trips
    """
    buf = io.BytesIO()
    categories: Dict[str, str] = payload["categories"]
    descriptions: Dict[str, str] = payload["category_descriptions"]

    readme = _render_markdown_readme(payload)

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("lore/README.md", readme)
        zf.writestr("lore/SUMMARY.md", payload.get("summary") or "# Lore Summary\n\n_No summary generated yet._\n")
        for cat, content in categories.items():
            body = content.strip() or f"# {cat.replace('_', ' ').title()}\n\n_No content yet._\n"
            if not body.startswith("#"):
                body = f"# {cat.replace('_', ' ').title()}\n\n{body}"
            zf.writestr(f"lore/categories/{cat}.md", body)
            # Human-readable description as a sidecar
        zf.writestr("lore/lore.json", json.dumps(_strip_payload_for_json(payload), indent=2))

    return buf.getvalue(), f"lore-export-{_ts_slug(payload)}.zip"


def _render_markdown_readme(payload: Dict[str, Any]) -> str:
    cats = payload["category_descriptions"]
    lines = [
        "# Your Lore",
        "",
        "This is your personal context, exported from Sidekick Forge.",
        "It belongs to you. You can read it, edit it, fork it, re-import it,",
        "or self-host it. No lock-in.",
        "",
        f"**Exported:** {payload.get('exported_at')}  ",
        f"**Schema:** `{payload.get('schema_version')}`",
        "",
        "## What's in this package",
        "",
        "- `SUMMARY.md` — compressed summary for system prompt injection",
        "- `categories/*.md` — one file per Lore category",
        "- `lore.json` — machine-readable mirror (for round-trip imports)",
        "",
        "## Categories",
        "",
    ]
    for cat, desc in cats.items():
        lines.append(f"- **{cat}** — {desc}")
    lines += [
        "",
        "## What you can do with this",
        "",
        "1. **Read it.** It's just markdown.",
        "2. **Edit it in Obsidian, Notion, or any editor.** Works everywhere.",
        "3. **Re-import it** to Sidekick Forge from the Lore admin page.",
        "4. **Self-host it** — grab the self-host package for a ready-to-run MCP server.",
        "",
        "## Your Lore, your call.",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# JSON export — a single structured document
# ---------------------------------------------------------------------------

def build_json_export(payload: Dict[str, Any]) -> Tuple[bytes, str]:
    body = json.dumps(_strip_payload_for_json(payload), indent=2)
    return body.encode("utf-8"), f"lore-export-{_ts_slug(payload)}.json"


def _strip_payload_for_json(payload: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-safe subset of the payload. user_id is left in so the export is
    self-identifying, but nothing sensitive is included."""
    return {
        "schema_version": payload.get("schema_version"),
        "exported_at": payload.get("exported_at"),
        "user_id": payload.get("user_id"),
        "summary": payload.get("summary") or "",
        "categories": payload.get("categories") or {},
        "category_descriptions": payload.get("category_descriptions") or {},
    }


# ---------------------------------------------------------------------------
# Self-host ZIP — data + Node.js MCP server + deploy scaffolding
# ---------------------------------------------------------------------------

def build_selfhost_zip(payload: Dict[str, Any]) -> Tuple[bytes, str]:
    """Bundles everything needed to run a personal Lore MCP server:

        lore-selfhost/
            server.js             ~50-line MCP server (stdio + SSE)
            package.json
            Dockerfile
            railway.json          one-click Railway deploy config
            render.yaml           Render blueprint
            .env.example          config template (port, token)
            README.md             three deployment paths + token setup
            data/
                SUMMARY.md
                categories/*.md
                lore.json          machine-readable mirror
    """
    buf = io.BytesIO()
    categories: Dict[str, str] = payload["categories"]

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Static template files (server.js, package.json, Dockerfile, etc.)
        for template_path in _iter_template_files():
            rel = template_path.relative_to(TEMPLATE_DIR)
            zf.writestr(f"lore-selfhost/{rel.as_posix()}", template_path.read_bytes())

        # 2. Seeded user data under data/
        summary_md = payload.get("summary") or "# Lore Summary\n\n_Empty_\n"
        zf.writestr("lore-selfhost/data/SUMMARY.md", summary_md)
        for cat, content in categories.items():
            body = content.strip() or f"# {cat.replace('_', ' ').title()}\n\n_No content yet._\n"
            if not body.startswith("#"):
                body = f"# {cat.replace('_', ' ').title()}\n\n{body}"
            zf.writestr(f"lore-selfhost/data/categories/{cat}.md", body)
        zf.writestr(
            "lore-selfhost/data/lore.json",
            json.dumps(_strip_payload_for_json(payload), indent=2),
        )

    return buf.getvalue(), f"lore-selfhost-{_ts_slug(payload)}.zip"


def _iter_template_files():
    if not TEMPLATE_DIR.exists():
        return
    for root, _dirs, files in os.walk(TEMPLATE_DIR):
        for f in files:
            yield Path(root) / f
