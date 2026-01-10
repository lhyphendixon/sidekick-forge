from __future__ import annotations

import logging
import re
import time
import hashlib
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Deque

from livekit.agents.llm.tool_context import function_tool as lk_function_tool

from app.integrations.helpscout_client import HelpScoutAPIError, HelpScoutClient


logger = logging.getLogger(__name__)

# Global tool call deduplication tracker
_TOOL_CALL_HISTORY: Dict[str, Deque[Tuple[str, float, Any]]] = {}
_DEDUPE_WINDOW_SECONDS = 10.0
_MAX_HISTORY_SIZE = 50


# Regex patterns
QUOTE_PATTERN = re.compile(r"[\"""''']([^\"""''']+)[\"""''']")
CONVERSATION_ID_PATTERN = re.compile(r"\b(\d{6,})\b")
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")

# Keys to search for user inquiry in metadata
_INQUIRY_CANDIDATE_KEYS = (
    "user_inquiry",
    "userInquiry",
    "latest_user_text",
    "latestUserText",
    "user_text",
    "userText",
    "last_user_message",
    "lastUserMessage",
    "user_message",
    "userMessage",
    "query",
    "question",
    "prompt",
    "text",
    "message",
    "transcript",
    "utterance",
    "final_transcript",
)


def _find_text_candidate(value: Any, *, depth: int = 0, max_depth: int = 3) -> Optional[str]:
    if depth > max_depth or value is None:
        return None

    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None

    if isinstance(value, dict):
        for key in _INQUIRY_CANDIDATE_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, str):
                candidate = candidate.strip()
                if candidate:
                    return candidate
        for nested in value.values():
            candidate = _find_text_candidate(nested, depth=depth + 1, max_depth=max_depth)
            if candidate:
                return candidate
        return None

    if isinstance(value, list):
        for item in value:
            candidate = _find_text_candidate(item, depth=depth + 1, max_depth=max_depth)
            if candidate:
                return candidate
    return None


def _coalesce_user_inquiry(metadata: Optional[Dict[str, Any]], messages: Any) -> Optional[str]:
    candidate = None
    if isinstance(metadata, dict):
        candidate = _find_text_candidate(metadata)
        if candidate:
            return candidate

    if isinstance(messages, list):
        for entry in reversed(messages):
            if not isinstance(entry, dict):
                continue
            role = entry.get("role")
            if role not in {"user", "system"}:
                continue
            content = entry.get("content")
            if isinstance(content, str):
                candidate = content.strip()
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_value = part.get("text")
                        if isinstance(text_value, str) and text_value.strip():
                            candidate = text_value.strip()
                            break
            if candidate:
                return candidate
    return None


def _compute_call_hash(slug: str, user_inquiry: str) -> str:
    """Compute a hash for deduplication purposes."""
    content = f"{slug}:{user_inquiry.lower().strip()}"
    return hashlib.md5(content.encode()).hexdigest()


class HelpScoutAbilityConfigError(ValueError):
    """Raised when the HelpScout ability is misconfigured."""


@dataclass
class MailboxConfig:
    """Configuration for a HelpScout mailbox."""
    id: int
    name: Optional[str] = None
    email: Optional[str] = None


