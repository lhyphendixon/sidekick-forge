"""
Image Catalyst Service

AI image generation with two modes:
- Thumbnail/Promotional: Nano Banana 2 Pro (google:4@2 / Gemini 3 Pro Image) for polished marketing images
- General: FLUX.2 Dev (runware:400@1) for creative/general imagery

Supports reference images, cost tracking per-client and per-agent.
"""

import logging
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from enum import Enum

from app.config import settings
from app.services.runware_service import (
    RunWareService,
    RunWareError,
    GeneratedImage,
    get_runware_service,
)

logger = logging.getLogger(__name__)


class ImageMode(str, Enum):
    THUMBNAIL = "thumbnail"
    GENERAL = "general"


# Model configuration per mode
MODEL_CONFIG = {
    ImageMode.THUMBNAIL: {
        "model_air": "google:4@2",
        "label": "Thumbnail / Promotional",
        "allowed_dimensions": [
            # 1K Resolution
            (1024, 1024),   # 1:1 Square
            (1376, 768),    # 16:9 Landscape
            (768, 1376),    # 9:16 Portrait
            (1264, 848),    # 3:2 Landscape
            (848, 1264),    # 2:3 Portrait
            (1200, 896),    # 4:3 Landscape
            (896, 1200),    # 3:4 Portrait
            (1152, 928),    # 5:4 Landscape
            (928, 1152),    # 4:5 Portrait
            (1584, 672),    # 21:9 Ultrawide
            # 2K Resolution
            (2048, 2048),   # 1:1 Square 2K
            (2752, 1536),   # 16:9 Landscape 2K
            (1536, 2752),   # 9:16 Portrait 2K
            (2528, 1696),   # 3:2 Landscape 2K
            (1696, 2528),   # 2:3 Portrait 2K
            (2400, 1792),   # 4:3 Landscape 2K
            (1792, 2400),   # 3:4 Portrait 2K
            (2304, 1856),   # 5:4 Landscape 2K
            (1856, 2304),   # 4:5 Portrait 2K
            (3168, 1344),   # 21:9 Ultrawide 2K
        ],
        "default_width": 1024,
        "default_height": 1024,
    },
    ImageMode.GENERAL: {
        "model_air": "runware:400@1",
        "label": "General Images",
        "default_width": 1024,
        "default_height": 1024,
        "default_steps": 28,
        "default_cfg_scale": 3.5,
        "default_strength": 0.75,
    },
}


