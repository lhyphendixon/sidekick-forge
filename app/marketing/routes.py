"""
Marketing site routes for Sidekick Forge
Handles homepage, pricing, features, about, contact, and signup
"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime
import logging
from typing import Optional
from supabase import create_client
from app.config import settings
from app.services.mailjet_service import mailjet_service

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
