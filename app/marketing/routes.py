"""
Marketing site routes for Sidekick Forge
Handles homepage, pricing, features, about, contact, and signup
"""
from fastapi import APIRouter, Request, Form, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from datetime import datetime, timedelta
import logging
import secrets
import uuid
from typing import Optional
from supabase import create_client
from app.config import settings
from app.services.mailjet_service import mailjet_service
from app.services.mailchimp_service import mailchimp_service
from app.services.stripe_service import stripe_service, TIER_PRICES as STRIPE_TIER_PRICES
from app.utils.helpers import generate_slug
import stripe

logger = logging.getLogger(__name__)

router = APIRouter(tags=["marketing"])

# Get Supabase client for database operations (platform database)
supabase = create_client(
    settings.supabase_url,
    settings.supabase_service_role_key  # Use service role for write access
)

# Templates
templates = Jinja2Templates(directory="app/templates")

@router.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    """Homepage - Hero's journey themed landing page"""
    return templates.TemplateResponse(
        "marketing/home.html",
        {
            "request": request,
            "current_year": datetime.now().year
        }
    )

@router.get("/pricing", response_class=HTMLResponse)
async def pricing(request: Request):
    """Pricing page with plan comparison"""
    return templates.TemplateResponse(
        "marketing/pricing.html",
        {
            "request": request,
            "current_year": datetime.now().year
        }
    )

@router.get("/features", response_class=HTMLResponse)
async def features(request: Request):
    """Features showcase page"""
    return templates.TemplateResponse(
        "marketing/features.html",
        {
            "request": request,
            "current_year": datetime.now().year
        }
    )

@router.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    """About us page"""
    return templates.TemplateResponse(
        "marketing/about.html",
        {
            "request": request,
            "current_year": datetime.now().year
        }
    )

@router.get("/contact", response_class=HTMLResponse)
async def contact(request: Request):
    """Contact page with form"""
    return templates.TemplateResponse(
        "marketing/contact.html",
        {
            "request": request,
            "current_year": datetime.now().year
        }
    )

@router.get("/signup", response_class=HTMLResponse)
async def signup(request: Request):
    """Early access signup page"""
    return templates.TemplateResponse(
        "marketing/signup.html",
        {
            "request": request,
            "current_year": datetime.now().year
        }
    )


@router.get("/checkout", response_class=HTMLResponse)
async def checkout(request: Request):
    """Checkout page with tier selection"""
    # Get plan from query params (from pricing page links)
    selected_tier = request.query_params.get("plan", "champion")
    if selected_tier not in ["adventurer", "champion", "paragon"]:
        selected_tier = "champion"

    # Check if user was redirected back from canceled payment
    canceled = request.query_params.get("canceled") == "true"

    return templates.TemplateResponse(
        "marketing/checkout.html",
        {
            "request": request,
            "current_year": datetime.now().year,
            "selected_tier": selected_tier,
            "canceled": canceled
        }
    )


# API endpoints for form submissions

@router.post("/api/signup/early-access")
async def submit_early_access(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    business: str = Form(None),
    stage: str = Form(...),
    use_case: str = Form(...)
):
    """Handle early access signup form submission"""
    try:
        # Extract request metadata
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        referrer = request.headers.get("referer")
        
        # Save to database
        submission_data = {
            "full_name": name,
            "email": email.lower().strip(),
            "business_name": business,
            "stage": stage,
            "use_case": use_case,
            "submission_type": "early_access",
            "status": "new",
            "priority": "normal",
            "ip_address": client_ip,
            "user_agent": user_agent,
            "referrer": referrer
        }
        
        result = supabase.table("contact_submissions").insert(submission_data).execute()
        
        logger.info(f"✅ Early access signup saved: {name} <{email}> - Stage: {stage}")
        logger.info(f"   Submission ID: {result.data[0]['id'] if result.data else 'unknown'}")

        submission_record = dict(submission_data)
        if result.data:
            submission_record.update(result.data[0])
        try:
            await mailjet_service.send_submission_notification("early_access", submission_record)
        except Exception:
            logger.exception("Failed to send Mailjet notification for early access signup")
        
        return HTMLResponse(
            content="""
            <div class="bg-green-500/10 border border-green-500/30 rounded-lg p-4 mb-4">
                <p class="text-green-400 font-medium">🎉 Success! You're on the list!</p>
                <p class="text-green-300 text-sm mt-2">
                    We'll be in touch soon with your early access invitation. 
                    Check your email for next steps.
                </p>
            </div>
            """,
            status_code=200
        )
    except Exception as e:
        logger.error(f"❌ Error processing signup: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return HTMLResponse(
            content="""
            <div class="bg-red-500/10 border border-red-500/30 rounded-lg p-4 mb-4">
                <p class="text-red-400 font-medium">Error submitting form</p>
                <p class="text-red-300 text-sm mt-2">
                    Please try again or contact us directly at hello@sidekickforge.com
                </p>
            </div>
            """,
            status_code=500
        )


@router.post("/api/newsletter/subscribe")
async def subscribe_newsletter(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
):
    """Handle newsletter email capture form submission."""
    try:
        email = email.lower().strip()
        name = name.strip()

        # Split name into first/last
        parts = name.split(None, 1)
        first_name = parts[0] if parts else name
        last_name = parts[1] if len(parts) > 1 else ""

        # Subscribe to Mailchimp with "newsletter" tag and double opt-in
        mailchimp_service.subscribe(
            email=email,
            first_name=first_name,
            last_name=last_name,
            tags=["newsletter"],
            status="pending",  # Double opt-in for newsletter signups
        )

        logger.info(f"Newsletter signup: {name} <{email}>")

        return HTMLResponse(
            content="""
            <div class="flex items-center justify-center gap-3 py-4">
                <svg class="w-8 h-8 text-green-400 shrink-0" fill="currentColor" viewBox="0 0 20 20">
                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"></path>
                </svg>
                <div class="text-left">
                    <p class="text-white font-semibold text-lg">You're in!</p>
                    <p class="text-gray-400 text-sm">Check your email to confirm your subscription.</p>
                </div>
            </div>
            """,
            status_code=200,
        )
    except Exception as e:
        logger.error(f"Newsletter signup error: {e}")
        return HTMLResponse(
            content="""
            <div class="flex items-center justify-center gap-3 py-4">
                <svg class="w-8 h-8 text-red-400 shrink-0" fill="currentColor" viewBox="0 0 20 20">
                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"></path>
                </svg>
                <div class="text-left">
                    <p class="text-white font-semibold">Something went wrong</p>
                    <p class="text-gray-400 text-sm">Please try again or contact team@sidekickforge.com</p>
                </div>
            </div>
            """,
            status_code=500,
        )


