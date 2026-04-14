"""
Lore Import Service — Multi-step AI extraction pipeline.

Ingests ChatGPT and Claude conversation exports, extracts personal context
signals via chunked parallel LLM calls, and proposes Lore updates.

Pipeline steps:
  1. Parse  — Extract user messages from ZIP (ChatGPT JSON / Claude JSONL)
  2. Chunk  — Split into LLM-sized batches (~3000 tokens each)
  3. Extract — Parallel LLM calls to pull signals from each chunk
  4. Consolidate — Merge extracted signals into per-category drafts
  5. Merge  — Combine drafts with existing Lore (no duplicates)
"""

import asyncio
import io
import json
import logging
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

LORE_MCP_BASE = os.getenv("LORE_MCP_URL", "http://lore-mcp:8082")

LORE_CATEGORIES = [
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

# Max tokens per chunk (approximate — ~4 chars per token)
CHUNK_CHAR_LIMIT = 12000
# Max concurrent extraction calls
MAX_CONCURRENT_EXTRACTIONS = 5


# ---------------------------------------------------------------------------
# Step 1: Parse exports
# ---------------------------------------------------------------------------

def parse_chatgpt_export(zip_bytes: bytes) -> List[str]:
    """Extract user messages from a ChatGPT data export ZIP."""
    user_messages = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if name.endswith("conversations.json"):
                raw = zf.read(name)
                conversations = json.loads(raw)
                for conv in conversations:
                    mapping = conv.get("mapping", {})
                    for node in mapping.values():
                        msg = node.get("message")
                        if not msg:
                            continue
                        author = msg.get("author", {})
                        if author.get("role") != "user":
                            continue
                        content = msg.get("content", {})
                        parts = content.get("parts", [])
                        for part in parts:
                            if isinstance(part, str) and part.strip():
                                user_messages.append(part.strip())
    return user_messages


def parse_claude_export(zip_bytes: bytes) -> List[str]:
    """Extract user messages from a Claude data export ZIP."""
    user_messages = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if name.endswith(".jsonl") or name.endswith("conversations.json"):
                raw = zf.read(name).decode("utf-8", errors="replace")
                for line in raw.strip().splitlines():
                    if not line.strip():
                        continue
                    try:
                        conv = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    messages = conv.get("chat_messages", [])
                    for msg in messages:
                        if msg.get("role") != "human" and msg.get("role") != "user":
                            continue
                        content_blocks = msg.get("content", [])
                        if isinstance(content_blocks, str):
                            if content_blocks.strip():
                                user_messages.append(content_blocks.strip())
                            continue
                        for block in content_blocks:
                            if isinstance(block, str):
                                if block.strip():
                                    user_messages.append(block.strip())
                            elif isinstance(block, dict):
                                text = block.get("text", "")
                                if text.strip():
                                    user_messages.append(text.strip())
    return user_messages


def detect_and_parse(zip_bytes: bytes) -> Tuple[str, List[str]]:
    """Auto-detect export format and parse user messages."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()

    logger.info(f"ZIP contains {len(names)} files. Sample: {names[:20]}")
    json_files = [n for n in names if n.endswith(".json")]
    jsonl_files = [n for n in names if n.endswith(".jsonl")]
    logger.info(f"JSON files: {json_files[:10]}, JSONL files: {jsonl_files[:10]}")

    # Try ChatGPT first (conversations.json with mapping structure)
    chatgpt_msgs = parse_chatgpt_export(zip_bytes)
    if chatgpt_msgs:
        return "chatgpt", chatgpt_msgs

    # Try Claude (JSONL or JSON with chat_messages)
    claude_msgs = parse_claude_export(zip_bytes)
    if claude_msgs:
        return "claude", claude_msgs

    # Fallback: try parsing ALL .json files as potential conversation arrays
    fallback_msgs = _parse_json_fallback(zip_bytes)
    if fallback_msgs:
        return "chatgpt", fallback_msgs

    return "unknown", []


def _parse_json_fallback(zip_bytes: bytes) -> List[str]:
    """Try to extract user messages from any JSON file that looks like conversations."""
    user_messages = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if not name.endswith(".json"):
                continue
            try:
                raw = zf.read(name)
                data = json.loads(raw)
            except (json.JSONDecodeError, Exception):
                continue

            # Could be a list of conversations
            if isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    # ChatGPT style: has "mapping" key
                    if "mapping" in item:
                        for node in item["mapping"].values():
                            msg = node.get("message") if isinstance(node, dict) else None
                            if not msg:
                                continue
                            author = msg.get("author", {})
                            if author.get("role") != "user":
                                continue
                            parts = msg.get("content", {}).get("parts", [])
                            for part in parts:
                                if isinstance(part, str) and part.strip():
                                    user_messages.append(part.strip())
                    # Simple message list style: has "role" and "content"
                    elif "role" in item and item.get("role") in ("user", "human"):
                        content = item.get("content", "")
                        if isinstance(content, str) and content.strip():
                            user_messages.append(content.strip())
                        elif isinstance(content, list):
                            for block in content:
                                text = block.get("text", "") if isinstance(block, dict) else (block if isinstance(block, str) else "")
                                if text.strip():
                                    user_messages.append(text.strip())

            # Could be a single conversation object
            elif isinstance(data, dict):
                if "mapping" in data:
                    for node in data["mapping"].values():
                        msg = node.get("message") if isinstance(node, dict) else None
                        if not msg:
                            continue
                        author = msg.get("author", {})
                        if author.get("role") != "user":
                            continue
                        parts = msg.get("content", {}).get("parts", [])
                        for part in parts:
                            if isinstance(part, str) and part.strip():
                                user_messages.append(part.strip())

    if user_messages:
        logger.info(f"Fallback parser found {len(user_messages)} messages across JSON files")
    return user_messages


def transcribe_audio_files(zip_bytes: bytes, api_key: str) -> List[str]:
    """
    Extract and transcribe audio files from the ZIP using OpenAI Whisper API.
    Returns list of transcribed text strings.
    """
    audio_extensions = {".mp3", ".wav", ".m4a", ".ogg", ".webm", ".mp4", ".flac"}
    transcripts = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        audio_files = [
            n for n in zf.namelist()
            if Path(n).suffix.lower() in audio_extensions
        ]
        if not audio_files:
            return []

        logger.info(f"Found {len(audio_files)} audio files to transcribe")

        for audio_name in audio_files:
            audio_data = zf.read(audio_name)
            suffix = Path(audio_name).suffix.lower()
            try:
                import httpx as _httpx
                # Synchronous call for simplicity — called from async via to_thread
                with _httpx.Client(timeout=120.0) as client:
                    resp = client.post(
                        "https://api.openai.com/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        files={"file": (Path(audio_name).name, audio_data, f"audio/{suffix.lstrip('.')}")},
                        data={"model": "whisper-1"},
                    )
                    if resp.status_code == 200:
                        text = resp.json().get("text", "").strip()
                        if text:
                            transcripts.append(text)
                            logger.info(f"Transcribed {audio_name}: {len(text)} chars")
                    else:
                        logger.warning(f"Whisper API error for {audio_name}: {resp.status_code}")
            except Exception as exc:
                logger.warning(f"Failed to transcribe {audio_name}: {exc}")

    return transcripts


# ---------------------------------------------------------------------------
# Step 2: Chunk messages
# ---------------------------------------------------------------------------

def chunk_messages(messages: List[str], char_limit: int = CHUNK_CHAR_LIMIT) -> List[str]:
    """Group user messages into chunks that fit within the LLM context limit."""
    chunks = []
    current_chunk: List[str] = []
    current_len = 0

    for msg in messages:
        msg_len = len(msg)
        if msg_len > char_limit:
            # Single message exceeds limit — split it
            if current_chunk:
                chunks.append("\n\n---\n\n".join(current_chunk))
                current_chunk = []
                current_len = 0
            # Truncate oversized messages
            chunks.append(msg[:char_limit])
            continue

        if current_len + msg_len + 5 > char_limit:
            chunks.append("\n\n---\n\n".join(current_chunk))
            current_chunk = []
            current_len = 0

        current_chunk.append(msg)
        current_len += msg_len + 5  # Account for separator

    if current_chunk:
        chunks.append("\n\n---\n\n".join(current_chunk))

    return chunks


# ---------------------------------------------------------------------------
# Step 3: Extract signals (parallel LLM calls)
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """You are an AI that extracts personal context signals from a user's conversation history.

Analyze the user messages below and extract any signals about the person who wrote them. Return a JSON object with these category keys (only include categories where you find clear signals):

- identity: Name, role, organization, philosophy, personal context
- roles_and_responsibilities: Day-to-day work, outputs, decisions, who they serve
- current_projects: Active workstreams, status, priorities
- team_and_relationships: Key people, roles, relationship dynamics
- tools_and_systems: Tech stack, tools mentioned, architecture patterns
- communication_style: Tone patterns, formatting preferences, how they communicate
- goals_and_priorities: Near-term and long-term goals, what they optimize for
- preferences_and_constraints: Always/never rules, stated preferences, hard constraints
- domain_knowledge: Expertise areas, frameworks they know, concepts they don't need explained
- decision_log: Decisions mentioned with reasoning

For each category, provide an array of concise signal strings. Only include signals that are clearly supported by the text — do not infer or guess.

Return ONLY valid JSON, no markdown fences."""

EXTRACTION_USER_TEMPLATE = """Here are user messages from a conversation export. Extract personal context signals.

USER MESSAGES:
{chunk}"""


async def extract_signals_from_chunk(
    chunk: str,
    llm_provider: Any,
    chunk_index: int,
    total_chunks: int,
) -> Dict[str, List[str]]:
    """Extract Lore signals from a single chunk via LLM."""
    for attempt in range(2):
        try:
            response = await llm_provider.chat(
                messages=[
                    {"role": "user", "content": EXTRACTION_SYSTEM_PROMPT + "\n\n" + EXTRACTION_USER_TEMPLATE.format(chunk=chunk)},
                ],
                max_tokens=4000,
            )
            text = (response or "").strip()
            if not text:
                if attempt == 0:
                    logger.warning(f"Chunk {chunk_index + 1}/{total_chunks}: empty response, retrying")
                    continue
                logger.warning(f"Chunk {chunk_index + 1}/{total_chunks}: empty response after retry")
                return {}

            # Strip markdown fences if present
            if text.startswith("```"):
                text = re.sub(r"^```\w*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
            signals = json.loads(text)
            logger.info(f"Chunk {chunk_index + 1}/{total_chunks}: extracted {sum(len(v) for v in signals.values())} signals")
            return signals
        except json.JSONDecodeError as exc:
            if attempt == 0:
                logger.warning(f"Chunk {chunk_index + 1}/{total_chunks}: JSON parse failed, retrying")
                continue
            logger.warning(f"Chunk {chunk_index + 1}/{total_chunks} extraction failed: {exc}")
            return {}
        except Exception as exc:
            logger.warning(f"Chunk {chunk_index + 1}/{total_chunks} extraction failed: {exc}")
            return {}
    return {}


async def extract_signals_parallel(
    chunks: List[str],
    llm_provider: Any,
) -> List[Dict[str, List[str]]]:
    """Run extraction across all chunks with bounded concurrency."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_EXTRACTIONS)
    total = len(chunks)

    async def bounded_extract(chunk: str, idx: int):
        async with semaphore:
            return await extract_signals_from_chunk(chunk, llm_provider, idx, total)

    tasks = [bounded_extract(chunk, i) for i, chunk in enumerate(chunks)]
    return await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# Step 4: Consolidate signals
# ---------------------------------------------------------------------------

def merge_signal_lists(all_signals: List[Dict[str, List[str]]]) -> Dict[str, List[str]]:
    """Merge signal lists from multiple chunks, deduplicating."""
    merged: Dict[str, List[str]] = {}
    for signals in all_signals:
        for category, items in signals.items():
            if category not in LORE_CATEGORIES:
                continue
            if category not in merged:
                merged[category] = []
            for item in items:
                item_lower = item.strip().lower()
                if not any(existing.strip().lower() == item_lower for existing in merged[category]):
                    merged[category].append(item.strip())
    return merged


CONSOLIDATION_SYSTEM_PROMPT = """You are writing a clean, structured Lore profile section for a user.

Given a list of raw extracted signals for the "{category}" category, produce a clean markdown document.

Description of this category: {description}

Rules:
- Use markdown formatting (headers, bullets, tables as appropriate)
- Deduplicate: merge similar signals into single entries
- Be concise but retain all meaningful information
- Start with a level-1 heading matching the category name
- Do NOT add information beyond what the signals support

Return ONLY the markdown content, no fences or explanation."""

CATEGORY_DESCRIPTIONS = {
    "identity": "Name, role, org, philosophy, personal context",
    "roles_and_responsibilities": "Day-to-day work, outputs, decisions, who you serve",
    "current_projects": "Active workstreams, status, priority, KPIs, definition of done",
    "team_and_relationships": "Key people, roles, what each relationship requires",
    "tools_and_systems": "Stack, architecture patterns, constraints, design systems",
    "communication_style": "Tone, formatting, editing preferences, voice matching notes",
    "goals_and_priorities": "Week / quarter / year / career optimization targets",
    "preferences_and_constraints": "Always/never rules, tool preferences, hard constraints",
    "domain_knowledge": "Expertise areas, frameworks used, what NOT to explain",
    "decision_log": "Past decisions and reasoning — how you think",
}


async def consolidate_category(
    category: str,
    signals: List[str],
    llm_provider: Any,
) -> str:
    """Convert raw signals into a clean markdown Lore section."""
    description = CATEGORY_DESCRIPTIONS.get(category, "")
    signals_text = "\n".join(f"- {s}" for s in signals)

    for attempt in range(2):
        response = await llm_provider.chat(
            messages=[
                {"role": "user", "content": CONSOLIDATION_SYSTEM_PROMPT.format(
                    category=category.replace("_", " ").title(),
                    description=description,
                ) + f"\n\nRaw signals:\n{signals_text}"},
            ],
            max_tokens=3000,
        )
        text = (response or "").strip()
        if text:
            return text
        logger.warning(f"Consolidation for '{category}' returned empty (attempt {attempt + 1}), retrying")
    return ""


# ---------------------------------------------------------------------------
# Step 5: Merge with existing Lore
# ---------------------------------------------------------------------------

MERGE_SYSTEM_PROMPT = """You are merging new insights into an existing Lore profile section.

Category: {category}
Description: {description}

EXISTING CONTENT:
{existing}

NEW INSIGHTS:
{new_content}

Rules:
- Preserve ALL existing content that is still valid
- Add new information that doesn't duplicate what's already there
- If new information contradicts existing, prefer the new (it's more recent)
- Maintain the existing formatting style and structure
- Start with a level-1 heading matching the category name
- Be concise, no filler

Return ONLY the merged markdown content."""


def _mcp_params(user_id: str, target_url: Optional[str], target_key: Optional[str]) -> Dict[str, str]:
    params = {"user_id": user_id}
    if target_url and target_key:
        params["target_url"] = target_url
        params["target_key"] = target_key
    return params


def _mcp_headers() -> Dict[str, str]:
    """Internal auth header for the Lore MCP admin API."""
    return {"X-Lore-Internal": os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")}


async def merge_with_existing(
    category: str,
    new_content: str,
    llm_provider: Any,
    user_id: str,
    target_url: Optional[str] = None,
    target_key: Optional[str] = None,
) -> Tuple[str, bool]:
    """Merge new content with existing Lore for a category.
    Returns (merged_content, has_existing).
    """
    existing = ""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{LORE_MCP_BASE}/admin-api/category/{category}",
                params=_mcp_params(user_id, target_url, target_key),
                headers=_mcp_headers(),
            )
            if resp.status_code == 200:
                existing = resp.json().get("content", "").strip()
    except Exception:
        pass

    # Check if existing is just a template (no real content)
    has_real_content = False
    if existing:
        lines = [l for l in existing.splitlines() if l.strip() and not l.strip().startswith("#")]
        real_lines = [
            l for l in lines
            if not re.match(r"^[\-\|:\s]+$", l.strip())
            and not re.match(r"^-\s+\*\*[^*]+\*\*:\s*$", l.strip())
            and not re.match(r"^\|(\s*\|)+\s*$", l.strip())
        ]
        has_real_content = len(real_lines) > 0

    if not has_real_content:
        return new_content, False

    # Merge via LLM
    for attempt in range(2):
        response = await llm_provider.chat(
            messages=[
                {"role": "user", "content": MERGE_SYSTEM_PROMPT.format(
                    category=category.replace("_", " ").title(),
                    description=CATEGORY_DESCRIPTIONS.get(category, ""),
                    existing=existing,
                    new_content=new_content,
                )},
            ],
            max_tokens=3000,
        )
        text = (response or "").strip()
        if text:
            return text, True
        logger.warning(f"Merge for '{category}' returned empty (attempt {attempt + 1}), retrying")
    return new_content, True


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def run_text_pipeline(
    texts: List[str],
    source_label: str,
    llm_provider: Any,
    progress_callback=None,
    user_id: str = "",
    target_url: Optional[str] = None,
    target_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Pipeline for raw text or transcribed audio. Same extraction/consolidation/merge
    steps as the ZIP pipeline but skips parsing — texts are provided directly.
    """
    if not texts:
        raise ValueError("No text content provided.")

    messages = [t.strip() for t in texts if t.strip()]
    if not messages:
        raise ValueError("No meaningful text content found.")

    logger.info(f"Text pipeline: {len(messages)} text blocks from {source_label}")

    if progress_callback:
        await progress_callback("chunking", f"Splitting {len(messages)} text blocks into chunks...")

    chunks = chunk_messages(messages)
    logger.info(f"Created {len(chunks)} chunks for extraction")

    if progress_callback:
        await progress_callback("extracting", f"Extracting signals from {len(chunks)} chunks...")

    all_signals = await extract_signals_parallel(chunks, llm_provider)

    if progress_callback:
        await progress_callback("consolidating", "Consolidating extracted signals...")

    merged_signals = merge_signal_lists(all_signals)
    total_signals = sum(len(v) for v in merged_signals.values())
    logger.info(f"Consolidated {total_signals} unique signals across {len(merged_signals)} categories")

    if not merged_signals:
        raise ValueError("No meaningful signals could be extracted from the provided text.")

    consolidation_tasks = []
    for category, signals in merged_signals.items():
        if signals:
            consolidation_tasks.append(consolidate_category(category, signals, llm_provider))
    consolidated = await asyncio.gather(*consolidation_tasks)

    category_drafts = {}
    for (category, signals), draft in zip(
        [(c, s) for c, s in merged_signals.items() if s], consolidated,
    ):
        category_drafts[category] = (draft, len(signals))

    if progress_callback:
        await progress_callback("merging", "Merging with existing Lore...")

    proposals = {}
    merge_tasks = []
    merge_categories = []
    for category, (draft, signal_count) in category_drafts.items():
        merge_tasks.append(
            merge_with_existing(category, draft, llm_provider, user_id, target_url, target_key)
        )
        merge_categories.append((category, signal_count))

    merge_results = await asyncio.gather(*merge_tasks)

    for (category, signal_count), (merged_content, has_existing) in zip(
        merge_categories, merge_results
    ):
        proposals[category] = {
            "content": merged_content,
            "has_existing": has_existing,
            "signal_count": signal_count,
        }

    if progress_callback:
        await progress_callback("complete", "Analysis complete. Review proposals below.")

    return {
        "source": source_label,
        "stats": {
            "messages_parsed": len(messages),
            "chunks": len(chunks),
            "signals_extracted": total_signals,
        },
        "proposals": proposals,
    }


async def run_import_pipeline(
    zip_bytes: bytes,
    llm_provider: Any,
    openai_api_key: Optional[str] = None,
    progress_callback=None,
    user_id: str = "",
    target_url: Optional[str] = None,
    target_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Full import pipeline. Returns proposals per category for user review.

    Returns:
        {
            "source": "chatgpt" | "claude",
            "stats": {"messages_parsed": N, "chunks": N, "signals_extracted": N},
            "proposals": {
                "category_name": {
                    "content": "merged markdown",
                    "has_existing": bool,
                    "signal_count": N,
                },
                ...
            }
        }
    """

    # Step 1: Parse
    if progress_callback:
        await progress_callback("parsing", "Parsing export file...")

    source, messages = detect_and_parse(zip_bytes)
    if not messages and source == "unknown":
        raise ValueError(
            "Could not detect export format. Expected a ChatGPT or Claude data export ZIP."
        )

    # Transcribe audio files if OpenAI key available
    if openai_api_key:
        audio_texts = await asyncio.to_thread(transcribe_audio_files, zip_bytes, openai_api_key)
        if audio_texts:
            messages.extend(audio_texts)
            logger.info(f"Added {len(audio_texts)} audio transcriptions to message pool")

    if not messages:
        raise ValueError("Export parsed but no user messages found.")

    logger.info(f"Parsed {len(messages)} user messages from {source} export")

    # Step 2: Chunk
    if progress_callback:
        await progress_callback("chunking", f"Splitting {len(messages)} messages into chunks...")

    chunks = chunk_messages(messages)
    logger.info(f"Created {len(chunks)} chunks for extraction")

    # Step 3: Extract signals (parallel)
    if progress_callback:
        await progress_callback("extracting", f"Extracting signals from {len(chunks)} chunks...")

    all_signals = await extract_signals_parallel(chunks, llm_provider)

    # Step 4: Consolidate
    if progress_callback:
        await progress_callback("consolidating", "Consolidating extracted signals...")

    merged_signals = merge_signal_lists(all_signals)
    total_signals = sum(len(v) for v in merged_signals.values())
    logger.info(f"Consolidated {total_signals} unique signals across {len(merged_signals)} categories")

    if not merged_signals:
        raise ValueError("No meaningful signals could be extracted from the export.")

    # Consolidate each category into clean markdown
    consolidation_tasks = []
    for category, signals in merged_signals.items():
        if signals:
            consolidation_tasks.append(
                consolidate_category(category, signals, llm_provider)
            )
    consolidated = await asyncio.gather(*consolidation_tasks)

    category_drafts = {}
    for (category, signals), draft in zip(
        [(c, s) for c, s in merged_signals.items() if s],
        consolidated,
    ):
        category_drafts[category] = (draft, len(signals))

    # Step 5: Merge with existing Lore
    if progress_callback:
        await progress_callback("merging", "Merging with existing Lore...")

    proposals = {}
    merge_tasks = []
    merge_categories = []
    for category, (draft, signal_count) in category_drafts.items():
        merge_tasks.append(
            merge_with_existing(category, draft, llm_provider, user_id, target_url, target_key)
        )
        merge_categories.append((category, signal_count))

    merge_results = await asyncio.gather(*merge_tasks)

    for (category, signal_count), (merged_content, has_existing) in zip(
        merge_categories, merge_results
    ):
        proposals[category] = {
            "content": merged_content,
            "has_existing": has_existing,
            "signal_count": signal_count,
        }

    if progress_callback:
        await progress_callback("complete", "Import analysis complete. Review proposals below.")

    return {
        "source": source,
        "stats": {
            "messages_parsed": len(messages),
            "chunks": len(chunks),
            "signals_extracted": total_signals,
        },
        "proposals": proposals,
    }


async def apply_proposals(
    proposals: Dict[str, Dict[str, Any]],
    user_id: str = "",
    target_url: Optional[str] = None,
    target_key: Optional[str] = None,
) -> Dict[str, str]:
    """Write approved proposals to the Lore MCP. Returns status per category."""
    results = {}
    params = _mcp_params(user_id, target_url, target_key)
    async with httpx.AsyncClient(timeout=10.0) as client:
        for category, proposal in proposals.items():
            content = proposal.get("content", "")
            if not content.strip():
                results[category] = "skipped"
                continue
            try:
                resp = await client.put(
                    f"{LORE_MCP_BASE}/admin-api/category/{category}",
                    params=params,
                    headers=_mcp_headers(),
                    json={"content": content},
                )
                results[category] = "saved" if resp.status_code == 200 else f"error: {resp.status_code}"
            except Exception as exc:
                results[category] = f"error: {exc}"
    return results
