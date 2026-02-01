"""
DocumentSense Learning Worker

Processes document intelligence extraction jobs when DocumentSense is enabled for a client.
Extracts intelligence from documents in batch with progress tracking.
"""

import json
import logging
import asyncio
from typing import Dict, Any, List, Optional

import httpx

from app.config import settings
from app.utils.supabase_credentials import SupabaseCredentialManager
from app.services.documentsense_executor import documentsense_executor, DocumentSenseResult

logger = logging.getLogger(__name__)

# Batch processing limits
DOCUMENTS_BATCH_SIZE = 5
MAX_DOCUMENTS_PER_JOB = 100


class DocumentSenseLearningWorker:
    """Processes document intelligence extraction jobs."""

    def __init__(self):
        self._running = False
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client for internal API calls."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=180.0)
        return self._http_client

    async def start(self):
        """Start the learning worker loop."""
        if self._running:
            logger.warning("DocumentSense Learning Worker already running")
            return

        self._running = True
        logger.info("DocumentSense Learning Worker started")

        while self._running:
            try:
                # Check for pending jobs
                job = await self._claim_next_job()
                if job:
                    await self._process_job(job)
                else:
                    # No jobs, wait before checking again
                    await asyncio.sleep(30)
            except Exception as e:
                logger.error(f"DocumentSense learning worker error: {e}", exc_info=True)
                await asyncio.sleep(10)

    async def stop(self):
        """Stop the learning worker."""
        self._running = False
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
        logger.info("DocumentSense Learning Worker stopped")

    async def _claim_next_job(self) -> Optional[Dict[str, Any]]:
        """Claim the next pending extraction job from the platform database."""
        try:
            from supabase import create_client
            platform_sb = create_client(
                settings.supabase_url,
                settings.supabase_service_role_key
            )

            result = platform_sb.rpc('claim_next_documentsense_job').execute()
            if result.data and len(result.data) > 0:
                return result.data[0]
            return None

        except Exception as e:
            logger.error(f"Failed to claim DocumentSense job: {e}")
            return None

    async def _process_job(self, job: Dict[str, Any]):
        """Process a single extraction job."""
        job_id = job['id']
        client_id = job['client_id']
        document_id = job.get('document_id')

        logger.info(f"Processing DocumentSense job {job_id} for client {client_id}")

        try:
            # Get client credentials
            client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
            from supabase import create_client
            client_sb = create_client(client_url, client_key)

            # If document_id is 0 (placeholder), process all documents needing extraction
            if document_id == 0:
                await self._process_batch_extraction(job_id, client_id, client_sb)
            else:
                await self._process_single_document(job_id, client_id, document_id, client_sb)

        except Exception as e:
            logger.error(f"DocumentSense job {job_id} failed: {e}", exc_info=True)
            await self._complete_job(job_id, success=False, error=str(e))

    async def _process_batch_extraction(
        self,
        job_id: str,
        client_id: str,
        client_sb
    ):
        """Process extraction for all documents that haven't been processed yet."""
        try:
            # Get all documents that don't have intelligence extracted yet
            # Query documents table, left join with document_intelligence
            documents_result = client_sb.table('documents').select(
                'id, title, content, status'
            ).eq('status', 'ready').order('created_at').limit(MAX_DOCUMENTS_PER_JOB).execute()

            documents = documents_result.data or []

            # Filter out documents that already have intelligence
            documents_to_process = []
            for doc in documents:
                # Check if intelligence exists
                intel_result = client_sb.table('document_intelligence').select(
                    'id'
                ).eq('document_id', doc['id']).eq('client_id', client_id).limit(1).execute()

                if not intel_result.data:
                    documents_to_process.append(doc)

            total_docs = len(documents_to_process)

            if total_docs == 0:
                await self._complete_job(job_id, success=True, summary="No documents needing extraction found")
                return

            logger.info(f"Found {total_docs} documents needing extraction for client {client_id}")

            # Process each document
            successful = 0
            failed = 0

            for i, doc in enumerate(documents_to_process):
                doc_id = doc['id']
                doc_title = doc.get('title', f'Document {doc_id}')
                doc_content = doc.get('content', '')

                progress = int(((i + 1) / total_docs) * 100)
                await self._update_job_progress(
                    job_id,
                    progress,
                    f"Extracting intelligence from document {i+1}/{total_docs}: {doc_title[:50]}...",
                    i + 1
                )

                try:
                    # Skip documents without content
                    if not doc_content or len(doc_content.strip()) < 50:
                        logger.warning(f"Skipping document {doc_id} - insufficient content")
                        continue

                    # Extract intelligence
                    result = await documentsense_executor.extract_intelligence(
                        client_id=client_id,
                        document_id=doc_id,
                        document_title=doc_title,
                        document_content=doc_content
                    )

                    if result.success:
                        successful += 1
                        logger.info(f"Successfully extracted intelligence from document {doc_id}")
                    else:
                        failed += 1
                        logger.warning(f"Failed to extract intelligence from document {doc_id}: {result.error}")

                except Exception as e:
                    failed += 1
                    logger.error(f"Failed to extract intelligence from document {doc_id}: {e}")

                # Small delay between documents to avoid overwhelming the LLM
                await asyncio.sleep(2)

            await self._complete_job(
                job_id,
                success=True,
                summary=f"Extracted intelligence from {successful} documents ({failed} failed)"
            )

            # Check if there are more documents to process and queue a follow-up job
            if successful > 0:
                await self._queue_followup_if_needed(client_id, client_sb)

        except Exception as e:
            logger.error(f"Batch extraction failed: {e}", exc_info=True)
            await self._complete_job(job_id, success=False, error=str(e))

    async def _process_single_document(
        self,
        job_id: str,
        client_id: str,
        document_id: int,
        client_sb
    ):
        """Process extraction for a single document."""
        try:
            await self._update_job_progress(job_id, 10, "Fetching document...", 0)

            # Get document content
            doc_result = client_sb.table('documents').select(
                'id, title, content, status'
            ).eq('id', document_id).limit(1).execute()

            if not doc_result.data:
                await self._complete_job(job_id, success=False, error=f"Document {document_id} not found")
                return

            doc = doc_result.data[0]
            doc_title = doc.get('title', f'Document {document_id}')
            doc_content = doc.get('content', '')

            if not doc_content or len(doc_content.strip()) < 50:
                await self._complete_job(job_id, success=False, error="Insufficient document content")
                return

            await self._update_job_progress(job_id, 30, f"Extracting intelligence from: {doc_title[:50]}...", 0)

            # Extract intelligence
            result = await documentsense_executor.extract_intelligence(
                client_id=client_id,
                document_id=document_id,
                document_title=doc_title,
                document_content=doc_content
            )

            if result.success:
                await self._complete_job(
                    job_id,
                    success=True,
                    summary=f"Extracted intelligence from {doc_title}"
                )
            else:
                await self._complete_job(
                    job_id,
                    success=False,
                    error=result.error or "Extraction failed"
                )

        except Exception as e:
            logger.error(f"Single document extraction failed: {e}", exc_info=True)
            await self._complete_job(job_id, success=False, error=str(e))

    async def _update_job_progress(
        self,
        job_id: str,
        progress: int,
        message: str,
        chunks_processed: int = None
    ):
        """Update job progress in platform database."""
        try:
            from supabase import create_client
            platform_sb = create_client(
                settings.supabase_url,
                settings.supabase_service_role_key
            )

            platform_sb.rpc('update_documentsense_job_progress', {
                'p_job_id': job_id,
                'p_progress_percent': progress,
                'p_progress_message': message,
                'p_chunks_processed': chunks_processed
            }).execute()

        except Exception as e:
            logger.warning(f"Failed to update DocumentSense job progress: {e}")

    async def _complete_job(
        self,
        job_id: str,
        success: bool,
        summary: str = None,
        error: str = None
    ):
        """Mark a job as completed or failed."""
        try:
            from supabase import create_client
            platform_sb = create_client(
                settings.supabase_url,
                settings.supabase_service_role_key
            )

            platform_sb.rpc('complete_documentsense_job', {
                'p_job_id': job_id,
                'p_success': success,
                'p_result_summary': summary,
                'p_error_message': error
            }).execute()

            logger.info(f"DocumentSense job {job_id} completed: success={success}")

        except Exception as e:
            logger.error(f"Failed to complete DocumentSense job: {e}")

    async def _queue_followup_if_needed(self, client_id: str, client_sb):
        """
        Check if there are more documents needing extraction and queue a follow-up job.
        This ensures all documents get processed without manual intervention.
        """
        try:
            # Count documents that still need processing
            # Get documents with 'ready' status
            documents_result = client_sb.table('documents').select(
                'id'
            ).eq('status', 'ready').limit(MAX_DOCUMENTS_PER_JOB + 1).execute()

            documents = documents_result.data or []

            # Filter out documents that already have intelligence
            remaining_count = 0
            for doc in documents:
                intel_result = client_sb.table('document_intelligence').select(
                    'id'
                ).eq('document_id', doc['id']).eq('client_id', client_id).limit(1).execute()

                if not intel_result.data:
                    remaining_count += 1
                    # Once we know there's at least one remaining, we can stop counting
                    if remaining_count >= 1:
                        break

            if remaining_count > 0:
                logger.info(f"Found more documents needing extraction for client {client_id}, queueing follow-up job")

                # Queue a new job via the platform RPC
                from supabase import create_client
                platform_sb = create_client(
                    settings.supabase_url,
                    settings.supabase_service_role_key
                )

                result = platform_sb.rpc('queue_client_documentsense_extraction', {
                    'p_client_id': client_id
                }).execute()

                logger.info(f"Follow-up DocumentSense job queued for client {client_id}")
            else:
                logger.info(f"All documents processed for client {client_id}, no follow-up needed")

        except Exception as e:
            logger.warning(f"Failed to check/queue follow-up DocumentSense job: {e}")


# Singleton instance
documentsense_learning_worker = DocumentSenseLearningWorker()
