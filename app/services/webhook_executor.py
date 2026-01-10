"""
Webhook Executor - executes ambient abilities via webhook calls.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

import httpx

from app.models.ambient import AmbientAbilityRun

logger = logging.getLogger(__name__)


class WebhookExecutor:
    """Executes ambient abilities by calling configured webhooks."""

    async def execute(self, run: AmbientAbilityRun) -> Dict[str, Any]:
        """
        Execute an ambient ability via webhook.

        Args:
            run: The ambient ability run containing context and config

        Returns:
            Dict with webhook response or error
        """
        try:
            ability_config = run.ability_config or {}
            webhook_url = ability_config.get("webhook_url")

            if not webhook_url:
                return {
                    "success": False,
                    "error": "No webhook_url configured for this ability"
                }

            # Build payload
            payload = {
                "run_id": str(run.id),
                "ability_id": str(run.ability_id),
                "ability_slug": run.ability_slug,
                "client_id": str(run.client_id),
                "user_id": str(run.user_id) if run.user_id else None,
                "conversation_id": str(run.conversation_id) if run.conversation_id else None,
                "session_id": str(run.session_id) if run.session_id else None,
                "trigger_type": run.trigger_type,
                "input_context": run.input_context,
                "timestamp": datetime.utcnow().isoformat()
            }

            # Get timeout from config
            timeout = ability_config.get("webhook_timeout", 30)

            # Optional authentication
            headers = {"Content-Type": "application/json"}
            if ability_config.get("webhook_auth_header"):
                auth_header = ability_config["webhook_auth_header"]
                auth_value = ability_config.get("webhook_auth_value", "")
                headers[auth_header] = auth_value

            logger.info(
                f"Executing webhook for {run.ability_slug} -> {webhook_url[:50]}..."
            )

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    webhook_url,
                    json=payload,
                    headers=headers,
                    timeout=timeout
                )

                response_data = {
                    "success": response.is_success,
                    "status_code": response.status_code,
                    "webhook_url": webhook_url,
                }

                if response.is_success:
                    try:
                        response_data["response"] = response.json()
                    except Exception:
                        response_data["response"] = response.text[:1000]

                    logger.info(
                        f"Webhook executed successfully for {run.ability_slug} "
                        f"(status: {response.status_code})"
                    )
                else:
                    response_data["error"] = f"HTTP {response.status_code}: {response.text[:500]}"
                    logger.warning(
                        f"Webhook failed for {run.ability_slug}: {response.status_code}"
                    )

                return response_data

        except httpx.TimeoutException:
            error_msg = f"Webhook timeout after {timeout}s"
            logger.error(f"Webhook timeout for {run.ability_slug}: {webhook_url}")
            return {
                "success": False,
                "error": error_msg,
                "webhook_url": webhook_url
            }

        except httpx.RequestError as e:
            error_msg = f"Webhook request failed: {str(e)}"
            logger.error(f"Webhook request error for {run.ability_slug}: {e}")
            return {
                "success": False,
                "error": error_msg,
                "webhook_url": webhook_url
            }

        except Exception as e:
            error_msg = f"Webhook execution error: {str(e)}"
            logger.error(f"Webhook execution failed for {run.ability_slug}: {e}", exc_info=True)
            return {
                "success": False,
                "error": error_msg
            }


# Singleton instance
webhook_executor = WebhookExecutor()