@router.post("/api/contact/submit")
async def submit_contact(
    request: Request,
    first_name: Optional[str] = Form(None),
    last_name: Optional[str] = Form(None),
    name: Optional[str] = Form(None),
    subject: Optional[str] = Form(None),
    company: Optional[str] = Form(None),
    email: str = Form(...),
    phone_number: Optional[str] = Form(None),
    message: str = Form(...)
):
    """Handle contact form submission"""
    try:
        def _clean(value: Optional[str]) -> Optional[str]:
            return value.strip() if value and value.strip() else None

        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        referrer = request.headers.get("referer")

        resolved_first = _clean(first_name)
        resolved_last = _clean(last_name)

        if not (resolved_first or resolved_last):
            raw_name = _clean(name)
            if raw_name:
                parts = raw_name.split(None, 1)
                resolved_first = parts[0]
                resolved_last = parts[1] if len(parts) > 1 else None
            else:
                resolved_first = (email.split("@")[0] if email else "Website").strip()

        full_name = " ".join(part for part in [resolved_first, resolved_last] if part).strip()
        if not full_name:
            full_name = _clean(name) or "Website Visitor"

        submission_data = {
            "first_name": resolved_first,
            "last_name": resolved_last,
            "full_name": full_name,
            "email": email.lower().strip(),
            "company": _clean(company),
            "phone_number": _clean(phone_number),
            "message": message,
            "submission_type": "contact",
            "status": "new",
            "priority": "normal",
            "ip_address": client_ip,
            "user_agent": user_agent,
            "referrer": referrer,
        }

        cleaned_subject = _clean(subject)
        if cleaned_subject:
            submission_data["notes"] = cleaned_subject

        result = supabase.table("contact_submissions").insert(submission_data).execute()

        logger.info(f"✅ Contact form saved: {full_name} <{email}>")
        logger.info(f"   Submission ID: {result.data[0]['id'] if result.data else 'unknown'}")
        logger.info(f"   Message preview: {message[:100]}...")

        submission_record = dict(submission_data)
        if result.data:
            submission_record.update(result.data[0])
        submission_record["subject"] = cleaned_subject
        submission_record["raw_name"] = _clean(name)

        try:
            await mailjet_service.send_submission_notification("contact", submission_record)
        except Exception:
            logger.exception("Failed to send Mailjet notification for contact submission")

        return HTMLResponse(
            content="""
            <div class="bg-green-500/10 border border-green-500/30 rounded-lg p-4 mb-4">
                <p class="text-green-400 font-medium">✅ Message sent!</p>
                <p class="text-green-300 text-sm mt-2">
                    Thanks for reaching out. We'll get back to you within 24 hours.
                </p>
            </div>
            """,
            status_code=200
        )
    except Exception as e:
        logger.error(f"❌ Error processing contact form: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return HTMLResponse(
            content="""
            <div class="bg-red-500/10 border border-red-500/30 rounded-lg p-4 mb-4">
                <p class="text-red-400 font-medium">Error sending message</p>
                <p class="text-red-300 text-sm mt-2">
                    Please try again or email us directly at hello@sidekickforge.com
                </p>
            </div>
            """,
            status_code=500
        )

@router.get("/api/demo/form")
async def demo_form():
    """Return demo booking form modal"""
    return HTMLResponse(
        content="""
        <div class="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4"
             x-data="{ open: true }"
             x-show="open"
             @click.self="open = false; document.getElementById('demo-modal').innerHTML = ''">
            <div class="bg-brand-dark-elevated border border-gray-800 rounded-2xl p-8 max-w-md w-full">
                <h3 class="text-2xl font-bold text-white mb-4">Book a Demo</h3>
                <form hx-post="/api/demo/submit" 
                      hx-target="#demo-response" 
                      hx-swap="innerHTML"
                      class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium text-gray-300 mb-2">Name *</label>
                        <input type="text" name="name" required
                               class="w-full bg-brand-dark border border-gray-700 text-white rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-brand-teal">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-300 mb-2">Email *</label>
                        <input type="email" name="email" required
                               class="w-full bg-brand-dark border border-gray-700 text-white rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-brand-teal">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-300 mb-2">Preferred Time</label>
                        <input type="text" name="time" placeholder="e.g., Next week, afternoons"
                               class="w-full bg-brand-dark border border-gray-700 text-white rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-brand-teal">
                    </div>
                    <div id="demo-response"></div>
                    <button type="submit" 
                            class="w-full bg-brand-teal hover:bg-brand-teal/90 text-white px-6 py-3 rounded-lg font-semibold transition">
                        Schedule Demo
                    </button>
                </form>
                <button @click="open = false; document.getElementById('demo-modal').innerHTML = ''" 
                        class="mt-4 text-gray-400 hover:text-white transition">
                    Cancel
                </button>
            </div>
        </div>
        """
    )

@router.post("/api/demo/submit")
async def submit_demo(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    time: str = Form(None)
):
    """Handle demo booking submission"""
    try:
        # Extract request metadata
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        referrer = request.headers.get("referer")
        
        # Save to database
        submission_data = {
            "full_name": name,
            "email": email.lower().strip(),
            "message": f"Preferred time: {time}" if time else "No preferred time specified",
            "submission_type": "demo",
            "status": "new",
            "priority": "high",  # Demo requests are higher priority
            "ip_address": client_ip,
            "user_agent": user_agent,
            "referrer": referrer
        }
        
        result = supabase.table("contact_submissions").insert(submission_data).execute()
        
        logger.info(f"✅ Demo request saved: {name} <{email}> - Preferred time: {time}")
        logger.info(f"   Submission ID: {result.data[0]['id'] if result.data else 'unknown'}")

        submission_record = dict(submission_data)
        if result.data:
            submission_record.update(result.data[0])
        try:
            await mailjet_service.send_submission_notification("demo", submission_record)
        except Exception:
            logger.exception("Failed to send Mailjet notification for demo request")
        
        return HTMLResponse(
            content="""
            <div class="bg-green-500/10 border border-green-500/30 rounded-lg p-4">
                <p class="text-green-400 font-medium">✅ Demo scheduled!</p>
                <p class="text-green-300 text-sm mt-2">
                    We'll send you calendar invites shortly. Check your email!
                </p>
            </div>
            """,
            status_code=200
        )
    except Exception as e:
        logger.error(f"❌ Error processing demo request: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return HTMLResponse(
            content="""
            <div class="bg-red-500/10 border border-red-500/30 rounded-lg p-4">
                <p class="text-red-400 font-medium">Error</p>
                <p class="text-red-300 text-sm mt-2">Please try again or email us.</p>
            </div>
            """,
            status_code=500
        )


