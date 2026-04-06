"""Utilities for embedding tool instructions into agent system prompts."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


InstructionSection = Dict[str, Optional[str]]


def _generate_default_instructions(tool: Mapping[str, Any]) -> str:
    """Generate basic instructions for a tool that doesn't have explicit instructions.

    This ensures every tool assigned to an agent is discoverable by the LLM,
    even if the tool creator didn't add system_prompt_instructions.
    """
    name = tool.get("name") or tool.get("slug") or "this tool"
    slug = tool.get("slug") or ""
    description = tool.get("description") or ""
    tool_type = tool.get("type") or "tool"

    # Type-specific trigger hints
    type_hints = {
        "n8n": "When the user's request matches this tool's purpose, call it immediately to trigger the workflow.",
        "lingua": "When the user wants to transcribe audio, create subtitles, or translate transcripts, call this tool.",
        "image_catalyst": "When the user wants to generate, create, or design images, call this tool.",
        "content_catalyst": "When the user wants to write articles, blog posts, or content, call this tool.",
        "prediction_market": "When the user asks about probabilities of future events, call this tool.",
        "print_ready": "When the user wants to print or export a conversation, call this tool.",
        "documentsense": "When the user wants to analyze, summarize, or extract information from documents, call this tool.",
        "asana": "When the user wants to manage tasks, create tasks, or check task status, call this tool.",
        "descript": "When the user wants to edit a video, remove filler words, remove silences, enhance audio, create highlight clips, or apply any video edits, call this tool.",
        "builtin": "When the user's request relates to this tool's functionality, it will be invoked automatically.",
    }

    # Build the instruction based on available metadata
    lines = []
    lines.append(f"You have access to the {name} tool.")

    if description:
        lines.append(f"\nDescription: {description}")

    # Add type-specific hint or generic one
    hint = type_hints.get(tool_type)
    if hint:
        lines.append(f"\n{hint}")
    else:
        lines.append(f"\nWhen the user's request relates to {name.lower()} functionality, call this tool immediately.")

    lines.append("Do not ask clarifying questions - invoke the tool and let the UI collect any needed details from the user.")

    if slug:
        lines.append(f"\nTo use this tool, call the `{slug}` function.")

    return "\n".join(lines)


def _extract_instructions(tool: Mapping[str, Any], auto_generate: bool = True) -> Optional[str]:
    """Return the best-effort instruction string for the given tool.

    If auto_generate is True and no explicit instructions are found,
    generate basic instructions from the tool's metadata.
    """
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

    # No explicit instructions found - auto-generate if enabled
    if auto_generate:
        return _generate_default_instructions(tool)

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
