"""
Firecrawl Web Scraper Service for Sidekick Forge Knowledge Base.

Handles website scraping using Firecrawl (https://docs.firecrawl.dev).
Supports both self-hosted and cloud API modes:
- Self-hosted: Set FIRECRAWL_URL env var (default: http://firecrawl:3002)
- Cloud API: Set FIRECRAWL_API_KEY for authenticated cloud access

Supports both single URL scraping and multi-page crawling.
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Firecrawl configuration - supports self-hosted or cloud API
# Self-hosted: http://firecrawl:3002 (no API key needed)
# Cloud API: https://api.firecrawl.dev (requires API key)
FIRECRAWL_BASE_URL = os.getenv("FIRECRAWL_URL", "http://firecrawl:3002")
FIRECRAWL_CLOUD_URL = "https://api.firecrawl.dev"

# Build API endpoints from base URL
def _get_firecrawl_endpoints(base_url: str = None) -> dict:
    """Get Firecrawl API endpoints for the given base URL."""
    base = (base_url or FIRECRAWL_BASE_URL).rstrip("/")
    # Ensure /v1 suffix
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return {
        "scrape": f"{base}/scrape",
        "crawl": f"{base}/crawl",
        "crawl_status": f"{base}/crawl",
    }

# Default endpoints (self-hosted)
_endpoints = _get_firecrawl_endpoints()
FIRECRAWL_SCRAPE_URL = _endpoints["scrape"]
FIRECRAWL_CRAWL_URL = _endpoints["crawl"]
FIRECRAWL_CRAWL_STATUS_URL = _endpoints["crawl_status"]

# Default settings
DEFAULT_TIMEOUT = 60  # seconds
DEFAULT_CRAWL_LIMIT = 10  # max pages to crawl
MAX_CRAWL_LIMIT = 100  # absolute max pages


class FirecrawlError(Exception):
    """Exception raised for Firecrawl API errors."""

    def __init__(self, message: str, status_code: int = None, response_data: dict = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data


class FirecrawlScraper:
    """
    Service for scraping websites using Firecrawl (self-hosted or cloud API).

    Supports:
    - Single URL scraping (fast, immediate response)
    - Multi-page crawling (async job with status polling)
    - Self-hosted mode (no API key required)
    - Cloud API mode (requires API key)
    """

    def __init__(self, api_key: str = None, base_url: str = None):
        """
        Initialize the scraper with optional Firecrawl API key and base URL.

        For self-hosted Firecrawl, no API key is needed.
        For cloud API (api.firecrawl.dev), an API key is required.

        Args:
            api_key: Firecrawl API key for authentication (optional for self-hosted)
            base_url: Base URL for Firecrawl API (default: FIRECRAWL_URL env var)
        """
        self.api_key = api_key
        self.base_url = base_url or FIRECRAWL_BASE_URL
        self._client: Optional[httpx.AsyncClient] = None

        # Get endpoints for this instance
        self._endpoints = _get_firecrawl_endpoints(self.base_url)

        # Determine if we're using cloud API (requires auth)
        self._is_cloud = FIRECRAWL_CLOUD_URL in self.base_url
        if self._is_cloud and not api_key:
            raise ValueError("Firecrawl API key is required for cloud API")

        logger.info(f"FirecrawlScraper initialized: base_url={self.base_url}, cloud_mode={self._is_cloud}")

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            headers = {"Content-Type": "application/json"}

            # Only add auth header if API key is provided (cloud mode)
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(DEFAULT_TIMEOUT),
                headers=headers
            )
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def validate_url(url: str) -> str:
        """
        Validate and normalize a URL.

        Args:
            url: URL to validate

        Returns:
            Normalized URL string

        Raises:
            ValueError: If URL is invalid
        """
        if not url:
            raise ValueError("URL is required")

        # Add scheme if missing
        if not url.startswith(('http://', 'https://')):
            url = f"https://{url}"

        parsed = urlparse(url)

        if not parsed.netloc:
            raise ValueError(f"Invalid URL: {url}")

        # Check for valid domain
        domain_pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$'
        if not re.match(domain_pattern, parsed.netloc.split(':')[0]):
            # Allow localhost for testing
            if parsed.netloc.split(':')[0] not in ('localhost', '127.0.0.1'):
                raise ValueError(f"Invalid domain in URL: {url}")

        return url

    async def scrape_url(
        self,
        url: str,
        formats: List[str] = None,
        only_main_content: bool = True,
        include_tags: List[str] = None,
        exclude_tags: List[str] = None,
        wait_for: int = None,
    ) -> Dict[str, Any]:
        """
        Scrape a single URL and return the content.

        This is a synchronous operation - returns immediately with results.

        Args:
            url: URL to scrape
            formats: Output formats - ["markdown", "html", "rawHtml", "links", "screenshot"]
            only_main_content: Extract only main content (removes headers, footers, etc.)
            include_tags: CSS selectors to include
            exclude_tags: CSS selectors to exclude
            wait_for: Milliseconds to wait for page to load (for JS-heavy pages)

        Returns:
            Dict with scraped content:
            {
                "success": True,
                "url": "https://...",
                "markdown": "# Page Title\n...",
                "html": "<html>...</html>",
                "metadata": {
                    "title": "Page Title",
                    "description": "...",
                    "language": "en",
                    "sourceURL": "https://...",
                    ...
                }
            }

        Raises:
            FirecrawlError: If scraping fails
        """
        url = self.validate_url(url)

        # Build request payload
        payload = {
            "url": url,
            "formats": formats or ["markdown"],
            "onlyMainContent": only_main_content,
        }

        if include_tags:
            payload["includeTags"] = include_tags
        if exclude_tags:
            payload["excludeTags"] = exclude_tags
        if wait_for:
            payload["waitFor"] = wait_for

        client = await self._get_client()

        try:
            logger.info(f"Scraping URL: {url} via {self._endpoints['scrape']}")
            response = await client.post(self._endpoints["scrape"], json=payload)

            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    logger.info(f"Successfully scraped: {url}")
                    return {
                        "success": True,
                        "url": url,
                        "markdown": data.get("data", {}).get("markdown", ""),
                        "html": data.get("data", {}).get("html", ""),
                        "metadata": data.get("data", {}).get("metadata", {}),
                        "links": data.get("data", {}).get("links", []),
                    }
                else:
                    error_msg = data.get("error", "Unknown error")
                    raise FirecrawlError(f"Scrape failed: {error_msg}", response.status_code, data)
            else:
                error_data = response.json() if response.content else {}
                error_msg = error_data.get("error", f"HTTP {response.status_code}")
                raise FirecrawlError(f"Scrape request failed: {error_msg}", response.status_code, error_data)

        except httpx.TimeoutException:
            raise FirecrawlError(f"Timeout while scraping {url}", 408)
        except httpx.RequestError as e:
            raise FirecrawlError(f"Network error while scraping {url}: {str(e)}", 0)

    async def crawl_website(
        self,
        url: str,
        limit: int = DEFAULT_CRAWL_LIMIT,
        max_depth: int = None,
        include_paths: List[str] = None,
        exclude_paths: List[str] = None,
        allow_external_links: bool = False,
        formats: List[str] = None,
        only_main_content: bool = True,
    ) -> Dict[str, Any]:
        """
        Start a crawl job for a website (multi-page).

        This is an asynchronous operation - returns a job ID for polling.

        Args:
            url: Starting URL for the crawl
            limit: Maximum number of pages to crawl (default: 10, max: 50)
            max_depth: Maximum depth to crawl from starting URL
            include_paths: Glob patterns for paths to include (e.g., ["/blog/*"])
            exclude_paths: Glob patterns for paths to exclude
            allow_external_links: Whether to follow external links
            formats: Output formats for each page
            only_main_content: Extract only main content

        Returns:
            Dict with job info:
            {
                "success": True,
                "job_id": "crawl-abc123",
                "url": "https://...",
            }

        Raises:
            FirecrawlError: If crawl initiation fails
        """
        url = self.validate_url(url)

        # Enforce limits
        limit = min(limit, MAX_CRAWL_LIMIT)

        # Build request payload
        payload = {
            "url": url,
            "limit": limit,
            "scrapeOptions": {
                "formats": formats or ["markdown"],
                "onlyMainContent": only_main_content,
            }
        }

        if max_depth is not None:
            payload["maxDepth"] = max_depth
        if include_paths:
            payload["includePaths"] = include_paths
        if exclude_paths:
            payload["excludePaths"] = exclude_paths
        if allow_external_links:
            payload["allowExternalLinks"] = allow_external_links

        client = await self._get_client()

        try:
            logger.info(f"Starting crawl for: {url} (limit: {limit}) via {self._endpoints['crawl']}")
            response = await client.post(self._endpoints["crawl"], json=payload)

            if response.status_code in (200, 201):
                data = response.json()
                if data.get("success"):
                    job_id = data.get("id")
                    logger.info(f"Crawl job started: {job_id}")
                    return {
                        "success": True,
                        "job_id": job_id,
                        "url": url,
                    }
                else:
                    error_msg = data.get("error", "Unknown error")
                    raise FirecrawlError(f"Crawl failed to start: {error_msg}", response.status_code, data)
            else:
                error_data = response.json() if response.content else {}
                error_msg = error_data.get("error", f"HTTP {response.status_code}")
                raise FirecrawlError(f"Crawl request failed: {error_msg}", response.status_code, error_data)

        except httpx.TimeoutException:
            raise FirecrawlError(f"Timeout while starting crawl for {url}", 408)
        except httpx.RequestError as e:
            raise FirecrawlError(f"Network error while starting crawl for {url}: {str(e)}", 0)

    async def get_crawl_status(self, job_id: str) -> Dict[str, Any]:
        """
        Check the status of a crawl job.

        Args:
            job_id: The crawl job ID returned from crawl_website()

        Returns:
            Dict with status info:
            {
                "success": True,
                "status": "completed" | "scraping" | "failed",
                "total": 10,
                "completed": 5,
                "credits_used": 5,
                "expires_at": "2024-01-01T00:00:00Z",
                "data": [
                    {
                        "markdown": "...",
                        "metadata": {...},
                    },
                    ...
                ]  # Only present when status is "completed"
            }

        Raises:
            FirecrawlError: If status check fails
        """
        if not job_id:
            raise ValueError("Job ID is required")

        client = await self._get_client()
        status_url = f"{self._endpoints['crawl_status']}/{job_id}"

        try:
            response = await client.get(status_url)

            if response.status_code == 200:
                data = response.json()
                return {
                    "success": True,
                    "status": data.get("status", "unknown"),
                    "total": data.get("total", 0),
                    "completed": data.get("completed", 0),
                    "credits_used": data.get("creditsUsed", 0),
                    "expires_at": data.get("expiresAt"),
                    "data": data.get("data", []),
                    "next": data.get("next"),  # Pagination cursor
                }
            else:
                error_data = response.json() if response.content else {}
                error_msg = error_data.get("error", f"HTTP {response.status_code}")
                raise FirecrawlError(f"Status check failed: {error_msg}", response.status_code, error_data)

        except httpx.TimeoutException:
            raise FirecrawlError(f"Timeout while checking crawl status for {job_id}", 408)
        except httpx.RequestError as e:
            raise FirecrawlError(f"Network error while checking crawl status: {str(e)}", 0)

    async def wait_for_crawl(
        self,
        job_id: str,
        poll_interval: float = 2.0,
        timeout: float = 300.0,
        progress_callback: callable = None,
    ) -> Dict[str, Any]:
        """
        Wait for a crawl job to complete, polling for status.

        Args:
            job_id: The crawl job ID
            poll_interval: Seconds between status checks
            timeout: Maximum seconds to wait
            progress_callback: Optional callback(completed, total) for progress updates

        Returns:
            Final status dict with all crawled pages in "data"

        Raises:
            FirecrawlError: If crawl fails or times out
        """
        start_time = asyncio.get_event_loop().time()

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                raise FirecrawlError(f"Crawl timed out after {timeout}s", 408)

            status = await self.get_crawl_status(job_id)

            if progress_callback:
                try:
                    progress_callback(status.get("completed", 0), status.get("total", 0))
                except Exception as e:
                    logger.warning(f"Progress callback error: {e}")

            if status["status"] == "completed":
                logger.info(f"Crawl {job_id} completed: {status['completed']} pages")
                return status
            elif status["status"] == "failed":
                raise FirecrawlError(f"Crawl job failed: {job_id}", 500, status)
            elif status["status"] in ("scraping", "processing"):
                logger.debug(f"Crawl {job_id} in progress: {status['completed']}/{status['total']}")
                await asyncio.sleep(poll_interval)
            else:
                logger.warning(f"Unknown crawl status: {status['status']}")
                await asyncio.sleep(poll_interval)

    async def scrape_and_extract(
        self,
        url: str,
        crawl: bool = False,
        crawl_limit: int = DEFAULT_CRAWL_LIMIT,
        include_paths: List[str] = None,
        exclude_paths: List[str] = None,
        progress_callback: callable = None,
    ) -> List[Dict[str, Any]]:
        """
        High-level method to scrape URL(s) and return extracted content.

        This is the main entry point for the Knowledge Base integration.

        Args:
            url: URL to scrape
            crawl: If True, crawl multiple pages. If False, scrape single URL.
            crawl_limit: Max pages to crawl (only if crawl=True)
            include_paths: Glob patterns for paths to include (e.g., ["/blog/*"])
            exclude_paths: Glob patterns for paths to exclude (e.g., ["/tag/*", "/author/*"])
            progress_callback: Optional callback(completed, total) for progress

        Returns:
            List of extracted pages:
            [
                {
                    "url": "https://...",
                    "title": "Page Title",
                    "content": "Markdown content...",
                    "metadata": {...},
                },
                ...
            ]

        Raises:
            FirecrawlError: If scraping fails
        """
        results = []

        if crawl:
            # Multi-page crawl
            job_info = await self.crawl_website(
                url,
                limit=crawl_limit,
                include_paths=include_paths,
                exclude_paths=exclude_paths,
            )
            job_id = job_info["job_id"]

            status = await self.wait_for_crawl(
                job_id,
                progress_callback=progress_callback
            )

            for page_data in status.get("data", []):
                metadata = page_data.get("metadata", {})
                results.append({
                    "url": metadata.get("sourceURL", url),
                    "title": metadata.get("title", "Untitled"),
                    "content": page_data.get("markdown", ""),
                    "metadata": metadata,
                })
        else:
            # Single URL scrape
            if progress_callback:
                progress_callback(0, 1)

            scraped = await self.scrape_url(url)
            metadata = scraped.get("metadata", {})

            results.append({
                "url": scraped.get("url", url),
                "title": metadata.get("title", "Untitled"),
                "content": scraped.get("markdown", ""),
                "metadata": metadata,
            })

            if progress_callback:
                progress_callback(1, 1)

        return results


# Factory function for creating scrapers - supports self-hosted or client API keys
async def get_firecrawl_scraper(client_id: str = None, use_self_hosted: bool = True) -> Optional[FirecrawlScraper]:
    """
    Get a FirecrawlScraper instance.

    By default, uses the self-hosted Firecrawl service (no API key needed).
    If use_self_hosted=False, attempts to use client-specific API key for cloud API.

    Args:
        client_id: The client ID to get the API key for (only for cloud API mode)
        use_self_hosted: If True, use self-hosted Firecrawl (default). If False, use cloud API.

    Returns:
        FirecrawlScraper instance or None if cloud mode and no API key configured
    """
    # Self-hosted mode - no API key needed
    if use_self_hosted:
        logger.info(f"Using self-hosted Firecrawl at {FIRECRAWL_BASE_URL}")
        return FirecrawlScraper()

    # Cloud API mode - requires client API key
    if not client_id:
        logger.warning("Client ID required for cloud API mode")
        return None

    from app.integrations.supabase_client import supabase_manager

    try:
        if not supabase_manager.admin_client:
            await supabase_manager.initialize()

        client = supabase_manager.admin_client
        result = client.table("clients").select("firecrawl_api_key").eq("id", client_id).single().execute()

        api_key = result.data.get("firecrawl_api_key") if result.data else None

        if not api_key:
            logger.warning(f"No Firecrawl API key configured for client {client_id}, falling back to self-hosted")
            return FirecrawlScraper()  # Fallback to self-hosted

        return FirecrawlScraper(api_key=api_key, base_url=FIRECRAWL_CLOUD_URL)

    except Exception as e:
        logger.error(f"Failed to get Firecrawl scraper for client {client_id}: {e}")
        # Fallback to self-hosted on error
        return FirecrawlScraper()


def get_self_hosted_scraper() -> FirecrawlScraper:
    """
    Get a FirecrawlScraper instance for the self-hosted service.

    This is a synchronous convenience function for simple use cases.

    Returns:
        FirecrawlScraper configured for self-hosted Firecrawl
    """
    return FirecrawlScraper()