# ============================================================
# CHECKOUT ENDPOINTS
# ============================================================

TIER_PRICES = {
    "adventurer": 49,
    "champion": 199,
    "paragon": 0,  # Custom pricing
}

TIER_NAMES = {
    "adventurer": "Adventurer",
    "champion": "Champion",
    "paragon": "Paragon",
}

TIER_HOSTING = {
    "adventurer": "shared",
    "champion": "dedicated",
    "paragon": "dedicated",
}

# Valid coupon codes and their discounts (staging/testing only)
VALID_COUPONS = {
    "STAGING100": {"discount_percent": 100, "message": "100% off - Free checkout!", "staging_only": True},
    "FOUNDER50": {"discount_percent": 50, "message": "50% off - Founder's discount!", "staging_only": False},
    "BETA25": {"discount_percent": 25, "message": "25% off - Beta tester discount!", "staging_only": False},
}


# ============================================================
# COUPON VALIDATION ENDPOINT
# ============================================================

@router.post("/api/coupon/validate")
async def validate_coupon(request: Request):
    """
    Validate a coupon code and return discount percentage.
    """
    try:
        data = await request.json()
        coupon_code = data.get("coupon_code", "").strip().upper()

        if not coupon_code:
            return JSONResponse(
                content={"valid": False, "error": "Please enter a coupon code."},
                status_code=400
            )

        coupon = VALID_COUPONS.get(coupon_code)

        if not coupon:
            return JSONResponse(
                content={"valid": False, "error": "Invalid coupon code."},
                status_code=400
            )

        # Check if coupon is staging-only and we're not on staging
        is_staging = settings.app_env == "staging" or settings.development_mode
        if coupon.get("staging_only") and not is_staging:
            return JSONResponse(
                content={"valid": False, "error": "This coupon is not valid in production."},
                status_code=400
            )

        return JSONResponse(content={
            "valid": True,
            "discount_percent": coupon["discount_percent"],
            "message": coupon["message"]
        })

    except Exception as e:
        logger.error(f"Error validating coupon: {e}")
        return JSONResponse(
            content={"valid": False, "error": "Failed to validate coupon."},
            status_code=500
        )


# ============================================================
# FREE CHECKOUT ENDPOINT (100% discount)
# ============================================================

@router.post("/api/checkout/free")
async def process_free_checkout(
    request: Request,
    tier: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    company: Optional[str] = Form(None),
    coupon_code: Optional[str] = Form(None),
):
    """
    Process a free checkout (100% discount coupon).
    Creates user, client, and provisions the account without payment.
    """
    try:
        # Normalize email
        email = email.lower().strip()
        full_name = f"{first_name} {last_name}"

        # Validate coupon is 100% off
        coupon_code = (coupon_code or "").strip().upper()
        coupon = VALID_COUPONS.get(coupon_code)

        if not coupon or coupon.get("discount_percent") != 100:
            return JSONResponse(
                content={"error": "Invalid coupon for free checkout."},
                status_code=400
            )

        # Check staging-only restriction
        is_staging = settings.app_env == "staging" or settings.development_mode
        if coupon.get("staging_only") and not is_staging:
            return JSONResponse(
                content={"error": "This coupon is not valid in production."},
                status_code=400
            )

        # Validate tier
        if tier not in TIER_PRICES:
            tier = "champion"

        # Validate passwords match
        if password != password_confirm:
            return JSONResponse(
                content={"error": "Passwords don't match."},
                status_code=400
            )

        # Validate password length
        if len(password) < 8:
            return JSONResponse(
                content={"error": "Password must be at least 8 characters."},
                status_code=400
            )

        # Check if email already exists
        existing_user = supabase.table("profiles").select("user_id").eq("email", email).execute()
        if existing_user.data:
            return JSONResponse(
                content={"error": "Email already registered. Please login instead."},
                status_code=400
            )

        # Generate IDs
        order_number = _generate_order_number()
        user_id = str(uuid.uuid4())
        client_id = str(uuid.uuid4())
        tier_name = TIER_NAMES.get(tier, "Champion")
        hosting_type = TIER_HOSTING.get(tier, "dedicated")

        logger.info(f"Processing FREE checkout for {email} - Tier: {tier_name} - Coupon: {coupon_code}")

        # 1. Create user in Supabase Auth (requires email verification like paid accounts)
        try:
            auth_response = supabase.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": False,  # Require email verification like paid accounts
                "user_metadata": {
                    "full_name": full_name,
                    "company": company,
                    "signup_source": "free_checkout",
                    "tier": tier,
                    "coupon_code": coupon_code,
                }
            })
            if auth_response.user:
                user_id = auth_response.user.id
                logger.info(f"Created Supabase Auth user: {user_id}")
            else:
                raise Exception("Failed to create user in Supabase Auth")

            # Send verification email via resend endpoint (generate_link doesn't send emails)
            try:
                import httpx
                resend_response = httpx.post(
                    f"{settings.supabase_url}/auth/v1/resend",
                    headers={
                        "apikey": settings.supabase_anon_key,
                        "Content-Type": "application/json"
                    },
                    json={
                        "type": "signup",
                        "email": email
                    },
                    timeout=10.0
                )
                if resend_response.status_code == 200:
                    logger.info(f"Sent verification email to: {email}")
                else:
                    logger.warning(f"Resend API returned {resend_response.status_code}: {resend_response.text}")
            except Exception as email_error:
                logger.warning(f"Failed to send verification email: {email_error}")
                # Continue anyway - user can request resend

        except Exception as auth_error:
            error_str = str(auth_error)
            if "already been registered" in error_str.lower() or "already exists" in error_str.lower():
                return JSONResponse(
                    content={"error": "Email already registered. Please login instead."},
                    status_code=400
                )
            raise

        # 2. Create user profile
        profile_data = {
            "user_id": user_id,
            "email": email,
            "full_name": full_name,
        }
        supabase.table("profiles").insert(profile_data).execute()
        logger.info(f"Created profile for user: {user_id}")

        # 3. Create client (business entity)
        client_name = company or full_name
        client_data = {
            "id": client_id,
            "name": client_name,
            "tier": tier,
            "hosting_type": hosting_type,
            "max_sidekicks": 1 if tier == "adventurer" else None,
            "owner_user_id": user_id,
            "owner_email": email,
            "provisioning_status": "queued",
            "uses_platform_keys": True,  # Use Sidekick Forge Inference by default
        }
        supabase.table("clients").insert(client_data).execute()
        logger.info(f"Created client: {client_id} ({client_name})")

        # 4. Update user metadata with tenant_assignments to grant admin role
        try:
            supabase.auth.admin.update_user_by_id(
                user_id,
                {
                    "user_metadata": {
                        "full_name": full_name,
                        "company": company,
                        "signup_source": "free_checkout",
                        "tier": tier,
                        "coupon_code": coupon_code,
                        "tenant_assignments": {
                            "admin_client_ids": [client_id],
                            "subscriber_client_ids": [],
                        }
                    }
                }
            )
            logger.info(f"Updated user metadata with admin role for client: {client_id}")
        except Exception as meta_error:
            logger.warning(f"Failed to update user metadata: {meta_error}")

        # 5. Create order record
        order_data = {
            "order_number": order_number,
            "user_id": user_id,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "company": company,
            "tier": tier,
            "price_cents": 0,
            "payment_status": "completed",
            "payment_method": "coupon",
            "payment_provider": "coupon",
            "client_id": client_id,
            "paid_at": datetime.utcnow().isoformat(),
        }
        supabase.table("orders").insert(order_data).execute()
        logger.info(f"Created order: {order_number}")

        # 6. Queue provisioning job
        try:
            from app.services.onboarding.provisioning_worker import provision_client_by_tier
            await provision_client_by_tier(client_id, tier, supabase)
            logger.info(f"Queued provisioning for client: {client_id}")
        except Exception as prov_error:
            logger.error(f"Failed to queue provisioning: {prov_error}")
            # Continue anyway - provisioning can be retried

        # Add to Mailchimp audience
        try:
            mailchimp_service.subscribe(
                email=email,
                first_name=first_name,
                last_name=last_name,
                tags=[tier, "checkout", "coupon"],
                status="subscribed",
            )
        except Exception:
            pass  # Non-critical

        # Build success URL
        base_url = f"https://{settings.domain_name}"
        success_url = f"{base_url}/checkout/success?free=true&order={order_number}"

        logger.info(f"FREE checkout completed for {email}")

        return JSONResponse(content={
            "success": True,
            "redirect_url": success_url,
            "order_number": order_number,
        })

    except Exception as e:
        logger.error(f"Error processing free checkout: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JSONResponse(
            content={"error": "Failed to process checkout. Please try again."},
            status_code=500
        )


