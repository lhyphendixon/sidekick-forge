"""
Mailchimp Audience Service

Manages subscriber additions to the Sidekick Forge Mailchimp audience.
Used for:
- Adding new customers on checkout completion
- Newsletter signups from the homepage email capture
"""
import hashlib
import logging
from typing import Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

try:
    import mailchimp_marketing as MailchimpMarketing
    from mailchimp_marketing.api_client import ApiClientError
except ImportError:
    MailchimpMarketing = None
    ApiClientError = Exception
    logger.warning("mailchimp-marketing not installed — Mailchimp integration disabled")


class MailchimpService:
    def __init__(self) -> None:
        self._client = None

        if not MailchimpMarketing:
            return

        api_key = settings.mailchimp_api_key
        list_id = settings.mailchimp_list_id

        if not api_key or not list_id:
            logger.info("Mailchimp not configured (missing API key or list ID)")
            return

        # Extract data center from API key (e.g. "abc123-us21" → "us21")
        try:
            server = api_key.split("-")[-1]
        except (IndexError, AttributeError):
            logger.error("Invalid Mailchimp API key format — expected 'key-datacenter'")
            return

        self._client = MailchimpMarketing.Client()
        self._client.set_config({"api_key": api_key, "server": server})
        self._list_id = list_id
        logger.info(f"Mailchimp service initialized (server: {server})")

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    def subscribe(
        self,
        email: str,
        first_name: str = "",
        last_name: str = "",
        tags: Optional[List[str]] = None,
        status: str = "subscribed",
    ) -> bool:
        """
        Add or update a subscriber in the Mailchimp audience.

        Args:
            email: Subscriber email address.
            first_name: First name.
            last_name: Last name.
            tags: List of tags to apply (e.g. ["champion", "checkout"]).
            status: "subscribed" (immediate) or "pending" (double opt-in).

        Returns:
            True if successful, False otherwise.
        """
        if not self.is_configured:
            logger.debug("Mailchimp not configured — skipping subscribe")
            return False

        email_lower = email.lower().strip()
        subscriber_hash = hashlib.md5(email_lower.encode()).hexdigest()

        merge_fields = {}
        if first_name:
            merge_fields["FNAME"] = first_name
        if last_name:
            merge_fields["LNAME"] = last_name

        member_data = {
            "email_address": email_lower,
            "status_if_new": status,
            "merge_fields": merge_fields,
        }

        try:
            self._client.lists.set_list_member(
                self._list_id,
                subscriber_hash,
                member_data,
            )
            logger.info(f"Mailchimp: subscribed {email_lower}")

            # Apply tags if provided
            if tags:
                tag_body = {"tags": [{"name": t, "status": "active"} for t in tags]}
                try:
                    self._client.lists.update_list_member_tags(
                        self._list_id,
                        subscriber_hash,
                        tag_body,
                    )
                    logger.info(f"Mailchimp: applied tags {tags} to {email_lower}")
                except ApiClientError as tag_err:
                    logger.warning(f"Mailchimp: failed to apply tags to {email_lower}: {tag_err.text}")

            return True

        except ApiClientError as e:
            logger.error(f"Mailchimp: failed to subscribe {email_lower}: {e.text}")
            return False
        except Exception as e:
            logger.error(f"Mailchimp: unexpected error subscribing {email_lower}: {e}")
            return False


# Module-level singleton
mailchimp_service = MailchimpService()