class HelpScoutToolHandler:
    """Handler for HelpScout ticket operations."""

    def __init__(
        self,
        *,
        slug: str,
        description: Optional[str],
        config: Dict[str, Any],
        oauth_service: Any = None,
        client_factory: Optional[Any] = None,
    ) -> None:
        self.slug = slug
        self.description = description or "Manage HelpScout tickets and conversations."
        self.config = config
        self._oauth_service = oauth_service
        self._timeout = self._coerce_float(config.get("timeout"), default=30.0)

        if client_factory is not None:
            self._client_factory = client_factory
        else:
            self._client_factory = lambda token: HelpScoutClient(access_token=token, timeout=self._timeout)

        # Configuration
        self.default_mailbox_id: Optional[int] = self._coerce_optional_int(config.get("default_mailbox_id"))
        self.max_results: int = self._coerce_int(config.get("max_results"), default=10, minimum=1, maximum=50)
        self.default_action: str = str(config.get("default_action") or "list").lower()

        # Mailbox configuration
        mailboxes_raw = config.get("mailboxes")
        self.mailboxes: List[MailboxConfig] = self._normalize_mailboxes(mailboxes_raw)
        self.mailbox_by_id: Dict[int, MailboxConfig] = {m.id: m for m in self.mailboxes}

    @staticmethod
    def _coerce_float(value: Any, *, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_optional_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError):
            result = default
        if result < minimum:
            return minimum
        if result > maximum:
            return maximum
        return result

    @staticmethod
    def _normalize_mailboxes(value: Any) -> List[MailboxConfig]:
        mailboxes: List[MailboxConfig] = []
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, int):
                    mailboxes.append(MailboxConfig(id=entry))
                elif isinstance(entry, dict):
                    mailbox_id = entry.get("id")
                    if isinstance(mailbox_id, int):
                        mailboxes.append(MailboxConfig(
                            id=mailbox_id,
                            name=entry.get("name"),
                            email=entry.get("email"),
                        ))
        elif isinstance(value, dict):
            for mailbox_id, name in value.items():
                try:
                    mid = int(mailbox_id)
                    mailboxes.append(MailboxConfig(id=mid, name=name if isinstance(name, str) else None))
                except (TypeError, ValueError):
                    continue
        return mailboxes

    @staticmethod
    def _extract_client_id(metadata: Optional[Dict[str, Any]]) -> str:
        if not isinstance(metadata, dict):
            raise HelpScoutAbilityConfigError(
                "Unable to determine client context for HelpScout ability. Please retry after selecting a client."
            )
        candidates = [
            metadata.get("client_id"),
            metadata.get("clientId"),
        ]
        client_obj = metadata.get("client")
        if isinstance(client_obj, dict):
            candidates.append(client_obj.get("id"))
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        raise HelpScoutAbilityConfigError(
            "Unable to determine client context for HelpScout ability. Please retry after selecting a client."
        )

    async def _acquire_client(
        self,
        metadata: Optional[Dict[str, Any]],
        *,
        force_refresh: bool = False,
    ) -> Tuple[HelpScoutClient, Optional[str]]:
        token: Optional[str] = None
        resolved_client_id: Optional[str] = None

        if self._oauth_service is not None:
            resolved_client_id = self._extract_client_id(metadata)
            bundle = await self._oauth_service.ensure_valid_token(resolved_client_id, force_refresh=force_refresh)
            if not bundle or not bundle.access_token:
                raise HelpScoutAbilityConfigError(
                    "HelpScout is not connected for this client yet. Ask an administrator to complete the OAuth connection."
                )
            token = bundle.access_token
        else:
            token = str(self.config.get("access_token") or "").strip()
            if not token:
                raise HelpScoutAbilityConfigError(
                    "HelpScout ability requires an OAuth connection. Ask an administrator to connect HelpScout for this client."
                )

        return self._client_factory(token), resolved_client_id

    async def _run_action(self, client: HelpScoutClient, action: str, details: Dict[str, Any]) -> str:
        if action == "skip":
            return ""
        if action == "list":
            return await self._handle_list(client, details)
        if action == "get":
            return await self._handle_get(client, details)
        if action == "create":
            return await self._handle_create(client, details)
        if action == "update":
            return await self._handle_update(client, details)
        if action == "reply":
            return await self._handle_reply(client, details)
        if action == "note":
            return await self._handle_add_note(client, details)
        if action == "close":
            return await self._handle_close(client, details)
        if action == "reopen":
            return await self._handle_reopen(client, details)
        if action == "assign":
            return await self._handle_assign(client, details)
        return "HelpScout ability is ready. Ask me to list tickets, create a ticket, update, reply, or close tickets."

    async def invoke(self, *, user_inquiry: str, metadata: Optional[Dict[str, Any]] = None, **_: Any) -> str:
        if not user_inquiry or not isinstance(user_inquiry, str):
            raise ValueError("HelpScout ability requires a user inquiry string.")

        action, details = self._interpret_intent(user_inquiry)
        logger.info("HelpScout ability intent resolved", extra={"slug": self.slug, "action": action, "details": details})

        if action == "skip":
            return "This inquiry doesn't appear to be related to HelpScout tickets. No action was taken."

        try:
            client, client_id = await self._acquire_client(metadata)
        except HelpScoutAbilityConfigError as exc:
            logger.warning("HelpScout client acquisition failed", exc_info=False)
            return str(exc)
        except Exception as exc:
            logger.exception("Unexpected error acquiring HelpScout client")
            return f"Failed to acquire HelpScout client: {exc}"

        refresh_attempted = False
        while True:
            try:
                return await self._run_action(client, action, details)
            except HelpScoutAPIError as exc:
                status = getattr(exc, "status_code", None)
                if (
                    self._oauth_service
                    and client_id
                    and not refresh_attempted
                    and status in {401, 403}
                ):
                    refresh_attempted = True
                    logger.info("HelpScout token rejected with status %s; forcing refresh", status)
                    try:
                        client, _ = await self._acquire_client(metadata, force_refresh=True)
                        continue
                    except HelpScoutAbilityConfigError:
                        return "HelpScout connection expired. Please reconnect HelpScout from your dashboard."
                    except Exception:
                        logger.exception("Forced HelpScout token refresh failed")
                        return "HelpScout connection could not be refreshed. Please reconnect HelpScout."
                logger.error("HelpScout API call failed", exc_info=True)
                return f"HelpScout API error: {exc}"
            except Exception as exc:
                logger.exception("HelpScout ability execution failed")
                return f"HelpScout ability failed: {exc}"

    # --- Intent parsing helpers -------------------------------------------------

    def _interpret_intent(self, message: str) -> Tuple[str, Dict[str, Any]]:
        lower = message.lower()

        # Check if message is HelpScout-related
        helpscout_keywords = (
            "helpscout", "help scout", "ticket", "tickets", "conversation", "conversations",
            "support", "inbox", "mailbox", "customer", "reply", "respond", "close",
            "reopen", "assign", "open ticket", "pending ticket", "active ticket",
        )
        is_helpscout_related = any(keyword in lower for keyword in helpscout_keywords)

        if not is_helpscout_related:
            return ("skip", {"raw": message, "lower": lower})

        # Determine action priority
        if any(phrase in lower for phrase in ("close ticket", "close the ticket", "close conversation", "mark closed", "mark as closed")):
            action = "close"
        elif any(phrase in lower for phrase in ("reopen", "re-open", "open again", "make active")):
            action = "reopen"
        elif any(phrase in lower for phrase in ("assign to", "assign ticket", "assign the ticket", "assign conversation")):
            action = "assign"
        elif any(phrase in lower for phrase in ("add note", "add a note", "internal note", "private note")):
            action = "note"
        elif any(phrase in lower for phrase in ("reply to", "respond to", "send reply", "send a reply", "write back")):
            action = "reply"
        elif any(word in lower for word in ("create", "new ticket", "open ticket", "create ticket", "submit ticket")):
            action = "create"
        elif any(phrase in lower for phrase in ("update ticket", "update the ticket", "change ticket", "modify ticket")):
            action = "update"
        elif any(phrase in lower for phrase in ("get ticket", "show ticket", "view ticket", "ticket details", "ticket #", "conversation #")):
            action = "get"
        elif any(word in lower for word in ("list", "show", "what", "pending", "open", "active", "tickets", "conversations")):
            action = "list"
        else:
            action = self.default_action if self.default_action in {"list", "create", "get", "update", "reply", "close"} else "list"

        details: Dict[str, Any] = {
            "raw": message,
            "lower": lower,
        }

        # Extract conversation/ticket ID
        conversation_id = self._extract_conversation_id(message)
        if conversation_id:
            details["conversation_id"] = conversation_id

        # Extract email address
        email = self._extract_email(message)
        if email:
            details["email"] = email

        # Extract quoted text (for subject or message content)
        quoted = QUOTE_PATTERN.findall(message)
        if quoted:
            details["quoted_text"] = quoted

        # Extract status filter
        status = self._extract_status(message)
        if status:
            details["status"] = status

        # Extract mailbox
        mailbox_id = self._extract_mailbox(message)
        if mailbox_id:
            details["mailbox_id"] = mailbox_id
        elif self.default_mailbox_id:
            details["mailbox_id"] = self.default_mailbox_id

        # Extract subject for create
        if action == "create":
            subject = self._extract_subject(message)
            if subject:
                details["subject"] = subject

        # Extract reply/note text
        if action in ("reply", "note"):
            text = self._extract_message_text(message)
            if text:
                details["text"] = text

        return action, details

    @staticmethod
    def _extract_conversation_id(message: str) -> Optional[int]:
        match = CONVERSATION_ID_PATTERN.search(message)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
        return None

    @staticmethod
    def _extract_email(message: str) -> Optional[str]:
        match = EMAIL_PATTERN.search(message)
        if match:
            return match.group(0)
        return None

    @staticmethod
    def _extract_status(message: str) -> Optional[str]:
        lower = message.lower()
        if "closed" in lower:
            return "closed"
        if "pending" in lower:
            return "pending"
        if "spam" in lower:
            return "spam"
        if "active" in lower or "open" in lower:
            return "active"
        if "all" in lower:
            return "all"
        return None

    def _extract_mailbox(self, message: str) -> Optional[int]:
        lower = message.lower()
        for mailbox in self.mailboxes:
            if mailbox.name and mailbox.name.lower() in lower:
                return mailbox.id
            if mailbox.email and mailbox.email.lower() in lower:
                return mailbox.id
        return None

    @staticmethod
    def _extract_subject(message: str) -> Optional[str]:
        # Look for "subject:" pattern
        match = re.search(r"subject[:\s]+[\"']?([^\"']+)[\"']?", message, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        # Fall back to quoted text
        quoted = QUOTE_PATTERN.findall(message)
        if quoted:
            return quoted[0].strip()
        return None

    @staticmethod
    def _extract_message_text(message: str) -> Optional[str]:
        # Look for message patterns
        patterns = [
            r"(?:message|reply|note|text)[:\s]+[\"']?(.+?)[\"']?$",
            r"(?:saying|with)[:\s]+[\"']?(.+?)[\"']?$",
        ]
        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()
        # Fall back to last quoted text
        quoted = QUOTE_PATTERN.findall(message)
        if quoted:
            return quoted[-1].strip()
        return None

    # --- Action handlers -------------------------------------------------------

    async def _handle_list(self, client: HelpScoutClient, details: Dict[str, Any]) -> str:
        mailbox_id = details.get("mailbox_id")
        status = details.get("status", "active")

        logger.info(
            "HelpScout list: fetching conversations",
            extra={"mailbox_id": mailbox_id, "status": status},
        )

        try:
            data = await client.list_conversations(
                mailbox_id=mailbox_id,
                status=status,
                page=1,
            )
        except HelpScoutAPIError as exc:
            return f"Failed to list tickets: {exc}"

        conversations = data.get("_embedded", {}).get("conversations", [])
        if not conversations:
            return f"No {status} tickets found."

        # Limit results
        conversations = conversations[:self.max_results]

        lines = [f"Found {len(conversations)} {status} ticket(s):"]
        for conv in conversations:
            conv_id = conv.get("id")
            subject = conv.get("subject", "No subject")
            conv_status = conv.get("status", "unknown")
            customer = conv.get("primaryCustomer", {})
            customer_email = ""
            if isinstance(customer, dict):
                customer_email = customer.get("email", "")

            assignee = conv.get("assignee", {})
            assignee_name = ""
            if isinstance(assignee, dict):
                first = assignee.get("firstName", "")
                last = assignee.get("lastName", "")
                assignee_name = f"{first} {last}".strip()

            line = f"• #{conv_id}: {subject}"
            if customer_email:
                line += f" (from: {customer_email})"
            if assignee_name:
                line += f" [assigned to: {assignee_name}]"
            line += f" [{conv_status}]"
            lines.append(line)

        return "\n".join(lines)

    async def _handle_get(self, client: HelpScoutClient, details: Dict[str, Any]) -> str:
        conversation_id = details.get("conversation_id")
        if not conversation_id:
            return "Please specify a ticket ID to view (e.g., 'show ticket #123456')."

        logger.info(
            "HelpScout get: fetching conversation",
            extra={"conversation_id": conversation_id},
        )

        try:
            data = await client.get_conversation(conversation_id, embed="threads")
        except HelpScoutAPIError as exc:
            return f"Failed to get ticket #{conversation_id}: {exc}"

        subject = data.get("subject", "No subject")
        status = data.get("status", "unknown")
        created_at = data.get("createdAt", "")

        customer = data.get("primaryCustomer", {})
        customer_email = customer.get("email", "") if isinstance(customer, dict) else ""

        assignee = data.get("assignee", {})
        assignee_name = ""
        if isinstance(assignee, dict):
            first = assignee.get("firstName", "")
            last = assignee.get("lastName", "")
            assignee_name = f"{first} {last}".strip()

        lines = [
            f"Ticket #{conversation_id}",
            f"Subject: {subject}",
            f"Status: {status}",
            f"Customer: {customer_email}",
        ]
        if assignee_name:
            lines.append(f"Assigned to: {assignee_name}")
        if created_at:
            lines.append(f"Created: {created_at}")

        # Include threads if available
        threads = data.get("_embedded", {}).get("threads", [])
        if threads:
            lines.append(f"\nThreads ({len(threads)}):")
            for thread in threads[:5]:  # Show last 5 threads
                thread_type = thread.get("type", "")
                thread_body = thread.get("body", "")
                if thread_body and len(thread_body) > 200:
                    thread_body = thread_body[:200] + "..."
                lines.append(f"  [{thread_type}] {thread_body}")

        return "\n".join(lines)

    async def _handle_create(self, client: HelpScoutClient, details: Dict[str, Any]) -> str:
        mailbox_id = details.get("mailbox_id")
        if not mailbox_id:
            if self.mailboxes:
                mailbox_id = self.mailboxes[0].id
            else:
                return "Please specify a mailbox to create the ticket in, or configure a default mailbox."

        email = details.get("email")
        if not email:
            return "Please provide a customer email address for the ticket."

        subject = details.get("subject")
        if not subject:
            quoted = details.get("quoted_text", [])
            if quoted:
                subject = quoted[0]
            else:
                return "Please provide a subject for the ticket."

        thread_text = details.get("text")
        if not thread_text:
            quoted = details.get("quoted_text", [])
            if len(quoted) > 1:
                thread_text = quoted[-1]
            elif subject:
                thread_text = subject
            else:
                return "Please provide the message content for the ticket."

        logger.info(
            "HelpScout create: creating conversation",
            extra={"mailbox_id": mailbox_id, "email": email, "subject": subject},
        )

        try:
            result = await client.create_conversation(
                subject=subject,
                mailbox_id=mailbox_id,
                customer_email=email,
                thread_text=thread_text,
            )
        except HelpScoutAPIError as exc:
            return f"Failed to create ticket: {exc}"

        resource_id = result.get("_resource_id")
        if resource_id:
            return f"Ticket created successfully. Ticket ID: #{resource_id}"
        return "Ticket created successfully."

    async def _handle_update(self, client: HelpScoutClient, details: Dict[str, Any]) -> str:
        conversation_id = details.get("conversation_id")
        if not conversation_id:
            return "Please specify a ticket ID to update (e.g., 'update ticket #123456')."

        # Determine what to update
        lower = details.get("lower", "")
        quoted = details.get("quoted_text", [])

        if "subject" in lower and quoted:
            new_subject = quoted[0]
            try:
                await client.update_conversation(
                    conversation_id,
                    operation="replace",
                    path="/subject",
                    value=new_subject,
                )
                return f"Ticket #{conversation_id} subject updated to: {new_subject}"
            except HelpScoutAPIError as exc:
                return f"Failed to update ticket: {exc}"

        status = details.get("status")
        if status:
            try:
                await client.update_conversation_status(conversation_id, status)
                return f"Ticket #{conversation_id} status updated to: {status}"
            except HelpScoutAPIError as exc:
                return f"Failed to update ticket status: {exc}"

        return "Please specify what to update (e.g., subject or status)."

    async def _handle_reply(self, client: HelpScoutClient, details: Dict[str, Any]) -> str:
        conversation_id = details.get("conversation_id")
        if not conversation_id:
            return "Please specify a ticket ID to reply to (e.g., 'reply to ticket #123456')."

        text = details.get("text")
        if not text:
            quoted = details.get("quoted_text", [])
            if quoted:
                text = quoted[-1]
            else:
                return "Please provide the reply message content."

        # First get the conversation to find the customer ID
        try:
            conv = await client.get_conversation(conversation_id)
        except HelpScoutAPIError as exc:
            return f"Failed to get ticket #{conversation_id}: {exc}"

        customer = conv.get("primaryCustomer", {})
        customer_id = customer.get("id") if isinstance(customer, dict) else None
        if not customer_id:
            return f"Could not find customer for ticket #{conversation_id}."

        logger.info(
            "HelpScout reply: creating reply",
            extra={"conversation_id": conversation_id, "customer_id": customer_id},
        )

        try:
            result = await client.create_reply(
                conversation_id,
                text=text,
                customer_id=customer_id,
            )
        except HelpScoutAPIError as exc:
            return f"Failed to send reply: {exc}"

        return f"Reply sent to ticket #{conversation_id} successfully."

    async def _handle_add_note(self, client: HelpScoutClient, details: Dict[str, Any]) -> str:
        conversation_id = details.get("conversation_id")
        if not conversation_id:
            return "Please specify a ticket ID to add a note to (e.g., 'add note to ticket #123456')."

        text = details.get("text")
        if not text:
            quoted = details.get("quoted_text", [])
            if quoted:
                text = quoted[-1]
            else:
                return "Please provide the note content."

        logger.info(
            "HelpScout note: creating note",
            extra={"conversation_id": conversation_id},
        )

        try:
            await client.create_note(conversation_id, text=text)
        except HelpScoutAPIError as exc:
            return f"Failed to add note: {exc}"

        return f"Note added to ticket #{conversation_id} successfully."

    async def _handle_close(self, client: HelpScoutClient, details: Dict[str, Any]) -> str:
        conversation_id = details.get("conversation_id")
        if not conversation_id:
            return "Please specify a ticket ID to close (e.g., 'close ticket #123456')."

        logger.info(
            "HelpScout close: closing conversation",
            extra={"conversation_id": conversation_id},
        )

        try:
            await client.update_conversation_status(conversation_id, "closed")
        except HelpScoutAPIError as exc:
            return f"Failed to close ticket: {exc}"

        return f"Ticket #{conversation_id} has been closed."

    async def _handle_reopen(self, client: HelpScoutClient, details: Dict[str, Any]) -> str:
        conversation_id = details.get("conversation_id")
        if not conversation_id:
            return "Please specify a ticket ID to reopen (e.g., 'reopen ticket #123456')."

        logger.info(
            "HelpScout reopen: reopening conversation",
            extra={"conversation_id": conversation_id},
        )

        try:
            await client.update_conversation_status(conversation_id, "active")
        except HelpScoutAPIError as exc:
            return f"Failed to reopen ticket: {exc}"

        return f"Ticket #{conversation_id} has been reopened."

    async def _handle_assign(self, client: HelpScoutClient, details: Dict[str, Any]) -> str:
        conversation_id = details.get("conversation_id")
        if not conversation_id:
            return "Please specify a ticket ID to assign (e.g., 'assign ticket #123456 to user')."

        # Extract user ID from message
        # This is simplified - in production you might want to look up users by name/email
        raw = details.get("raw", "")
        user_id_match = re.search(r"user\s*(?:#|id)?\s*(\d+)", raw, re.IGNORECASE)
        if not user_id_match:
            return "Please specify a user ID to assign the ticket to (e.g., 'assign ticket #123456 to user #789')."

        user_id = int(user_id_match.group(1))

        logger.info(
            "HelpScout assign: assigning conversation",
            extra={"conversation_id": conversation_id, "user_id": user_id},
        )

        try:
            await client.assign_conversation(conversation_id, user_id)
        except HelpScoutAPIError as exc:
            return f"Failed to assign ticket: {exc}"

        return f"Ticket #{conversation_id} has been assigned to user #{user_id}."


def _check_duplicate_call(slug: str, call_hash: str) -> Optional[Any]:
    """Check if this call is a duplicate of a recent call."""
    now = time.time()

    if slug not in _TOOL_CALL_HISTORY:
        _TOOL_CALL_HISTORY[slug] = deque(maxlen=_MAX_HISTORY_SIZE)
        return None

    history = _TOOL_CALL_HISTORY[slug]

    while history and (now - history[0][1]) > _DEDUPE_WINDOW_SECONDS:
        history.popleft()

    for stored_hash, timestamp, result in history:
        if stored_hash == call_hash and (now - timestamp) <= _DEDUPE_WINDOW_SECONDS:
            logger.info(
                f"🔄 Duplicate tool call detected: slug={slug}, hash={call_hash[:8]}, "
                f"age={now - timestamp:.1f}s - returning cached result"
            )
            return result

    return None


def _record_call(slug: str, call_hash: str, result: Any) -> None:
    """Record a successful tool call in the history."""
    if slug not in _TOOL_CALL_HISTORY:
        _TOOL_CALL_HISTORY[slug] = deque(maxlen=_MAX_HISTORY_SIZE)

    _TOOL_CALL_HISTORY[slug].append((call_hash, time.time(), result))


def build_helpscout_tool(
    tool_def: Dict[str, Any],
    config: Dict[str, Any],
    *,
    oauth_service: Any = None,
    client_factory: Optional[Any] = None,
) -> Any:
    """Build a LiveKit-compatible HelpScout tool."""
    slug = tool_def.get("slug") or tool_def.get("name") or "helpscout_tickets"
    description = tool_def.get("description") or "Manage HelpScout support tickets and conversations."

    handler = HelpScoutToolHandler(
        slug=slug,
        description=description,
        config=config,
        oauth_service=oauth_service,
        client_factory=client_factory,
    )

    async def _invoke(**kwargs: Any) -> Dict[str, Any]:
        metadata = kwargs.get("metadata") if isinstance(kwargs.get("metadata"), dict) else {}
        user_inquiry = kwargs.get("user_inquiry")
        if not isinstance(user_inquiry, str) or not user_inquiry.strip():
            auto_fallback = _coalesce_user_inquiry(metadata, kwargs.get("messages"))
            if auto_fallback:
                user_inquiry = auto_fallback

        if not user_inquiry and isinstance(metadata, dict):
            for key in ("user_inquiry", "latest_user_text", "transcript", "message"):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip():
                    user_inquiry = value.strip()
                    break

        if not user_inquiry or not isinstance(user_inquiry, str):
            message = (
                "I wasn't able to determine what to do with HelpScout tickets. "
                "Please repeat the request in your own words."
            )
            return message

        # Check for duplicate call
        call_hash = _compute_call_hash(slug, user_inquiry)
        cached_result = _check_duplicate_call(slug, call_hash)
        if cached_result is not None:
            return cached_result

        # Execute the tool
        summary = await handler.invoke(user_inquiry=user_inquiry, metadata=metadata)
        if not isinstance(summary, str):
            summary = str(summary)

        result = {
            "slug": slug,
            "summary": summary,
            "text": summary,
            "raw_text": summary,
        }

        # Record successful call
        _record_call(slug, call_hash, result)

        return result

    enhanced_description = f"{description} Read the tool's complete output to the user."

    return lk_function_tool(
        raw_schema={
            "name": slug,
            "description": enhanced_description,
            "parameters": {
                "type": "object",
                "properties": {
                    "user_inquiry": {
                        "type": "string",
                        "description": "Pass the COMPLETE user request VERBATIM including the action verb. For example: if user says 'list open tickets', pass EXACTLY 'list open tickets'. REQUIRED: Must start with action verb (list/get/create/update/reply/close/reopen/assign).",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Additional session metadata.",
                        "additionalProperties": True,
                    },
                },
                "required": ["user_inquiry"],
            },
        }
    )(_invoke)
