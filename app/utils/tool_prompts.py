"""Utilities for embedding tool instructions into agent system prompts."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


InstructionSection = Dict[str, Optional[str]]


def _extract_instructions(tool: Mapping[str, Any]) -> Optional[str]:
    """Return the best-effort instruction string for the given tool."""
    if not isinstance(tool, Mapping):
        return None

    direct_candidates = [
        "system_prompt_instructions",
        "system_prompt_append",
        "hidden_instructions",
        "hidden_prompt",
        "llm_instructions",
        "prompt_instructions",
        "instructions",
    ]

    for key in direct_candidates:
        value = tool.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    config = tool.get("config")
    if isinstance(config, Mapping):
        for key in direct_candidates:
            value = config.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    metadata = tool.get("metadata")
    if isinstance(metadata, Mapping):
        for key in direct_candidates:
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return None


def _build_section(tool: Mapping[str, Any], instructions: str) -> InstructionSection:
    slug = tool.get("slug") or tool.get("id")
    name = tool.get("name") or slug or "Ability"
    section: InstructionSection = {
        "slug": str(slug) if slug else None,
        "name": str(name) if name else "Ability",
        "instructions": instructions,
    }
    return section


def build_tool_prompt_sections(tools: Optional[Sequence[Mapping[str, Any]]]) -> List[InstructionSection]:
    sections: List[InstructionSection] = []
    if not tools:
        return sections

    seen_slugs: set[str] = set()
    seen_instruction_hashes: set[str] = set()

    for tool in tools:
        if not isinstance(tool, Mapping):
            continue
        if tool.get("enabled") is False:
            continue

        instructions = _extract_instructions(tool)
        if not instructions:
            continue

        normalized = instructions.strip()
        if not normalized:
            continue

        slug = tool.get("slug")
        if isinstance(slug, str) and slug:
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)

        hash_key = normalized.lower().strip()
        if hash_key in seen_instruction_hashes:
            continue
        seen_instruction_hashes.add(hash_key)

        sections.append(_build_section(tool, normalized))

    return sections


def apply_tool_prompt_instructions(
    base_prompt: Optional[str],
    tools: Optional[Sequence[Mapping[str, Any]]],
) -> Tuple[str, List[InstructionSection]]:
    """Return the system prompt with hidden tool instructions appended."""
    original = base_prompt or ""
    sections = build_tool_prompt_sections(tools)
    if not sections:
        return original, []

    existing = original
    applied_sections: List[InstructionSection] = []
    applied_snippets: List[str] = []

    for section in sections:
        snippet = section["instructions"] or ""
        if not snippet:
            continue
        if snippet in existing:
            continue
        applied_sections.append(section)
        applied_snippets.append(snippet)

    if not applied_sections:
        return original, []

    lines: List[str] = [original.rstrip(), "---", "# Ability Instructions"]

    for section in applied_sections:
        name = section.get("name") or "Ability"
        slug = section.get("slug")
        heading = f"## Ability: {name}".strip()
        lines.append(heading)
        if slug:
            lines.append(f"*Function slug:* `{slug}`")
        lines.append(section["instructions"] or "")

    combined = "\n\n".join(filter(None, lines)).rstrip() + "\n"
    return combined, applied_sections


__all__ = [
    "InstructionSection",
    "apply_tool_prompt_instructions",
    "build_tool_prompt_sections",
]
