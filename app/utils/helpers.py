import secrets
import hashlib
import re
import unicodedata
from typing import Optional
import os

def generate_api_key(prefix: str = "sk_live") -> str:
    """
    Generate a secure API key
    
    Args:
        prefix: Prefix for the API key (default: sk_live)
    
    Returns:
        API key string
    """
    token = secrets.token_urlsafe(32)
    return f"{prefix}_{token}"

def hash_api_key(api_key: str) -> str:
    """
    Hash an API key using SHA256
    
    Args:
        api_key: The API key to hash
    
    Returns:
        Hashed API key
    """
    return hashlib.sha256(api_key.encode()).hexdigest()

def generate_slug(text: str) -> str:
    """
    Generate a URL-friendly slug from text
    
    Args:
        text: Text to convert to slug
    
    Returns:
        URL-friendly slug
    """
    # Normalize unicode characters
    text = unicodedata.normalize('NFKD', text)
    text = text.encode('ascii', 'ignore').decode('ascii')
    
    # Convert to lowercase
    text = text.lower()
    
    # Replace spaces and special characters with hyphens
    text = re.sub(r'[^a-z0-9]+', '-', text)
    
    # Remove leading/trailing hyphens
    text = text.strip('-')
    
    # Replace multiple hyphens with single hyphen
    text = re.sub(r'-+', '-', text)
    
    return text

def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename to prevent directory traversal and other issues
    
    Args:
        filename: Original filename
    
    Returns:
        Sanitized filename
    """
    # Remove path components
    filename = os.path.basename(filename)
    
    # Remove special characters
    filename = re.sub(r'[^\w\s.-]', '', filename)
    
    # Replace spaces with underscores
    filename = filename.replace(' ', '_')
    
    # Limit length
    name, ext = os.path.splitext(filename)
    if len(name) > 100:
        name = name[:100]
    
    return f"{name}{ext}"

def calculate_file_hash(file_content: bytes, algorithm: str = "sha256") -> str:
    """
    Calculate hash of file content
    
    Args:
        file_content: File content as bytes
        algorithm: Hash algorithm to use (default: sha256)
    
    Returns:
        Hex digest of the hash
    """
    hash_func = getattr(hashlib, algorithm)()
    hash_func.update(file_content)
    return hash_func.hexdigest()

def format_file_size(size_bytes: int) -> str:
    """
    Format file size in human-readable format
    
    Args:
        size_bytes: Size in bytes
    
    Returns:
        Human-readable size string
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"

def validate_email(email: str) -> bool:
    """
    Basic email validation
    
    Args:
        email: Email address to validate
    
    Returns:
        True if valid, False otherwise
    """
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))

def validate_domain(domain: str) -> bool:
    """
    Basic domain validation
    
    Args:
        domain: Domain to validate
    
    Returns:
        True if valid, False otherwise
    """
    # Remove protocol if present
    domain = re.sub(r'https?://', '', domain)
    
    # Remove path if present
    domain = domain.split('/')[0]
    
    # Basic domain pattern
    pattern = r'^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$'
    return bool(re.match(pattern, domain))

def generate_room_name() -> str:
    """
    Generate a unique room name for LiveKit
    
    Returns:
        Room name string
    """
    return f"room_{secrets.token_urlsafe(16)}"

def generate_session_id() -> str:
    """
    Generate a unique session ID
    
    Returns:
        Session ID string
    """
    return f"session_{secrets.token_urlsafe(16)}"

def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncate text to specified length
    
    Args:
        text: Text to truncate
        max_length: Maximum length
        suffix: Suffix to add if truncated
    
    Returns:
        Truncated text
    """
    if len(text) <= max_length:
        return text
    
    return text[:max_length - len(suffix)] + suffix