def _generate_order_number() -> str:
    """Generate a unique order number like ORD-ABC12345"""
    return f"ORD-{uuid.uuid4().hex[:8].upper()}"


def _generate_verification_token() -> str:
    """Generate a secure verification token"""
    return secrets.token_urlsafe(32)


@router.post("/api/checkout/process")
async def process_checkout(
    request: Request,
    tier: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    company: Optional[str] = Form(None),
    card_number: str = Form(...),
    card_expiry: str = Form(...),
    card_cvc: str = Form(...),
):
    """
    Process checkout: create user, client, order, send verification email.

    Test card numbers:
    - 4242 4242 4242 4242: Success
    - 4000 0000 0000 0002: Declined
    """
    import traceback

    try:
        # Normalize email
        email = email.lower().strip()
        full_name = f"{first_name} {last_name}"

        # Validate tier
        if tier not in TIER_PRICES:
            tier = "champion"

        # Validate passwords match
        if password != password_confirm:
            return HTMLResponse(
                content="""
                <div class="bg-red-500/10 border border-red-500/30 rounded-lg p-4 mb-4">
                    <p class="text-red-400 font-medium">Passwords don't match</p>
                    <p class="text-red-300 text-sm mt-2">Please ensure both password fields match.</p>
                </div>
                """,
                status_code=400
            )

        # Validate password length
        if len(password) < 8:
            return HTMLResponse(
                content="""
                <div class="bg-red-500/10 border border-red-500/30 rounded-lg p-4 mb-4">
                    <p class="text-red-400 font-medium">Password too short</p>
                    <p class="text-red-300 text-sm mt-2">Password must be at least 8 characters.</p>
                </div>
                """,
                status_code=400
            )

        # Clean card number and simulate payment
        clean_card = card_number.replace(" ", "").replace("-", "")

        if clean_card == "4000000000000002":
            return HTMLResponse(
                content="""
                <div class="bg-red-500/10 border border-red-500/30 rounded-lg p-4 mb-4">
                    <div class="flex items-center gap-2 mb-2">
                        <svg class="w-5 h-5 text-red-400" fill="currentColor" viewBox="0 0 20 20">
                            <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"></path>
                        </svg>
                        <span class="text-red-400 font-medium">Payment Declined</span>
                    </div>
                    <p class="text-red-300/80 text-sm">
                        Your card was declined. Please try a different card or contact your bank.
                    </p>
                </div>
                """,
                status_code=400
            )

        # Check if email already exists in platform
        existing_user = supabase.table("profiles").select("user_id").eq("email", email).execute()
        if existing_user.data:
            return HTMLResponse(
                content="""
                <div class="bg-red-500/10 border border-red-500/30 rounded-lg p-4 mb-4">
                    <p class="text-red-400 font-medium">Email already registered</p>
                    <p class="text-red-300 text-sm mt-2">
                        An account with this email already exists.
                        <a href="/admin/login" class="text-blue-400 hover:underline">Login here</a> or use a different email.
                    </p>
                </div>
                """,
                status_code=400
            )

        # Generate IDs and tokens
        order_number = _generate_order_number()
        user_id = str(uuid.uuid4())
        client_id = str(uuid.uuid4())
        verification_token = _generate_verification_token()
        price = TIER_PRICES.get(tier, 199)
        tier_name = TIER_NAMES.get(tier, "Champion")
        hosting_type = TIER_HOSTING.get(tier, "dedicated")

        # Extract metadata
        client_ip = request.client.host if request.client else None
        user_agent_str = request.headers.get("user-agent")

        logger.info(f"Processing checkout for {email} - Tier: {tier_name}")

        # 1. Create user in Supabase Auth (email NOT confirmed)
        # Use the service role client's admin API
        try:
            auth_response = supabase.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": False,  # Requires email verification
                "user_metadata": {
                    "full_name": full_name,
                    "company": company,
                    "signup_source": "checkout",
                    "tier": tier,
                }
            })
            if auth_response.user:
                user_id = auth_response.user.id
                logger.info(f"Created Supabase Auth user: {user_id}")
            else:
                raise Exception("Failed to create user in Supabase Auth")
        except Exception as auth_error:
            error_str = str(auth_error)
            if "already been registered" in error_str.lower() or "already exists" in error_str.lower():
                return HTMLResponse(
                    content="""
                    <div class="bg-red-500/10 border border-red-500/30 rounded-lg p-4 mb-4">
                        <p class="text-red-400 font-medium">Email already registered</p>
                        <p class="text-red-300 text-sm mt-2">
                            An account with this email already exists.
                            <a href="/admin/login" class="text-blue-400 hover:underline">Login here</a>.
                        </p>
                    </div>
                    """,
                    status_code=400
                )
            raise

        # 2. Create user profile in profiles table
        # Note: company/role are stored in auth user_metadata, not profiles table
        profile_data = {
            "user_id": user_id,
            "email": email,
            "full_name": full_name,
        }
        supabase.table("profiles").insert(profile_data).execute()
        logger.info(f"Created profile for user: {user_id}")

        # 3. Create client record
        client_name = company or f"{first_name}'s Sidekick"

        client_data = {
            "id": client_id,
            "name": client_name,
            "tier": tier,
            "hosting_type": hosting_type,
            "max_sidekicks": 1 if tier == "adventurer" else None,
            "owner_user_id": user_id,
            "owner_email": email,
            "provisioning_status": "queued",
            "uses_platform_keys": True,  # Use Sidekick Forge Inference by default
        }
        supabase.table("clients").insert(client_data).execute()
        logger.info(f"Created client: {client_id} ({client_name})")

        # 4. Update user metadata with tenant_assignments to grant admin role
        # This allows the user to access their client in the admin dashboard
        try:
            supabase.auth.admin.update_user_by_id(
                user_id,
                {
                    "user_metadata": {
                        "full_name": full_name,
                        "company": company,
                        "signup_source": "checkout",
                        "tier": tier,
                        "tenant_assignments": {
                            "admin_client_ids": [client_id],
                            "subscriber_client_ids": [],
                        }
                    }
                }
            )
            logger.info(f"Updated user metadata with admin role for client: {client_id}")
        except Exception as meta_error:
            logger.warning(f"Failed to update user metadata with tenant_assignments: {meta_error}")
            # Continue anyway - user can be granted access manually

        # 5. Create order record
        order_data = {
            "order_number": order_number,
            "user_id": user_id,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "company": company,
            "tier": tier,
            "price_cents": price * 100,
            "payment_status": "completed",
            "payment_method": "card",
            "payment_provider": "test",
            "client_id": client_id,
            "ip_address": client_ip,
            "user_agent": user_agent_str,
            "paid_at": datetime.utcnow().isoformat(),
        }
        order_result = supabase.table("orders").insert(order_data).execute()
        order_id = order_result.data[0]["id"] if order_result.data else None
        logger.info(f"Created order: {order_number}")

        # 6. Create verification token
        token_data = {
            "user_id": user_id,
            "token": verification_token,
            "email": email,
            "expires_at": (datetime.utcnow() + timedelta(hours=24)).isoformat(),
            "order_id": order_id,
        }
        supabase.table("email_verification_tokens").insert(token_data).execute()
        logger.info(f"Created verification token for: {email}")

        # 7. Queue provisioning job
        try:
            from app.services.onboarding.provisioning_worker import provision_client_by_tier
            await provision_client_by_tier(client_id, tier, supabase)
            logger.info(f"Queued provisioning for client: {client_id}")
        except Exception as prov_error:
            logger.error(f"Failed to queue provisioning: {prov_error}")
            # Continue anyway - provisioning can be retried

        # Add to Mailchimp audience
        try:
            mailchimp_service.subscribe(
                email=email,
                first_name=first_name,
                last_name=last_name,
                tags=[tier, "checkout"],
                status="subscribed",
            )
        except Exception:
            pass  # Non-critical

        # 8. Send verification email
        verification_url = f"https://{settings.domain_name}/verify-email?token={verification_token}"
        try:
            await mailjet_service.send_order_confirmation_email(
                to_email=email,
                to_name=full_name,
                order_data={
                    "order_id": order_id,
                    "order_number": order_number,
                    "tier_name": tier_name,
                    "price": price,
                },
                verification_url=verification_url,
            )
            logger.info(f"Sent order confirmation email to: {email}")
        except Exception as email_error:
            logger.error(f"Failed to send confirmation email: {email_error}")
            # Continue anyway - user can resend

        # 9. Send admin notification
        try:
            admin_data = {
                "full_name": full_name,
                "email": email,
                "company": company,
                "message": f"New {tier_name} signup!\nOrder: {order_number}\nPrice: ${price}/mo",
                "id": order_id,
            }
            await mailjet_service.send_submission_notification("checkout", admin_data)
        except Exception:
            pass  # Admin notification is not critical

        logger.info(f"✅ Checkout completed: {email} - {tier_name} - {order_number}")

        # 10. Return redirect to confirmation page
        # Use HX-Redirect header for HTMX
        return HTMLResponse(
            content="",
            status_code=200,
            headers={"HX-Redirect": f"/order-confirmation?order={order_number}"}
        )

    except Exception as e:
        logger.error(f"❌ Error processing checkout: {e}")
        logger.error(traceback.format_exc())
        return HTMLResponse(
            content="""
            <div class="bg-red-500/10 border border-red-500/30 rounded-lg p-4 mb-4">
                <p class="text-red-400 font-medium">Error processing order</p>
                <p class="text-red-300 text-sm mt-2">
                    Something went wrong. Please try again or contact support.
                </p>
            </div>
            """,
            status_code=500
        )


