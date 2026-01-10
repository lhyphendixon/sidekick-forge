from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import aiohttp


logger = logging.getLogger(__name__)


class HelpScoutAPIError(RuntimeError):
    """Raised when the HelpScout API returns an error response."""

    def __init__(self, message: str, *, status_code: Optional[int] = None, body: Optional[str] = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class HelpScoutClient:
    """Lightweight async client for the HelpScout Mailbox API v2."""

    BASE_URL = "https://api.helpscout.net/v2"

    def __init__(
        self,
        access_token: str,
        timeout: float = 30.0,
    ) -> None:
        if not access_token or not isinstance(access_token, str):
            raise ValueError("HelpScout access token is required")
        self._access_token = access_token.strip()
        self._timeout = timeout

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        return_headers: bool = False,
    ) -> Dict[str, Any]:
        """Make an authenticated request to the HelpScout API."""
        url = f"{self.BASE_URL}{path}"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method,
                url,
                params=params,
                data=json.dumps(payload) if payload is not None else None,
                headers=headers,
            ) as response:
                text = await response.text()

                # Handle 204 No Content (successful updates)
                if response.status == 204:
                    if return_headers:
                        return {"_headers": dict(response.headers)}
                    return {}

                # Handle 201 Created (successful creates)
                if response.status == 201:
                    result: Dict[str, Any] = {}
                    if text:
                        try:
                            result = json.loads(text)
                        except json.JSONDecodeError:
                            pass
                    # Include headers for Resource-ID and Location
                    result["_resource_id"] = response.headers.get("Resource-ID")
                    result["_location"] = response.headers.get("Location")
                    return result

                # Handle errors
                if response.status // 100 != 2:
                    logger.error(
                        "HelpScout API error",
                        extra={
                            "status": response.status,
                            "url": url,
                            "params": params,
                            "payload": payload,
                            "body": text,
                        },
                    )
                    raise HelpScoutAPIError(
                        f"HelpScout API request failed ({response.status}): {text or 'no response body'}",
                        status_code=response.status,
                        body=text,
                    )

                if not text:
                    return {}
                try:
                    return json.loads(text)
                except json.JSONDecodeError as exc:
                    raise HelpScoutAPIError("Failed to parse HelpScout API response as JSON") from exc

    # -------------------------------------------------------------------------
    # Conversations (Tickets)
    # -------------------------------------------------------------------------

    async def list_conversations(
        self,
        *,
        mailbox_id: Optional[int] = None,
        status: Optional[str] = None,
        tag: Optional[str] = None,
        assigned_to: Optional[int] = None,
        modified_since: Optional[str] = None,
        sort_field: Optional[str] = None,
        sort_order: Optional[str] = None,
        query: Optional[str] = None,
        page: int = 1,
        embed: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        List conversations with optional filters.

        Args:
            mailbox_id: Filter by inbox ID
            status: Filter by status (active, closed, open, pending, spam, all)
            tag: Filter by tag name
            assigned_to: Filter by assignee user ID
            modified_since: ISO 8601 datetime string
            sort_field: Sort by createdAt, customerEmail, status, subject, etc.
            sort_order: desc or asc
            query: Advanced search query
            page: Page number (1-indexed)
            embed: Include sub-entities like 'threads'

        Returns:
            Paginated response with _embedded.conversations array
        """
        params: Dict[str, Any] = {"page": page}

        if mailbox_id is not None:
            params["mailbox"] = mailbox_id
        if status:
            params["status"] = status
        if tag:
            params["tag"] = tag
        if assigned_to is not None:
            params["assigned_to"] = assigned_to
        if modified_since:
            params["modifiedSince"] = modified_since
        if sort_field:
            params["sortField"] = sort_field
        if sort_order:
            params["sortOrder"] = sort_order
        if query:
            params["query"] = query
        if embed:
            params["embed"] = embed

        return await self._request("GET", "/conversations", params=params)

    async def get_conversation(
        self,
        conversation_id: int,
        *,
        embed: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get a single conversation by ID.

        Args:
            conversation_id: The conversation ID
            embed: Include sub-entities like 'threads'

        Returns:
            Conversation object with all fields
        """
        params: Dict[str, Any] = {}
        if embed:
            params["embed"] = embed

        return await self._request("GET", f"/conversations/{conversation_id}", params=params or None)

    async def create_conversation(
        self,
        *,
        subject: str,
        mailbox_id: int,
        customer_email: str,
        thread_text: str,
        conversation_type: str = "email",
        status: str = "active",
        customer_first_name: Optional[str] = None,
        customer_last_name: Optional[str] = None,
        customer_id: Optional[int] = None,
        assign_to: Optional[int] = None,
        tags: Optional[List[str]] = None,
        auto_reply: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a new conversation (ticket).

        Args:
            subject: Conversation subject line
            mailbox_id: Inbox ID where conversation is created
            customer_email: Customer's email address
            thread_text: Initial message/thread content
            conversation_type: Type of conversation (email, chat, phone)
            status: Initial status (active, closed, pending)
            customer_first_name: Optional customer first name
            customer_last_name: Optional customer last name
            customer_id: Use existing customer ID instead of email lookup
            assign_to: User ID to assign conversation to
            tags: List of tag names to add
            auto_reply: Whether to enable auto-replies

        Returns:
            Dict with _resource_id containing the new conversation ID
        """
        # Build customer object
        customer: Dict[str, Any] = {}
        if customer_id:
            customer["id"] = customer_id
        else:
            customer["email"] = customer_email
            if customer_first_name:
                customer["firstName"] = customer_first_name
            if customer_last_name:
                customer["lastName"] = customer_last_name

        # Build thread object (at least one required)
        thread: Dict[str, Any] = {
            "type": "customer",  # Thread from customer
            "text": thread_text,
            "customer": customer,
        }

        payload: Dict[str, Any] = {
            "subject": subject,
            "type": conversation_type,
            "mailboxId": mailbox_id,
            "status": status,
            "customer": customer,
            "threads": [thread],
            "autoReply": auto_reply,
        }

        if assign_to:
            payload["assignTo"] = assign_to
        if tags:
            payload["tags"] = tags

        return await self._request("POST", "/conversations", payload=payload)

    async def update_conversation(
        self,
        conversation_id: int,
        *,
        operation: str,
        path: str,
        value: Any = None,
    ) -> Dict[str, Any]:
        """
        Update a conversation using JSON Patch format.

        Args:
            conversation_id: The conversation ID to update
            operation: Patch operation (replace, add, move, remove)
            path: Field path to update (/subject, /status, /assignTo, etc.)
            value: New value (not needed for 'remove' operation)

        Common operations:
            - Update subject: op=replace, path=/subject, value="New subject"
            - Change status: op=replace, path=/status, value="closed"
            - Assign user: op=replace, path=/assignTo, value=123
            - Unassign: op=remove, path=/assignTo
            - Move mailbox: op=move, path=/mailboxId, value=456

        Returns:
            Empty dict on success (HTTP 204)
        """
        payload: Dict[str, Any] = {
            "op": operation,
            "path": path,
        }
        if value is not None:
            payload["value"] = value

        return await self._request("PATCH", f"/conversations/{conversation_id}", payload=payload)

    async def update_conversation_status(
        self,
        conversation_id: int,
        status: str,
    ) -> Dict[str, Any]:
        """
        Update conversation status.

        Args:
            conversation_id: The conversation ID
            status: New status (active, closed, pending, spam)
        """
        return await self.update_conversation(
            conversation_id,
            operation="replace",
            path="/status",
            value=status,
        )

    async def assign_conversation(
        self,
        conversation_id: int,
        user_id: int,
    ) -> Dict[str, Any]:
        """
        Assign a conversation to a user.

        Args:
            conversation_id: The conversation ID
            user_id: User ID to assign to
        """
        return await self.update_conversation(
            conversation_id,
            operation="replace",
            path="/assignTo",
            value=user_id,
        )

    async def unassign_conversation(
        self,
        conversation_id: int,
    ) -> Dict[str, Any]:
        """Remove assignment from a conversation."""
        return await self.update_conversation(
            conversation_id,
            operation="remove",
            path="/assignTo",
        )

    # -------------------------------------------------------------------------
    # Threads (Replies and Notes)
    # -------------------------------------------------------------------------

    async def create_reply(
        self,
        conversation_id: int,
        *,
        text: str,
        customer_id: int,
        user_id: Optional[int] = None,
        draft: bool = False,
        status: Optional[str] = None,
        assign_to: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Create a reply thread on a conversation.

        Args:
            conversation_id: The conversation ID
            text: Reply content
            customer_id: Customer being replied to
            user_id: User ID adding the reply
            draft: Create as draft (True) or send immediately (False)
            status: Update conversation status after reply
            assign_to: User ID to assign after reply

        Returns:
            Dict with _resource_id containing the new thread ID
        """
        payload: Dict[str, Any] = {
            "text": text,
            "customer": {"id": customer_id},
        }

        if user_id:
            payload["user"] = user_id
        if draft:
            payload["draft"] = True
        if status:
            payload["status"] = status
        if assign_to:
            payload["assignTo"] = assign_to

        return await self._request("POST", f"/conversations/{conversation_id}/reply", payload=payload)

    async def create_note(
        self,
        conversation_id: int,
        *,
        text: str,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Create an internal note on a conversation.

        Args:
            conversation_id: The conversation ID
            text: Note content
            user_id: User ID adding the note

        Returns:
            Dict with _resource_id containing the new thread ID
        """
        payload: Dict[str, Any] = {
            "text": text,
        }

        if user_id:
            payload["user"] = user_id

        return await self._request("POST", f"/conversations/{conversation_id}/notes", payload=payload)

    async def list_threads(
        self,
        conversation_id: int,
    ) -> List[Dict[str, Any]]:
        """
        List all threads for a conversation.

        Args:
            conversation_id: The conversation ID

        Returns:
            List of thread objects
        """
        data = await self._request("GET", f"/conversations/{conversation_id}/threads")
        return data.get("_embedded", {}).get("threads", [])

    # -------------------------------------------------------------------------
    # Mailboxes
    # -------------------------------------------------------------------------

    async def list_mailboxes(self) -> List[Dict[str, Any]]:
        """
        List all mailboxes the authenticated user can access.

        Returns:
            List of mailbox objects with id, name, email, etc.
        """
        data = await self._request("GET", "/mailboxes")
        return data.get("_embedded", {}).get("mailboxes", [])

    async def get_mailbox(self, mailbox_id: int) -> Dict[str, Any]:
        """
        Get a single mailbox by ID.

        Args:
            mailbox_id: The mailbox ID

        Returns:
            Mailbox object
        """
        return await self._request("GET", f"/mailboxes/{mailbox_id}")

    # -------------------------------------------------------------------------
    # Users
    # -------------------------------------------------------------------------

    async def list_users(self) -> List[Dict[str, Any]]:
        """
        List all users.

        Returns:
            List of user objects
        """
        data = await self._request("GET", "/users")
        return data.get("_embedded", {}).get("users", [])

    async def get_user(self, user_id: int) -> Dict[str, Any]:
        """
        Get a single user by ID.

        Args:
            user_id: The user ID

        Returns:
            User object
        """
        return await self._request("GET", f"/users/{user_id}")

    async def get_me(self) -> Dict[str, Any]:
        """
        Get the currently authenticated user.

        Returns:
            User object for the authenticated user
        """
        return await self._request("GET", "/users/me")

    # -------------------------------------------------------------------------
    # Customers
    # -------------------------------------------------------------------------

    async def list_customers(
        self,
        *,
        email: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        query: Optional[str] = None,
        page: int = 1,
    ) -> Dict[str, Any]:
        """
        List customers with optional filters.

        Args:
            email: Filter by email
            first_name: Filter by first name
            last_name: Filter by last name
            query: Advanced search query
            page: Page number

        Returns:
            Paginated response with _embedded.customers array
        """
        params: Dict[str, Any] = {"page": page}

        if email:
            params["email"] = email
        if first_name:
            params["firstName"] = first_name
        if last_name:
            params["lastName"] = last_name
        if query:
            params["query"] = query

        return await self._request("GET", "/customers", params=params)

    async def get_customer(self, customer_id: int) -> Dict[str, Any]:
        """
        Get a single customer by ID.

        Args:
            customer_id: The customer ID

        Returns:
            Customer object
        """
        return await self._request("GET", f"/customers/{customer_id}")

    async def create_customer(
        self,
        *,
        email: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        phone: Optional[str] = None,
        organization: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new customer.

        Args:
            email: Customer email (required)
            first_name: First name
            last_name: Last name
            phone: Phone number
            organization: Organization name

        Returns:
            Dict with _resource_id containing the new customer ID
        """
        payload: Dict[str, Any] = {
            "emails": [{"type": "work", "value": email}],
        }

        if first_name:
            payload["firstName"] = first_name
        if last_name:
            payload["lastName"] = last_name
        if phone:
            payload["phones"] = [{"type": "work", "value": phone}]
        if organization:
            payload["organization"] = organization

        return await self._request("POST", "/customers", payload=payload)

    # -------------------------------------------------------------------------
    # Tags
    # -------------------------------------------------------------------------

    async def list_tags(self) -> List[Dict[str, Any]]:
        """
        List all tags.

        Returns:
            List of tag objects
        """
        data = await self._request("GET", "/tags")
        return data.get("_embedded", {}).get("tags", [])

    async def add_conversation_tags(
        self,
        conversation_id: int,
        tags: List[str],
    ) -> Dict[str, Any]:
        """
        Add tags to a conversation.

        Args:
            conversation_id: The conversation ID
            tags: List of tag names to add
        """
        payload = {"tags": tags}
        return await self._request("PUT", f"/conversations/{conversation_id}/tags", payload=payload)
