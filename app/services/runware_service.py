"""
RunWare.ai Image Generation Service

Provides async image generation using RunWare's FLUX models.
Used for Ken Burns style visual generation during voice conversations.
"""
import asyncio
import logging
import time
import uuid
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class GeneratedImage:
    """Result of an image generation request"""
    image_url: str
    prompt: str
    seed: int
    generation_time_ms: float
    task_uuid: str
    cost: Optional[float] = None  # Dollar amount from Runware API when includeCost=True


class RunWareError(Exception):
    """Custom exception for RunWare API errors"""
    def __init__(self, message: str, status_code: Optional[int] = None, details: Optional[Dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}


class RunWareService:
    """
    Service for generating images via RunWare.ai API.

    Uses FLUX.2 klein 9B model by default for fast, high-quality image generation.
    Optimized for real-time conversational use cases.
    """

    BASE_URL = "https://api.runware.ai/v1"

    # Default settings optimized for conversational image generation
    DEFAULT_WIDTH = 576
    DEFAULT_HEIGHT = 1024  # 9:16 portrait aspect ratio
    DEFAULT_STEPS = 4  # FLUX klein is optimized for low step counts
    DEFAULT_SCHEDULER = "FlowMatchEulerDiscreteScheduler"
    DEFAULT_CFG_SCALE = 1.0  # FLUX klein works best with CFG ~1

    # Standard negative prompt for quality
    DEFAULT_NEGATIVE_PROMPT = (
        "blurry, low quality, low resolution, pixelated, "
        "watermark, text, logo, signature, username, "
        "deformed, distorted, disfigured, bad anatomy, "
        "poorly drawn, amateur"
    )

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the RunWare service.

        Args:
            api_key: RunWare API key. If not provided, uses settings.runware_api_key
        """
        self.api_key = api_key or settings.runware_api_key
        if not self.api_key:
            logger.warning("RunWare API key not configured - image generation will fail")

        self.default_model = settings.runware_default_model
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(120.0, connect=10.0),  # 120s total, 10s connect (GPT Image 1.5 can be slow)
            )
        return self._client

    async def close(self):
        """Close the HTTP client"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def generate_image(
        self,
        prompt: str,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        model: Optional[str] = None,
        negative_prompt: Optional[str] = None,
        steps: int = DEFAULT_STEPS,
        cfg_scale: float = DEFAULT_CFG_SCALE,
        seed: Optional[int] = None,
        style_preset: Optional[str] = None,
    ) -> GeneratedImage:
        """
        Generate an image using RunWare.ai API.

        Args:
            prompt: Text description of the image to generate
            width: Image width in pixels (default 1024)
            height: Image height in pixels (default 576 for 16:9)
            model: Model ID to use (default: FLUX.2 klein 9B)
            negative_prompt: Things to avoid in the image
            steps: Number of inference steps (default 4 for FLUX klein)
            cfg_scale: Classifier-free guidance scale (default 1.0)
            seed: Random seed for reproducibility
            style_preset: Optional style modifier to append to prompt

        Returns:
            GeneratedImage with URL and metadata

        Raises:
            RunWareError: If API call fails
        """
        if not self.api_key:
            raise RunWareError("RunWare API key not configured")

        start_time = time.time()

        # Enhance prompt with style if provided
        full_prompt = prompt
        if style_preset:
            full_prompt = f"{prompt}, {style_preset} style"

        # Generate unique task ID
        task_uuid = str(uuid.uuid4())

        # Build request payload
        payload = {
            "taskType": "imageInference",
            "taskUUID": task_uuid,
            "model": model or self.default_model,
            "positivePrompt": full_prompt,
            "negativePrompt": negative_prompt or self.DEFAULT_NEGATIVE_PROMPT,
            "width": width,
            "height": height,
            "steps": steps,
            "CFGScale": cfg_scale,
            "scheduler": self.DEFAULT_SCHEDULER,
            "numberResults": 1,
            "outputType": "URL",
            "outputFormat": "WEBP",
        }

        if seed is not None:
            payload["seed"] = seed

        logger.info(f"RunWare: Generating image for prompt: {prompt[:100]}...")
        logger.debug(f"RunWare request payload: {payload}")

        try:
            client = await self._get_client()

            # RunWare uses a task-based API
            response = await client.post(
                "/tasks",
                json=[payload],  # API accepts array of tasks
            )

            if response.status_code != 200:
                error_text = response.text
                logger.error(f"RunWare API error: {response.status_code} - {error_text}")
                raise RunWareError(
                    f"RunWare API returned {response.status_code}",
                    status_code=response.status_code,
                    details={"response": error_text}
                )

            result = response.json()
            logger.debug(f"RunWare response: {result}")

            # Parse response
            if not result or not isinstance(result, dict):
                raise RunWareError("Invalid response format from RunWare API")

            # Check for errors in response
            if "errors" in result and result["errors"]:
                error_msg = result["errors"][0].get("message", "Unknown error")
                raise RunWareError(f"RunWare generation failed: {error_msg}")

            # Extract image data from response
            data = result.get("data", [])
            if not data:
                raise RunWareError("No image data in RunWare response")

            image_data = data[0]
            image_url = image_data.get("imageURL")

            if not image_url:
                raise RunWareError("No image URL in RunWare response")

            generation_time_ms = (time.time() - start_time) * 1000

            generated_image = GeneratedImage(
                image_url=image_url,
                prompt=full_prompt,
                seed=image_data.get("seed", 0),
                generation_time_ms=generation_time_ms,
                task_uuid=task_uuid,
            )

            logger.info(
                f"RunWare: Image generated in {generation_time_ms:.0f}ms - {image_url[:80]}..."
            )

            return generated_image

        except httpx.TimeoutException as e:
            logger.error(f"RunWare API timeout: {e}")
            raise RunWareError("RunWare API request timed out") from e
        except httpx.HTTPError as e:
            logger.error(f"RunWare HTTP error: {e}")
            raise RunWareError(f"RunWare HTTP error: {str(e)}") from e

    async def generate_scene_image(
        self,
        scene_description: str,
        context: Optional[str] = None,
        style: str = "cinematic",
    ) -> GeneratedImage:
        """
        Generate a scene image optimized for Ken Burns display.

        This is a convenience method that enhances the prompt for
        better visual storytelling results.

        Args:
            scene_description: Description of the scene to visualize
            context: Optional conversation context for better relevance
            style: Visual style (cinematic, futuristic, realistic, artistic)

        Returns:
            GeneratedImage with URL and metadata
        """
        # Style presets optimized for conversational visualization
        style_modifiers = {
            "cinematic": "cinematic lighting, dramatic composition, film still, professional photography",
            "futuristic": "futuristic, sci-fi, advanced technology, sleek design, neon accents",
            "realistic": "photorealistic, high detail, natural lighting, professional photograph",
            "artistic": "artistic interpretation, painterly, vibrant colors, creative composition",
            "documentary": "documentary style, realistic, informative, clear visualization",
        }

        style_modifier = style_modifiers.get(style, style_modifiers["cinematic"])

        # Build enhanced prompt
        enhanced_prompt = f"{scene_description}, {style_modifier}, high quality, detailed"

        if context:
            # Add context hint (but keep it brief to not overwhelm the model)
            enhanced_prompt = f"{enhanced_prompt}, {context[:50]}"

        return await self.generate_image(
            prompt=enhanced_prompt,
            negative_prompt=self.DEFAULT_NEGATIVE_PROMPT,
        )

    async def generate_image_advanced(
        self,
        prompt: str,
        model: str,
        width: int = 1024,
        height: int = 1024,
        *,
        # GPT Image 1.5 specific
        reference_images: Optional[List[str]] = None,
        quality: Optional[str] = None,
        # FLUX specific
        seed_image: Optional[str] = None,
        strength: Optional[float] = None,
        steps: Optional[int] = None,
        cfg_scale: Optional[float] = None,
        # Common
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None,
        include_cost: bool = True,
    ) -> GeneratedImage:
        """
        Generate an image with advanced model-specific options and cost tracking.

        Supports GPT Image 1.5 (openai:4@1) with referenceImages and quality tiers,
        and FLUX.2 Dev (runware:400@1) with seedImage img2img.

        Args:
            prompt: Text description of the image to generate
            model: Runware AIR model ID (e.g. 'openai:4@1', 'runware:400@1')
            width: Image width in pixels
            height: Image height in pixels
            reference_images: List of image URLs for GPT model reference
            quality: Quality tier for GPT model ('low', 'medium', 'high')
            seed_image: Image URL for FLUX img2img
            strength: Img2img strength for FLUX (0.0-1.0)
            steps: Inference steps for FLUX
            cfg_scale: CFG scale for FLUX
            negative_prompt: Things to avoid in the image
            seed: Random seed for reproducibility
            include_cost: Whether to request cost data from API

        Returns:
            GeneratedImage with URL, metadata, and cost

        Raises:
            RunWareError: If API call fails
        """
        if not self.api_key:
            raise RunWareError("RunWare API key not configured")

        start_time = time.time()
        task_uuid = str(uuid.uuid4())

        # Base payload
        payload: Dict[str, Any] = {
            "taskType": "imageInference",
            "taskUUID": task_uuid,
            "model": model,
            "positivePrompt": prompt,
            "width": width,
            "height": height,
            "numberResults": 1,
            "outputType": "URL",
            "outputFormat": "WEBP",
            "includeCost": include_cost,
        }

        if negative_prompt:
            payload["negativePrompt"] = negative_prompt

        if seed is not None:
            payload["seed"] = seed

        # Model-specific configuration
        is_gpt_model = model.startswith("openai:")

        if is_gpt_model:
            # GPT Image 1.5 uses referenceImages and providerSettings
            if reference_images:
                payload["referenceImages"] = reference_images
            if quality:
                payload["providerSettings"] = {
                    "openai": {"quality": quality}
                }
        else:
            # FLUX.2 Dev / other FLUX models use seedImage + strength
            if seed_image:
                payload["seedImage"] = seed_image
                payload["strength"] = strength or 0.75
            if steps:
                payload["steps"] = steps
            if cfg_scale is not None:
                payload["CFGScale"] = cfg_scale
            payload["scheduler"] = self.DEFAULT_SCHEDULER

        logger.info(f"RunWare advanced: Generating {model} image for prompt: {prompt[:100]}...")
        logger.debug(f"RunWare advanced payload: {payload}")

        try:
            client = await self._get_client()
            response = await client.post("/tasks", json=[payload])

            if response.status_code != 200:
                error_text = response.text
                logger.error(f"RunWare API error: {response.status_code} - {error_text}")
                raise RunWareError(
                    f"RunWare API returned {response.status_code}",
                    status_code=response.status_code,
                    details={"response": error_text}
                )

            result = response.json()
            logger.debug(f"RunWare advanced response: {result}")

            if not result or not isinstance(result, dict):
                raise RunWareError("Invalid response format from RunWare API")

            if "errors" in result and result["errors"]:
                error_msg = result["errors"][0].get("message", "Unknown error")
                raise RunWareError(f"RunWare generation failed: {error_msg}")

            data = result.get("data", [])
            if not data:
                raise RunWareError("No image data in RunWare response")

            image_data = data[0]
            image_url = image_data.get("imageURL")

            if not image_url:
                raise RunWareError("No image URL in RunWare response")

            generation_time_ms = (time.time() - start_time) * 1000
            cost_value = image_data.get("cost")

            generated_image = GeneratedImage(
                image_url=image_url,
                prompt=prompt,
                seed=image_data.get("seed", 0),
                generation_time_ms=generation_time_ms,
                task_uuid=task_uuid,
                cost=cost_value,
            )

            logger.info(
                f"RunWare advanced: Image generated in {generation_time_ms:.0f}ms, "
                f"cost=${cost_value or 0:.4f} - {image_url[:80]}..."
            )

            return generated_image

        except httpx.TimeoutException as e:
            logger.error(f"RunWare API timeout: {e}")
            raise RunWareError("RunWare API request timed out") from e
        except httpx.HTTPError as e:
            logger.error(f"RunWare HTTP error: {e}")
            raise RunWareError(f"RunWare HTTP error: {str(e)}") from e


# Singleton instance
_runware_service: Optional[RunWareService] = None


def get_runware_service() -> RunWareService:
    """Get or create the singleton RunWare service instance"""
    global _runware_service
    if _runware_service is None:
        _runware_service = RunWareService()
    return _runware_service