@router.get("/order-confirmation", response_class=HTMLResponse)
async def order_confirmation(request: Request, order: Optional[str] = None):
    """Display order confirmation page"""
    order_data = None

    if order:
        # Fetch order details
        result = supabase.table("orders").select("*").eq("order_number", order).execute()
        if result.data:
            order_data = result.data[0]
            order_data["tier_name"] = TIER_NAMES.get(order_data.get("tier"), "Unknown")
            order_data["price"] = order_data.get("price_cents", 0) // 100

    return templates.TemplateResponse(
        "marketing/order-confirmation.html",
        {
            "request": request,
            "current_year": datetime.now().year,
            "order": order_data,
        }
    )


@router.get("/verify-email", response_class=HTMLResponse)
async def verify_email(request: Request, token: Optional[str] = None):
    """
    Handle email verification when user clicks link from email.
    Validates token, confirms email in Supabase Auth, redirects to login.
    """
    if not token:
        return templates.TemplateResponse(
            "marketing/verify-email-result.html",
            {
                "request": request,
                "current_year": datetime.now().year,
                "success": False,
                "error": "invalid",
                "message": "No verification token provided.",
            }
        )

    try:
        # Look up token
        token_result = supabase.table("email_verification_tokens").select("*").eq("token", token).execute()

        if not token_result.data:
            return templates.TemplateResponse(
                "marketing/verify-email-result.html",
                {
                    "request": request,
                    "current_year": datetime.now().year,
                    "success": False,
                    "error": "invalid",
                    "message": "Invalid verification link. Please request a new one.",
                }
            )

        token_data = token_result.data[0]

        # Check if already used
        if token_data.get("used_at"):
            return templates.TemplateResponse(
                "marketing/verify-email-result.html",
                {
                    "request": request,
                    "current_year": datetime.now().year,
                    "success": False,
                    "error": "already_used",
                    "message": "This link has already been used. Please login to your account.",
                }
            )

        # Check if expired
        expires_at = datetime.fromisoformat(token_data["expires_at"].replace("Z", "+00:00"))
        if datetime.now(expires_at.tzinfo) > expires_at:
            return templates.TemplateResponse(
                "marketing/verify-email-result.html",
                {
                    "request": request,
                    "current_year": datetime.now().year,
                    "success": False,
                    "error": "expired",
                    "message": "This verification link has expired. Please request a new one.",
                    "email": token_data.get("email"),
                }
            )

        user_id = token_data["user_id"]
        email = token_data["email"]
        order_id = token_data.get("order_id")

        # Confirm email in Supabase Auth
        try:
            supabase.auth.admin.update_user_by_id(
                user_id,
                {"email_confirm": True}
            )
            logger.info(f"Confirmed email for user: {user_id}")
        except Exception as auth_error:
            logger.error(f"Failed to confirm email in auth: {auth_error}")
            # Continue anyway - might already be confirmed

        # Mark token as used
        supabase.table("email_verification_tokens").update({
            "used_at": datetime.utcnow().isoformat()
        }).eq("token", token).execute()

        # Update order activated_at if we have order_id
        if order_id:
            supabase.table("orders").update({
                "activated_at": datetime.utcnow().isoformat()
            }).eq("id", order_id).execute()

        logger.info(f"Email verified for: {email}")

        # Success - show result page
        return templates.TemplateResponse(
            "marketing/verify-email-result.html",
            {
                "request": request,
                "current_year": datetime.now().year,
                "success": True,
                "message": "Your email has been verified successfully!",
                "email": email,
            }
        )

    except Exception as e:
        logger.error(f"Error verifying email: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return templates.TemplateResponse(
            "marketing/verify-email-result.html",
            {
                "request": request,
                "current_year": datetime.now().year,
                "success": False,
                "error": "error",
                "message": "Something went wrong. Please try again or contact support.",
            }
        )


