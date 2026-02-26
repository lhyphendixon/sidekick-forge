"""
Stripe Payment Integration Service

Handles Stripe Checkout Sessions, subscriptions, webhooks, and billing management.
"""

import os
import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

import stripe

logger = logging.getLogger(__name__)

# Tier configuration
TIER_CONFIG = {
    "adventurer": {
        "name": "Adventurer Tier",
        "price_cents": 4900,  # $49/month
        "description": "Perfect for solopreneurs and small projects",
    },
    "champion": {
        "name": "Champion Tier",
        "price_cents": 19900,  # $199/month
        "description": "For growing teams and businesses",
    },
    "paragon": {
        "name": "Paragon Tier",
        "price_cents": 0,  # Custom pricing
        "description": "Enterprise-grade with dedicated support",
    },
}

# Legacy exports for backward compatibility
TIER_PRICES = {tier: config["price_cents"] for tier, config in TIER_CONFIG.items()}
TIER_NAMES = {tier: config["name"] for tier, config in TIER_CONFIG.items()}


class StripeService:
    """Service for handling Stripe payment operations"""

    def __init__(self):
        self._initialized = False
        self._publishable_key = None
        self._webhook_secret = None
        self._price_ids = {}  # Cache for Stripe Price IDs

    def initialize(self, secret_key: Optional[str] = None):
        """Initialize Stripe with API keys"""
        if secret_key:
            stripe.api_key = secret_key
        else:
            stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

        if not stripe.api_key:
            raise ValueError("STRIPE_SECRET_KEY environment variable not set")

        self._publishable_key = os.getenv("STRIPE_PUBLISHABLE_KEY")
        self._webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
        self._initialized = True
        logger.info("Stripe service initialized")

    def _ensure_initialized(self):
        if not self._initialized:
            self.initialize()

    @property
    def publishable_key(self) -> str:
        """Get the Stripe publishable key for frontend use"""
        return self._publishable_key or os.getenv("STRIPE_PUBLISHABLE_KEY", "")

    def _get_or_create_price(self, tier: str) -> str:
        """
        Get or create a Stripe Price for a tier.
        Uses product metadata to find existing products.
        """
        self._ensure_initialized()

        # Check cache first
        if tier in self._price_ids:
            return self._price_ids[tier]

        config = TIER_CONFIG.get(tier)
        if not config or config["price_cents"] <= 0:
            raise ValueError(f"Invalid tier for pricing: {tier}")

        # Search for existing product by metadata
        products = stripe.Product.search(
            query=f"metadata['tier']:'{tier}' AND active:'true'"
        )

        if products.data:
            # Found existing product, get its default price
            product = products.data[0]
            if product.default_price:
                self._price_ids[tier] = product.default_price
                return product.default_price

            # Product exists but no default price, create one
            price = stripe.Price.create(
                product=product.id,
                unit_amount=config["price_cents"],
                currency="usd",
                recurring={"interval": "month"},
            )
            # Update product with default price
            stripe.Product.modify(product.id, default_price=price.id)
            self._price_ids[tier] = price.id
            return price.id

        # Create new product and price
        product = stripe.Product.create(
            name=f"Sidekick Forge - {config['name']}",
            description=config["description"],
            metadata={"tier": tier},
        )

        price = stripe.Price.create(
            product=product.id,
            unit_amount=config["price_cents"],
            currency="usd",
            recurring={"interval": "month"},
        )

        # Set as default price
        stripe.Product.modify(product.id, default_price=price.id)

        self._price_ids[tier] = price.id
        logger.info(f"Created Stripe product/price for tier {tier}: {price.id}")
        return price.id

    async def create_checkout_session(
        self,
        tier: str,
        email: str,
        first_name: str,
        last_name: str,
        company: Optional[str] = None,
        success_url: str = None,
        cancel_url: str = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str]:
        """
        Create a Stripe Checkout Session for subscription purchase.

        Returns:
            Tuple of (checkout_session_id, checkout_url)
        """
        self._ensure_initialized()

        config = TIER_CONFIG.get(tier)
        if not config or config["price_cents"] <= 0:
            raise ValueError(f"Invalid tier for checkout: {tier}")

        # Get or create the price for this tier
        price_id = self._get_or_create_price(tier)

        # Build metadata for webhook processing
        session_metadata = {
            "tier": tier,
            "first_name": first_name,
            "last_name": last_name,
            "company": company or "",
        }
        if metadata:
            session_metadata.update(metadata)

        domain = os.getenv("DOMAIN_NAME", "https://sidekickforge.com")
        if not domain.startswith("http"):
            domain = f"https://{domain}"

        # Create subscription checkout session
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price": price_id,
                "quantity": 1,
            }],
            mode="subscription",  # Subscription mode for recurring billing
            customer_email=email,
            success_url=success_url or f"{domain}/checkout/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=cancel_url or f"{domain}/checkout?canceled=true",
            metadata=session_metadata,
            subscription_data={
                "metadata": session_metadata,
            },
            allow_promotion_codes=True,
            billing_address_collection="auto",
        )

        logger.info(f"Created Stripe checkout session {session.id} for {email} - {tier}")
        return session.id, session.url

    async def retrieve_session(self, session_id: str) -> Dict[str, Any]:
        """Retrieve a checkout session by ID"""
        self._ensure_initialized()

        session = stripe.checkout.Session.retrieve(
            session_id,
            expand=["customer", "subscription"]
        )
        return {
            "id": session.id,
            "status": session.status,
            "payment_status": session.payment_status,
            "customer_email": session.customer_email,
            "customer_id": session.customer if isinstance(session.customer, str) else (session.customer.id if session.customer else None),
            "subscription_id": session.subscription.id if session.subscription else None,
            "amount_total": session.amount_total,
            "currency": session.currency,
            "metadata": dict(session.metadata) if session.metadata else {},
        }

    async def get_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """Get subscription details"""
        self._ensure_initialized()

        subscription = stripe.Subscription.retrieve(subscription_id)
        return {
            "id": subscription.id,
            "status": subscription.status,
            "current_period_start": datetime.fromtimestamp(subscription.current_period_start),
            "current_period_end": datetime.fromtimestamp(subscription.current_period_end),
            "cancel_at_period_end": subscription.cancel_at_period_end,
            "canceled_at": datetime.fromtimestamp(subscription.canceled_at) if subscription.canceled_at else None,
            "customer_id": subscription.customer,
        }

    async def create_customer_portal_session(
        self,
        customer_id: str,
        return_url: str = None,
    ) -> str:
        """
        Create a Stripe Customer Portal session for self-service billing management.
        Returns the portal URL.
        """
        self._ensure_initialized()

        domain = os.getenv("DOMAIN_NAME", "https://sidekickforge.com")
        if not domain.startswith("http"):
            domain = f"https://{domain}"

        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url or f"{domain}/admin/settings",
        )
        return session.url

    async def cancel_subscription(
        self,
        subscription_id: str,
        cancel_immediately: bool = False,
    ) -> Dict[str, Any]:
        """
        Cancel a subscription.
        By default, cancels at end of billing period.
        """
        self._ensure_initialized()

        if cancel_immediately:
            subscription = stripe.Subscription.cancel(subscription_id)
        else:
            subscription = stripe.Subscription.modify(
                subscription_id,
                cancel_at_period_end=True,
            )

        return {
            "id": subscription.id,
            "status": subscription.status,
            "cancel_at_period_end": subscription.cancel_at_period_end,
            "canceled_at": datetime.fromtimestamp(subscription.canceled_at) if subscription.canceled_at else None,
        }

    async def reactivate_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """
        Reactivate a subscription that was set to cancel at period end.
        """
        self._ensure_initialized()

        subscription = stripe.Subscription.modify(
            subscription_id,
            cancel_at_period_end=False,
        )

        return {
            "id": subscription.id,
            "status": subscription.status,
            "cancel_at_period_end": subscription.cancel_at_period_end,
        }

    def construct_webhook_event(self, payload: bytes, sig_header: str) -> stripe.Event:
        """
        Construct and verify a webhook event from Stripe.

        Args:
            payload: Raw request body bytes
            sig_header: Stripe-Signature header value

        Returns:
            Verified Stripe Event object
        """
        self._ensure_initialized()

        webhook_secret = self._webhook_secret or os.getenv("STRIPE_WEBHOOK_SECRET")
        if not webhook_secret:
            raise ValueError("STRIPE_WEBHOOK_SECRET not configured")

        return stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
        )

    async def update_client_subscription_status(
        self,
        client_id: str,
        subscription: Dict[str, Any],
        supabase_client=None,
    ) -> None:
        """
        Update client record with subscription status from Stripe.
        """
        if supabase_client is None:
            from app.services.client_connection_manager import get_connection_manager
            supabase_client = get_connection_manager().platform_client

        update_data = {
            "stripe_subscription_id": subscription.get("id"),
            "subscription_status": subscription.get("status"),
            "subscription_current_period_start": subscription.get("current_period_start").isoformat() if subscription.get("current_period_start") else None,
            "subscription_current_period_end": subscription.get("current_period_end").isoformat() if subscription.get("current_period_end") else None,
            "subscription_cancel_at_period_end": subscription.get("cancel_at_period_end", False),
            "subscription_canceled_at": subscription.get("canceled_at").isoformat() if subscription.get("canceled_at") else None,
        }

        supabase_client.table("clients").update(update_data).eq("id", client_id).execute()
        logger.info(f"Updated client {client_id} subscription status: {subscription.get('status')}")

    async def handle_subscription_deleted(
        self,
        subscription_id: str,
        supabase_client=None,
    ) -> None:
        """
        Handle subscription deletion/cancellation - update status.
        """
        if supabase_client is None:
            from app.services.client_connection_manager import get_connection_manager
            supabase_client = get_connection_manager().platform_client

        # Find client by subscription ID
        result = supabase_client.table("clients").select("id, name, owner_user_id").eq(
            "stripe_subscription_id", subscription_id
        ).single().execute()

        if not result.data:
            logger.warning(f"No client found for canceled subscription {subscription_id}")
            return

        client_id = result.data["id"]
        client_name = result.data["name"]

        # Update client status
        supabase_client.table("clients").update({
            "subscription_status": "canceled",
            "subscription_canceled_at": datetime.utcnow().isoformat(),
        }).eq("id", client_id).execute()

        logger.info(f"Client {client_name} ({client_id}) subscription canceled")

    async def handle_subscription_updated(
        self,
        subscription_data: Dict[str, Any],
        supabase_client=None,
    ) -> None:
        """
        Handle subscription updates from webhook.
        """
        if supabase_client is None:
            from app.services.client_connection_manager import get_connection_manager
            supabase_client = get_connection_manager().platform_client

        subscription_id = subscription_data.get("id")

        # Find client by subscription ID
        result = supabase_client.table("clients").select("id").eq(
            "stripe_subscription_id", subscription_id
        ).single().execute()

        if not result.data:
            logger.warning(f"No client found for subscription {subscription_id}")
            return

        client_id = result.data["id"]

        # Update subscription details
        update_data = {
            "subscription_status": subscription_data.get("status"),
            "subscription_cancel_at_period_end": subscription_data.get("cancel_at_period_end", False),
        }

        if subscription_data.get("current_period_start"):
            update_data["subscription_current_period_start"] = datetime.fromtimestamp(
                subscription_data["current_period_start"]
            ).isoformat()
        if subscription_data.get("current_period_end"):
            update_data["subscription_current_period_end"] = datetime.fromtimestamp(
                subscription_data["current_period_end"]
            ).isoformat()
        if subscription_data.get("canceled_at"):
            update_data["subscription_canceled_at"] = datetime.fromtimestamp(
                subscription_data["canceled_at"]
            ).isoformat()

        supabase_client.table("clients").update(update_data).eq("id", client_id).execute()
        logger.info(f"Updated subscription status for client {client_id}: {subscription_data.get('status')}")


# Singleton instance
stripe_service = StripeService()
