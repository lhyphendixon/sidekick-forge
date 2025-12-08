"""Shared constants for platform-wide limits and defaults."""

import os


def _get_int_from_env(env_var: str, default: str) -> int:
    """Read an integer environment variable with a safe fallback."""
    value = os.getenv(env_var, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


# Maximum upload size for knowledge base documents (in megabytes)
_default_upload_mb = os.getenv("DOCUMENT_MAX_UPLOAD_MB", "100")
DOCUMENT_MAX_UPLOAD_MB = _get_int_from_env("DOCUMENT_PROCESSOR_MAX_FILE_MB", _default_upload_mb)
DOCUMENT_MAX_UPLOAD_BYTES = DOCUMENT_MAX_UPLOAD_MB * 1024 * 1024

# Allowed document extensions for knowledge base uploads
KNOWLEDGE_BASE_ALLOWED_EXTENSIONS = ["pdf", "doc", "docx", "txt", "md", "srt"]