@router.post("/api/resend-verification")
async def resend_verification(request: Request, email: str = Form(...)):
    """Resend verification email"""
    email = email.lower().strip()

    try:
        # Find user by email
        profile_result = supabase.table("profiles").select("user_id, full_name").eq("email", email).execute()
        if not profile_result.data:
            return HTMLResponse(
                content="""
                <div class="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-4">
                    <p class="text-yellow-400 font-medium">Email not found</p>
                    <p class="text-yellow-300 text-sm mt-2">No account found with this email address.</p>
                </div>
                """,
                status_code=404
            )

        user_id = profile_result.data[0]["user_id"]
        full_name = profile_result.data[0].get("full_name", "")

        # Check if user already verified (check Supabase Auth)
        try:
            auth_user = supabase.auth.admin.get_user_by_id(user_id)
            if auth_user.user and auth_user.user.email_confirmed_at:
                return HTMLResponse(
                    content="""
                    <div class="bg-green-500/10 border border-green-500/30 rounded-lg p-4">
                        <p class="text-green-400 font-medium">Already verified</p>
                        <p class="text-green-300 text-sm mt-2">
                            Your email is already verified. <a href="/admin/login" class="text-blue-400 hover:underline">Login here</a>.
                        </p>
                    </div>
                    """,
                    status_code=200
                )
        except Exception:
            pass  # Continue if check fails

        # Invalidate old tokens
        supabase.table("email_verification_tokens").update({
            "used_at": datetime.utcnow().isoformat()
        }).eq("email", email).is_("used_at", "null").execute()

        # Create new token
        verification_token = _generate_verification_token()
        token_data = {
            "user_id": user_id,
            "token": verification_token,
            "email": email,
            "expires_at": (datetime.utcnow() + timedelta(hours=24)).isoformat(),
        }
        supabase.table("email_verification_tokens").insert(token_data).execute()

        # Send email
        verification_url = f"https://{settings.domain_name}/verify-email?token={verification_token}"
        await mailjet_service.send_verification_email(
            to_email=email,
            to_name=full_name or email,
            verification_url=verification_url,
        )

        logger.info(f"Resent verification email to: {email}")

        return HTMLResponse(
            content="""
            <div class="bg-green-500/10 border border-green-500/30 rounded-lg p-4">
                <p class="text-green-400 font-medium">Verification email sent!</p>
                <p class="text-green-300 text-sm mt-2">Please check your inbox and spam folder.</p>
            </div>
            """,
            status_code=200
        )

    except Exception as e:
        logger.error(f"Error resending verification: {e}")
        return HTMLResponse(
            content="""
            <div class="bg-red-500/10 border border-red-500/30 rounded-lg p-4">
                <p class="text-red-400 font-medium">Error</p>
                <p class="text-red-300 text-sm mt-2">Failed to send email. Please try again.</p>
            </div>
            """,
            status_code=500
        )


# ============================================================
# STRIPE PAYMENT ENDPOINTS
# ============================================================

@router.post("/api/stripe/create-checkout-session")
async def create_stripe_checkout_session(
    request: Request,
    tier: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    company: Optional[str] = Form(None),
):
    """
    Create a Stripe Checkout Session for tier purchase.
    Stores pending user data and redirects to Stripe.
    """
    try:
        # Normalize email
        email = email.lower().strip()

        # Validate tier
        if tier not in TIER_PRICES:
            tier = "champion"

        # Validate passwords match
        if password != password_confirm:
            return JSONResponse(
                content={"error": "Passwords don't match"},
                status_code=400
            )

        # Validate password length
        if len(password) < 8:
            return JSONResponse(
                content={"error": "Password must be at least 8 characters"},
                status_code=400
            )

        # Check if email already exists
        existing_user = supabase.table("profiles").select("user_id").eq("email", email).execute()
        if existing_user.data:
            return JSONResponse(
                content={"error": "Email already registered. Please login instead."},
                status_code=400
            )

        # Generate pending checkout ID
        pending_id = str(uuid.uuid4())

        # Store pending checkout data (will be used by webhook)
        pending_data = {
            "id": pending_id,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "company": company,
            "password": password,  # Will be used to create auth user after payment
            "tier": tier,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": (datetime.utcnow() + timedelta(hours=1)).isoformat(),
        }
        supabase.table("pending_checkouts").insert(pending_data).execute()

        # Build success/cancel URLs
        base_url = f"https://{settings.domain_name}"
        success_url = f"{base_url}/checkout/success?session_id={{CHECKOUT_SESSION_ID}}&pending_id={pending_id}"
        cancel_url = f"{base_url}/checkout?tier={tier}&canceled=true"

        # Create Stripe checkout session
        session_id, checkout_url = await stripe_service.create_checkout_session(
            tier=tier,
            email=email,
            first_name=first_name,
            last_name=last_name,
            company=company,
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "pending_checkout_id": pending_id,
            }
        )

        # Update pending checkout with session ID
        supabase.table("pending_checkouts").update({
            "stripe_session_id": session_id,
        }).eq("id", pending_id).execute()

        logger.info(f"Created Stripe checkout session {session_id} for {email}")

        # Return checkout URL for redirect
        return JSONResponse(content={"checkout_url": checkout_url})

    except ValueError as e:
        logger.error(f"Stripe checkout error: {e}")
        return JSONResponse(
            content={"error": str(e)},
            status_code=400
        )
    except Exception as e:
        logger.error(f"Error creating Stripe checkout session: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JSONResponse(
            content={"error": "Failed to create checkout session. Please try again."},
            status_code=500
        )


