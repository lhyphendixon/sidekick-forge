"""
Ambient Ability Worker - background worker that processes ambient ability runs.
"""

import asyncio
import logging
import time
from typing import Dict, Any

from app.models.ambient import AmbientAbilityRun, AmbientRunStatus
from app.services.ambient_ability_service import ambient_ability_service
from app.services.usersense_executor import usersense_executor
from app.services.webhook_executor import webhook_executor
from app.services.activity_logging_service import activity_logger

logger = logging.getLogger(__name__)


class AmbientAbilityWorker:
    """
    Background worker that polls for and executes pending ambient abilities.
    """

    def __init__(self, poll_interval: float = 5.0, batch_size: int = 5):
        """
        Initialize the worker.

        Args:
            poll_interval: Seconds between polling for pending runs
            batch_size: Maximum runs to fetch per poll
        """
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self._running = False
        self._task = None

    async def start(self):
        """Start the background worker."""
        if self._running:
            logger.warning("Ambient ability worker already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            f"Ambient ability worker started (poll_interval={self.poll_interval}s, "
            f"batch_size={self.batch_size})"
        )

    async def stop(self):
        """Stop the background worker."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Ambient ability worker stopped")

    async def _run_loop(self):
        """Main worker loop."""
        while self._running:
            try:
                await self._process_pending_runs()
            except Exception as e:
                logger.error(f"Error in ambient ability worker loop: {e}", exc_info=True)

            await asyncio.sleep(self.poll_interval)

    async def _process_pending_runs(self):
        """Fetch and process pending runs."""
        try:
            runs = await ambient_ability_service.get_pending_runs(limit=self.batch_size)

            if not runs:
                return

            logger.debug(f"Processing {len(runs)} pending ambient ability runs")

            for run in runs:
                try:
                    await self._execute_run(run)
                except Exception as e:
                    logger.error(
                        f"Failed to execute ambient run {run.id}: {e}",
                        exc_info=True
                    )
                    await ambient_ability_service.update_run_status(
                        str(run.id),
                        AmbientRunStatus.FAILED,
                        error=str(e)
                    )

        except Exception as e:
            logger.error(f"Failed to fetch pending runs: {e}")

    async def _execute_run(self, run: AmbientAbilityRun):
        """Execute a single ambient ability run."""
        logger.info(
            f"Executing ambient ability: {run.ability_slug} "
            f"(run_id: {str(run.id)[:8]}..., type: {run.ability_type})"
        )

        # Mark as running
        await ambient_ability_service.update_run_status(
            str(run.id),
            AmbientRunStatus.RUNNING
        )

        # Track execution time
        start_time = time.time()

        # Log ability start
        try:
            await activity_logger.log_ability_run(
                client_id=run.client_id,
                agent_id=None,  # Abilities may not be agent-specific
                ability_name=run.ability_slug or "unknown",
                ability_id=str(run.ability_id) if run.ability_id else None,
                details={
                    "trigger_type": run.trigger_type,
                    "ability_type": run.ability_type
                }
            )
        except Exception as log_err:
            logger.warning(f"Failed to log ability start: {log_err}")

        try:
            # Execute based on ability type
            result = await self._dispatch_execution(run)

            # Calculate duration
            duration_ms = int((time.time() - start_time) * 1000)

            # Mark as completed
            await ambient_ability_service.update_run_status(
                str(run.id),
                AmbientRunStatus.COMPLETED,
                output_result=result
            )

            logger.info(
                f"Ambient ability {run.ability_slug} completed successfully "
                f"(run_id: {str(run.id)[:8]}...)"
            )

            # Log ability completion
            try:
                await activity_logger.log_ability_completed(
                    client_id=run.client_id,
                    agent_id=None,
                    ability_name=run.ability_slug or "unknown",
                    ability_id=str(run.ability_id) if run.ability_id else None,
                    duration_ms=duration_ms,
                    result_summary=result.get("summary") if isinstance(result, dict) else None
                )
            except Exception as log_err:
                logger.warning(f"Failed to log ability completion: {log_err}")

        except Exception as e:
            logger.error(f"Ambient ability {run.ability_slug} failed: {e}")
            await ambient_ability_service.update_run_status(
                str(run.id),
                AmbientRunStatus.FAILED,
                error=str(e)
            )

            # Log ability failure
            try:
                await activity_logger.log_ability_failed(
                    client_id=run.client_id,
                    agent_id=None,
                    ability_name=run.ability_slug or "unknown",
                    error=str(e),
                    ability_id=str(run.ability_id) if run.ability_id else None
                )
            except Exception as log_err:
                logger.warning(f"Failed to log ability failure: {log_err}")

            raise

    async def _dispatch_execution(self, run: AmbientAbilityRun) -> Dict[str, Any]:
        """Dispatch execution to the appropriate executor."""
        ability_type = run.ability_type or "unknown"
        ability_slug = run.ability_slug or "unknown"

        # UserSense (builtin)
        if ability_slug == "usersense" or ability_type == "builtin":
            result = await usersense_executor.execute(run)
            return {
                "executor": "usersense",
                "updates_count": len(result.updates),
                "sections_updated": result.sections_updated,
                "summary": result.summary,
                "confidence": result.confidence
            }

        # Webhook
        elif ability_type == "webhook":
            result = await webhook_executor.execute(run)
            return {
                "executor": "webhook",
                **result
            }

        # n8n (treated as webhook)
        elif ability_type == "n8n":
            result = await webhook_executor.execute(run)
            return {
                "executor": "n8n",
                **result
            }

        else:
            raise ValueError(f"Unknown ability type: {ability_type}")


# Global worker instance
ambient_ability_worker = AmbientAbilityWorker()
