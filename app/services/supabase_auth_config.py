"""
Supabase Auth Configuration Service

Automatically configures Supabase Auth redirect URLs based on the DOMAIN_NAME
environment variable. This ensures email confirmation links work correctly
in both staging and production environments.
"""
import os
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)


def configure_supabase_auth_urls(domain_name: Optional[str] = None) -> bool:
    """
    Configure Supabase Auth redirect URLs for the current environment.

    This should be called on application startup to ensure email confirmation
    links point to the correct domain.

    Args:
        domain_name: The domain to configure. If not provided, reads from DOMAIN_NAME env var.

    Returns:
        True if configuration was successful, False otherwise.
    """
    # Get configuration from environment
    domain = domain_name or os.environ.get('DOMAIN_NAME')
    supabase_url = os.environ.get('SUPABASE_URL', '')
    access_token = os.environ.get('SUPABASE_ACCESS_TOKEN', '')

    if not domain:
        logger.warning("DOMAIN_NAME not set - skipping Supabase auth URL configuration")
        return False

    if not supabase_url:
        logger.warning("SUPABASE_URL not set - skipping Supabase auth URL configuration")
        return False

    if not access_token:
        logger.warning("SUPABASE_ACCESS_TOKEN not set - skipping Supabase auth URL configuration")
        return False

    # Extract project ref from Supabase URL
    try:
        project_ref = supabase_url.split('https://')[1].split('.supabase.co')[0]
    except (IndexError, AttributeError):
        logger.error(f"Could not extract project ref from SUPABASE_URL: {supabase_url}")
        return False

    # Build the site URL and redirect URIs
    site_url = f"https://{domain}"
    redirect_uris = [
        f"https://{domain}",
        f"https://{domain}/**",
        f"https://{domain}/auth/callback",
        f"https://{domain}/admin/login",
        f"https://{domain}/admin/**",
    ]

    logger.info(f"Configuring Supabase auth URLs for domain: {domain}")
    logger.info(f"Project ref: {project_ref}")

    # Update auth config via Management API
    url = f"https://api.supabase.com/v1/projects/{project_ref}/config/auth"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "site_url": site_url,
        "uri_allow_list": ",".join(redirect_uris),
        # Set email template redirect paths to use /auth/callback
        "mailer_urlpaths_confirmation": "/auth/callback",
        "mailer_urlpaths_email_change": "/auth/callback",
        "mailer_urlpaths_recovery": "/auth/callback",
        "mailer_urlpaths_invite": "/auth/callback",
    }

    try:
        response = requests.patch(url, headers=headers, json=payload, timeout=30)

        if response.status_code == 200:
            logger.info(f"Successfully configured Supabase auth URLs for {domain}")
            logger.info(f"  site_url: {site_url}")
            logger.info(f"  uri_allow_list: {', '.join(redirect_uris)}")
            return True
        else:
            # Try to parse error message
            try:
                error_data = response.json()
                error_msg = error_data.get('message', response.text[:200])
            except Exception:
                error_msg = response.text[:200]

            logger.error(f"Failed to configure Supabase auth URLs: {response.status_code} - {error_msg}")
            return False

    except requests.exceptions.RequestException as e:
        logger.error(f"Network error configuring Supabase auth URLs: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error configuring Supabase auth URLs: {e}")
        return False


def verify_supabase_auth_config(domain_name: Optional[str] = None) -> dict:
    """
    Verify the current Supabase auth configuration.

    Returns:
        Dict with current config or error information.
    """
    domain = domain_name or os.environ.get('DOMAIN_NAME', '')
    supabase_url = os.environ.get('SUPABASE_URL', '')
    access_token = os.environ.get('SUPABASE_ACCESS_TOKEN', '')

    if not all([supabase_url, access_token]):
        return {"error": "Missing SUPABASE_URL or SUPABASE_ACCESS_TOKEN"}

    try:
        project_ref = supabase_url.split('https://')[1].split('.supabase.co')[0]
    except (IndexError, AttributeError):
        return {"error": f"Could not extract project ref from: {supabase_url}"}

    url = f"https://api.supabase.com/v1/projects/{project_ref}/config/auth"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code == 200:
            config = response.json()
            current_site_url = config.get('site_url', '')
            current_allow_list = config.get('uri_allow_list', '')

            expected_site_url = f"https://{domain}" if domain else None
            is_correct = expected_site_url and current_site_url == expected_site_url

            return {
                "project_ref": project_ref,
                "site_url": current_site_url,
                "uri_allow_list": current_allow_list,
                "expected_site_url": expected_site_url,
                "is_correctly_configured": is_correct,
            }
        else:
            return {"error": f"API returned {response.status_code}"}

    except Exception as e:
        return {"error": str(e)}