@router.post("/api/stripe/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="Stripe-Signature"),
):
    """
    Handle Stripe webhook events.
    Primary handler for checkout.session.completed events.
    """
    try:
        # Get raw body for signature verification
        payload = await request.body()

        if not stripe_signature:
            logger.warning("Webhook received without Stripe-Signature header")
            raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")

        # Construct and verify event
        try:
            event = stripe_service.construct_webhook_event(payload, stripe_signature)
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Invalid Stripe webhook signature: {e}")
            raise HTTPException(status_code=400, detail="Invalid signature")

        logger.info(f"Received Stripe webhook: {event['type']}")

        # Handle checkout completion (subscription created)
        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            await _handle_checkout_session_completed(session)

        # Handle subscription updates (status changes, period renewals)
        elif event["type"] == "customer.subscription.updated":
            subscription = event["data"]["object"]
            await stripe_service.handle_subscription_updated(subscription)

        # Handle subscription cancellation/deletion
        elif event["type"] == "customer.subscription.deleted":
            subscription = event["data"]["object"]
            subscription_id = subscription.get("id")
            logger.info(f"Subscription canceled: {subscription_id}")
            await stripe_service.handle_subscription_deleted(subscription_id)

        # Handle failed payments on subscription renewal
        elif event["type"] == "invoice.payment_failed":
            invoice = event["data"]["object"]
            subscription_id = invoice.get("subscription")
            customer_email = invoice.get("customer_email")
            logger.warning(f"Invoice payment failed for subscription {subscription_id}, customer {customer_email}")
            # Update status to past_due if subscription exists
            if subscription_id:
                try:
                    result = supabase.table("clients").update({
                        "subscription_status": "past_due"
                    }).eq("stripe_subscription_id", subscription_id).execute()
                except Exception as e:
                    logger.error(f"Failed to update subscription status: {e}")

        elif event["type"] == "payment_intent.payment_failed":
            payment_intent = event["data"]["object"]
            logger.warning(f"Payment failed: {payment_intent.get('id')}")

        return Response(status_code=200)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Webhook handler error")


async def _handle_checkout_session_completed(session: dict):
    """
    Process a completed Stripe checkout session.
    Creates user, client, order, and sends verification email.
    """
    session_id = session.get("id")
    customer_email = session.get("customer_email", "").lower()
    metadata = session.get("metadata", {})
    pending_checkout_id = metadata.get("pending_checkout_id")
    payment_intent = session.get("payment_intent")
    amount_total = session.get("amount_total", 0)
    # Subscription data (for subscription mode)
    stripe_customer_id = session.get("customer")
    stripe_subscription_id = session.get("subscription")

    logger.info(f"Processing completed checkout session: {session_id}")

    # Get pending checkout data
    pending_result = supabase.table("pending_checkouts").select("*").eq(
        "id", pending_checkout_id
    ).execute()

    if not pending_result.data:
        # Try to find by session ID
        pending_result = supabase.table("pending_checkouts").select("*").eq(
            "stripe_session_id", session_id
        ).execute()

    if not pending_result.data:
        logger.error(f"No pending checkout found for session {session_id}")
        return

    pending = pending_result.data[0]

    # Check if already processed
    if pending.get("status") == "completed":
        logger.info(f"Checkout {pending_checkout_id} already processed")
        return

    email = pending.get("email")
    first_name = pending.get("first_name")
    last_name = pending.get("last_name")
    company = pending.get("company")
    password = pending.get("password")
    tier = pending.get("tier") or metadata.get("tier", "champion")

    full_name = f"{first_name} {last_name}"
    order_number = _generate_order_number()
    verification_token = _generate_verification_token()
    tier_name = TIER_NAMES.get(tier, "Champion")
    hosting_type = TIER_HOSTING.get(tier, "dedicated")

    logger.info(f"Creating account for {email} - {tier_name}")

    try:
        # 1. Create user in Supabase Auth
        auth_response = supabase.auth.admin.create_user({
            "email": email,
            "password": password,
            "email_confirm": False,
            "user_metadata": {
                "full_name": full_name,
                "company": company,
                "signup_source": "stripe_checkout",
                "tier": tier,
            }
        })

        if not auth_response.user:
            raise Exception("Failed to create user in Supabase Auth")

        user_id = auth_response.user.id
        logger.info(f"Created Supabase Auth user: {user_id}")

        # 2. Create profile
        supabase.table("profiles").insert({
            "user_id": user_id,
            "email": email,
            "full_name": full_name,
        }).execute()

        # 3. Create client record
        client_id = str(uuid.uuid4())
        client_name = company or f"{first_name}'s Sidekick"

        supabase.table("clients").insert({
            "id": client_id,
            "name": client_name,
            "tier": tier,
            "hosting_type": hosting_type,
            "max_sidekicks": 1 if tier == "adventurer" else None,
            "owner_user_id": user_id,
            "owner_email": email,
            "provisioning_status": "queued",
            "uses_platform_keys": True,  # Use Sidekick Forge Inference by default
            # Stripe subscription data
            "stripe_customer_id": stripe_customer_id,
            "stripe_subscription_id": stripe_subscription_id,
            "subscription_status": "active" if stripe_subscription_id else "none",
        }).execute()

        # 4. Update user metadata with tenant assignments
        supabase.auth.admin.update_user_by_id(
            user_id,
            {
                "user_metadata": {
                    "full_name": full_name,
                    "company": company,
                    "signup_source": "stripe_checkout",
                    "tier": tier,
                    "tenant_assignments": {
                        "admin_client_ids": [client_id],
                        "subscriber_client_ids": [],
                    }
                }
            }
        )

        # 5. Create order record
        order_result = supabase.table("orders").insert({
            "order_number": order_number,
            "user_id": user_id,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "company": company,
            "tier": tier,
            "price_cents": amount_total,
            "payment_status": "completed",
            "payment_method": "card",
            "payment_provider": "stripe",
            "payment_provider_id": payment_intent,
            "client_id": client_id,
            "paid_at": datetime.utcnow().isoformat(),
        }).execute()

        order_id = order_result.data[0]["id"] if order_result.data else None

        # 6. Create verification token
        supabase.table("email_verification_tokens").insert({
            "user_id": user_id,
            "token": verification_token,
            "email": email,
            "expires_at": (datetime.utcnow() + timedelta(hours=24)).isoformat(),
            "order_id": order_id,
        }).execute()

        # 7. Queue provisioning
        try:
            from app.services.onboarding.provisioning_worker import provision_client_by_tier
            await provision_client_by_tier(client_id, tier, supabase)
        except Exception as prov_error:
            logger.error(f"Failed to queue provisioning: {prov_error}")

        # Add to Mailchimp audience
        try:
            mailchimp_service.subscribe(
                email=email,
                first_name=first_name,
                last_name=last_name,
                tags=[tier, "checkout", "stripe"],
                status="subscribed",
            )
        except Exception:
            pass  # Non-critical

        # 8. Send verification email
        verification_url = f"https://{settings.domain_name}/verify-email?token={verification_token}"
        try:
            await mailjet_service.send_order_confirmation_email(
                to_email=email,
                to_name=full_name,
                order_data={
                    "order_id": order_id,
                    "order_number": order_number,
                    "tier_name": tier_name,
                    "price": amount_total // 100,
                },
                verification_url=verification_url,
            )
        except Exception as email_error:
            logger.error(f"Failed to send confirmation email: {email_error}")

        # 9. Send admin notification
        try:
            admin_data = {
                "full_name": full_name,
                "email": email,
                "company": company,
                "message": f"New {tier_name} signup via Stripe!\nOrder: {order_number}\nPrice: ${amount_total // 100}",
                "id": order_id,
            }
            await mailjet_service.send_submission_notification("checkout", admin_data)
        except Exception:
            pass

        # 10. Mark pending checkout as completed
        supabase.table("pending_checkouts").update({
            "status": "completed",
            "completed_at": datetime.utcnow().isoformat(),
            "user_id": user_id,
            "client_id": client_id,
            "order_number": order_number,
        }).eq("id", pending_checkout_id).execute()

        logger.info(f"✅ Stripe checkout completed: {email} - {tier_name} - {order_number}")

    except Exception as e:
        logger.error(f"Error processing Stripe checkout: {e}")
        import traceback
        logger.error(traceback.format_exc())

        # Mark as failed
        supabase.table("pending_checkouts").update({
            "status": "failed",
            "error": str(e),
        }).eq("id", pending_checkout_id).execute()

        raise