@dataclass
class ImageCatalystResult:
    """Result of an Image Catalyst generation."""
    run_id: str
    status: str  # pending, generating, complete, failed
    mode: str
    prompt: str
    image_url: Optional[str] = None
    seed: Optional[int] = None
    generation_time_ms: Optional[float] = None
    cost: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ImageCatalystService:
    """
    Service for generating images via RunWare with mode-based model selection
    and integrated cost tracking.
    """

    def __init__(self, runware_service: Optional[RunWareService] = None):
        self.runware = runware_service or get_runware_service()
        self._platform_sb = None

    def _get_platform_supabase(self):
        if self._platform_sb is None:
            from supabase import create_client
            self._platform_sb = create_client(
                settings.supabase_url,
                settings.supabase_service_role_key,
            )
        return self._platform_sb

    async def generate(
        self,
        *,
        mode: ImageMode,
        prompt: str,
        enriched_prompt: Optional[str] = None,
        client_id: str = "",
        agent_id: str,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        session_id: Optional[str] = None,
        reference_image_url: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        quality: Optional[str] = None,
        steps: Optional[int] = None,
        cfg_scale: Optional[float] = None,
        strength: Optional[float] = None,
        seed: Optional[int] = None,
    ) -> ImageCatalystResult:
        """
        Generate an image using the specified mode.

        Returns ImageCatalystResult with all generation details including cost.
        Also records the generation in image_catalyst_runs and increments
        usage tracking costs.
        """
        config = MODEL_CONFIG[mode]
        model_air = config["model_air"]

        # Resolve dimensions
        final_width = width or config["default_width"]
        final_height = height or config["default_height"]

        # Validate dimensions for thumbnail mode
        if mode == ImageMode.THUMBNAIL:
            allowed = config["allowed_dimensions"]
            if (final_width, final_height) not in allowed:
                dims_str = ", ".join(f"{w}x{h}" for w, h in allowed)
                raise ValueError(
                    f"Invalid dimensions {final_width}x{final_height} for thumbnail mode. "
                    f"Allowed: {dims_str}"
                )

        # Create the run record in database
        run_id = None
        try:
            platform_sb = self._get_platform_supabase()
            result = platform_sb.rpc("create_image_catalyst_run", {
                "p_client_id": client_id,
                "p_agent_id": agent_id,
                "p_user_id": user_id,
                "p_conversation_id": conversation_id,
                "p_session_id": session_id,
                "p_mode": mode.value,
                "p_prompt": prompt,
                "p_enriched_prompt": enriched_prompt,
                "p_model_air": model_air,
                "p_width": final_width,
                "p_height": final_height,
            }).execute()
            if result.data:
                run_id = str(result.data)
        except Exception as e:
            logger.warning(f"Failed to create image_catalyst_run record: {e}")

        if not run_id:
            run_id = str(uuid.uuid4())

        # Update status to generating
        try:
            platform_sb = self._get_platform_supabase()
            platform_sb.rpc("update_image_catalyst_status", {
                "p_run_id": run_id,
                "p_status": "generating",
            }).execute()
        except Exception:
            pass

        # Generate the image (use enriched prompt if available)
        actual_prompt = enriched_prompt or prompt
        try:
            if mode == ImageMode.THUMBNAIL:
                generated = await self.runware.generate_image_advanced(
                    prompt=actual_prompt,
                    model=model_air,
                    width=final_width,
                    height=final_height,
                    reference_images=[reference_image_url] if reference_image_url else None,
                    seed=seed,
                    include_cost=True,
                )
            else:
                generated = await self.runware.generate_image_advanced(
                    prompt=actual_prompt,
                    model=model_air,
                    width=final_width,
                    height=final_height,
                    seed_image=reference_image_url if reference_image_url else None,
                    strength=strength or config.get("default_strength", 0.75),
                    steps=steps or config.get("default_steps", 28),
                    cfg_scale=cfg_scale if cfg_scale is not None else config.get("default_cfg_scale", 3.5),
                    negative_prompt=RunWareService.DEFAULT_NEGATIVE_PROMPT,
                    seed=seed,
                    include_cost=True,
                )
        except RunWareError as e:
            # Update run as failed
            try:
                platform_sb = self._get_platform_supabase()
                platform_sb.rpc("update_image_catalyst_status", {
                    "p_run_id": run_id,
                    "p_status": "failed",
                    "p_error": str(e),
                }).execute()
            except Exception:
                pass

            return ImageCatalystResult(
                run_id=run_id,
                status="failed",
                mode=mode.value,
                prompt=prompt,
                error=str(e),
            )

        # Save result to database
        cost_value = generated.cost or 0.0
        try:
            platform_sb = self._get_platform_supabase()
            platform_sb.rpc("save_image_catalyst_result", {
                "p_run_id": run_id,
                "p_output_image_url": generated.image_url,
                "p_seed": generated.seed,
                "p_task_uuid": generated.task_uuid,
                "p_generation_time_ms": generated.generation_time_ms,
                "p_cost": float(cost_value),
            }).execute()
        except Exception as e:
            logger.warning(f"Failed to save image_catalyst_run result: {e}")

        # Track cost in agent_usage (atomic increment)
        await self._track_cost(client_id, agent_id, cost_value)

        return ImageCatalystResult(
            run_id=run_id,
            status="complete",
            mode=mode.value,
            prompt=prompt,
            image_url=generated.image_url,
            seed=generated.seed,
            generation_time_ms=generated.generation_time_ms,
            cost=cost_value,
        )

    async def _track_cost(self, client_id: str, agent_id: str, cost: float):
        """Track generation cost in agent_usage via atomic RPC."""
        if cost <= 0:
            return

        try:
            platform_sb = self._get_platform_supabase()
            result = platform_sb.rpc("increment_agent_image_cost", {
                "p_client_id": client_id,
                "p_agent_id": agent_id,
                "p_cost": float(cost),
                "p_count": 1,
            }).execute()

            if result.data:
                data = result.data[0] if isinstance(result.data, list) else result.data
                logger.info(
                    "Image cost tracked: agent=%s, cost=$%.4f, total=$%.4f, count=%d",
                    agent_id, cost,
                    float(data.get("new_cost", 0)),
                    int(data.get("new_count", 0)),
                )
        except Exception as e:
            logger.warning(
                "Failed to track image cost via RPC (cost=$%.4f, agent=%s): %s",
                cost, agent_id, e,
            )

    async def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Get a run by ID from the database."""
        try:
            platform_sb = self._get_platform_supabase()
            result = platform_sb.table("image_catalyst_runs").select("*").eq(
                "id", run_id
            ).maybe_single().execute()
            return result.data
        except Exception as e:
            logger.error(f"Failed to get image_catalyst_run {run_id}: {e}")
            return None

    async def get_client_cost_summary(self, client_id: str) -> Dict[str, Any]:
        """Get aggregated image generation costs for a client in the current billing period."""
        try:
            from datetime import date
            period_start = date.today().replace(day=1)

            platform_sb = self._get_platform_supabase()
            result = platform_sb.table("agent_usage").select(
                "agent_id, image_generation_cost, image_generation_count"
            ).eq("client_id", client_id).eq(
                "period_start", period_start.isoformat()
            ).execute()

            total_cost = sum(
                float(r.get("image_generation_cost", 0) or 0)
                for r in (result.data or [])
            )
            total_count = sum(
                int(r.get("image_generation_count", 0) or 0)
                for r in (result.data or [])
            )

            return {
                "total_cost": round(total_cost, 6),
                "total_count": total_count,
                "per_agent": [
                    {
                        "agent_id": r["agent_id"],
                        "cost": round(float(r.get("image_generation_cost", 0) or 0), 6),
                        "count": int(r.get("image_generation_count", 0) or 0),
                    }
                    for r in (result.data or [])
                    if float(r.get("image_generation_cost", 0) or 0) > 0
                ],
            }
        except Exception as e:
            logger.error(f"Failed to get client cost summary: {e}")
            return {"total_cost": 0, "total_count": 0, "per_agent": []}