@router.get("/auth/callback", response_class=HTMLResponse)
async def auth_callback(request: Request):
    """
    Handle Supabase auth callbacks (email verification, password reset, etc.)
    Supabase redirects here with hash parameters that are parsed client-side.
    """
    return templates.TemplateResponse(
        "marketing/auth-callback.html",
        {"request": request, "current_year": datetime.now().year}
    )


@router.get("/checkout/success", response_class=HTMLResponse)
async def checkout_success(
    request: Request,
    session_id: Optional[str] = None,
    pending_id: Optional[str] = None,
    order: Optional[str] = None,
    free: Optional[str] = None,
):
    """
    Display checkout success page.
    Called after successful Stripe payment or free checkout.
    """
    order_data = None

    # Handle free checkout - look up order directly by order_number
    if order and free == "true":
        order_result = supabase.table("orders").select("*").eq(
            "order_number", order
        ).execute()
        if order_result.data:
            order_data = order_result.data[0]
            order_data["tier_name"] = TIER_NAMES.get(order_data.get("tier"), "Unknown")
            order_data["price"] = order_data.get("price_cents", 0) // 100
            order_data["free_checkout"] = True

    # Try to get order from pending checkout
    if pending_id and not order_data:
        pending_result = supabase.table("pending_checkouts").select(
            "order_number, status, email, tier, first_name"
        ).eq("id", pending_id).execute()

        if pending_result.data:
            pending = pending_result.data[0]
            if pending.get("order_number"):
                # Get full order data
                order_result = supabase.table("orders").select("*").eq(
                    "order_number", pending["order_number"]
                ).execute()
                if order_result.data:
                    order_data = order_result.data[0]
                    order_data["tier_name"] = TIER_NAMES.get(order_data.get("tier"), "Unknown")
                    order_data["price"] = order_data.get("price_cents", 0) // 100
            elif pending.get("status") == "pending":
                # Payment completed but webhook hasn't processed yet
                # Show interim message
                return templates.TemplateResponse(
                    "marketing/checkout-processing.html",
                    {
                        "request": request,
                        "current_year": datetime.now().year,
                        "email": pending.get("email"),
                        "tier": TIER_NAMES.get(pending.get("tier"), "Unknown"),
                    }
                )

    # If we have a session ID, try to get order from Stripe metadata
    if session_id and not order_data:
        try:
            session_data = await stripe_service.retrieve_session(session_id)
            pending_checkout_id = session_data.get("metadata", {}).get("pending_checkout_id")
            if pending_checkout_id:
                pending_result = supabase.table("pending_checkouts").select(
                    "order_number"
                ).eq("id", pending_checkout_id).execute()
                if pending_result.data and pending_result.data[0].get("order_number"):
                    order_result = supabase.table("orders").select("*").eq(
                        "order_number", pending_result.data[0]["order_number"]
                    ).execute()
                    if order_result.data:
                        order_data = order_result.data[0]
                        order_data["tier_name"] = TIER_NAMES.get(order_data.get("tier"), "Unknown")
                        order_data["price"] = order_data.get("price_cents", 0) // 100
        except Exception as e:
            logger.error(f"Error retrieving Stripe session: {e}")

    return templates.TemplateResponse(
        "marketing/order-confirmation.html",
        {
            "request": request,
            "current_year": datetime.now().year,
            "order": order_data,
            "stripe_success": True,
        }
    )


@router.get("/api/stripe/publishable-key")
async def get_stripe_publishable_key():
    """Return the Stripe publishable key for frontend use"""
    return JSONResponse(content={
        "publishable_key": stripe_service.publishable_key
    })
