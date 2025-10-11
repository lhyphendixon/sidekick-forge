# For Dev Agent: Email Notification Setup

## 🎯 Your Task

Set up email notifications so the admin gets notified whenever someone submits a form on the marketing site.

## ✅ What's Already Done

The Oversight agent has:
- ✅ Created `contact_submissions` table in database
- ✅ Updated all form handlers to save submissions
- ✅ Forms capture: contact, demo requests, early access signups

## 📧 What You Need to Do

Add email notifications triggered when a new row is inserted into `contact_submissions`.

## Recommended Approach: Resend

**Why Resend:**
- Modern, simple API
- Free tier: 3,000 emails/month (plenty for form notifications)
- Great deliverability
- Python SDK available

### Setup Steps:

1. **Sign up at https://resend.com**
   - Free plan is fine

2. **Verify domain:** `sidekickforge.com`
   - Add DNS records they provide
   - OR use their test domain for now: `onboarding@resend.dev`

3. **Get API key**
   - Dashboard → API Keys → Create

4. **Add to .env:**
   ```bash
   RESEND_API_KEY=re_xxxxxxxxxxxx
   ADMIN_EMAIL=hello@sidekickforge.com
   ```

5. **Install package:**
   ```bash
   pip install resend
   # Add to requirements.txt
   ```

6. **Create notification service:**
   ```python
   # app/services/email_notifications.py
   import resend
   import os
   from datetime import datetime
   
   resend.api_key = os.getenv("RESEND_API_KEY")
   ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "hello@sidekickforge.com")
   
   def send_form_notification(submission_data: dict):
       """Send email notification for new form submission"""
       
       submission_type = submission_data.get('submission_type', 'contact')
       full_name = submission_data.get('full_name', 'Unknown')
       email = submission_data.get('email', 'No email')
       message = submission_data.get('message', '')
       priority = submission_data.get('priority', 'normal')
       
       # Priority emoji
       priority_emoji = "🔥" if priority == "high" else "📬"
       
       # Build email HTML
       html_content = f"""
       <html>
       <body style="font-family: system-ui, -apple-system, sans-serif; max-width: 600px;">
           <h2>{priority_emoji} New {submission_type.replace('_', ' ').title()}</h2>
           
           <div style="background: #f5f5f5; padding: 20px; border-radius: 8px; margin: 20px 0;">
               <p><strong>Name:</strong> {full_name}</p>
               <p><strong>Email:</strong> <a href="mailto:{email}">{email}</a></p>
               
               {f'<p><strong>Message:</strong><br>{message}</p>' if message else ''}
               
               {_format_additional_fields(submission_data)}
           </div>
           
           <p style="color: #666; font-size: 14px;">
               Submitted: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}
           </p>
           
           <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd;">
               <a href="[YOUR_SUPABASE_PROJECT_URL]/editor" 
                  style="background: #01a4a6; color: white; padding: 10px 20px; text-decoration: none; border-radius: 6px;">
                   View in Database →
               </a>
           </div>
       </body>
       </html>
       """
       
       try:
           resend.Emails.send({
               "from": "notifications@sidekickforge.com",  # Or onboarding@resend.dev for testing
               "to": ADMIN_EMAIL,
               "subject": f"{priority_emoji} New {submission_type.replace('_', ' ').title()} from {full_name}",
               "html": html_content
           })
           return True
       except Exception as e:
           print(f"Failed to send email notification: {e}")
           return False
   
   def _format_additional_fields(data: dict) -> str:
       """Format type-specific fields"""
       html = ""
       
       if data.get('submission_type') == 'early_access':
           html += f"<p><strong>Business Stage:</strong> {data.get('stage', 'N/A')}</p>"
           html += f"<p><strong>Use Case:</strong> {data.get('use_case', 'N/A')}</p>"
       
       if data.get('company'):
           html += f"<p><strong>Company:</strong> {data.get('company')}</p>"
       
       if data.get('phone_number'):
           html += f"<p><strong>Phone:</strong> {data.get('phone_number')}</p>"
       
       return html
   ```

7. **Update routes to call notification:**
   ```python
   # In app/marketing/routes.py
   from app.services.email_notifications import send_form_notification
   
   # After saving to database in each form handler:
   result = supabase.table("contact_submissions").insert(submission_data).execute()
   
   # Send notification
   if result.data:
       send_form_notification(result.data[0])
   ```

8. **Test it:**
   - Submit a form on staging
   - Check admin email
   - Should receive notification within seconds

## Alternative: Database Webhook

If you prefer, use Supabase Database Webhooks:

1. Go to your Supabase project database hooks page
2. Create webhook for `contact_submissions` table
3. Trigger on: INSERT
4. POST to external service or edge function
5. Edge function sends email via Resend

## Testing

Test email sending directly:
```python
import resend
resend.api_key = "re_xxx"

resend.Emails.send({
    "from": "onboarding@resend.dev",  # Use this for testing
    "to": "your-email@example.com",
    "subject": "Test Notification",
    "html": "<strong>It works!</strong>"
})
```

## Environment Variables Needed

Add to `.env`:
```bash
RESEND_API_KEY=re_xxxxxxxxxxxx
ADMIN_EMAIL=hello@sidekickforge.com
```

## 🎯 Success Criteria

- ✅ Admin receives email within 10 seconds of form submission
- ✅ Email contains all form data
- ✅ Demo requests marked as high priority (🔥 emoji)
- ✅ Email includes link to view in database
- ✅ Works for all three form types (contact, demo, early_access)

## Questions?

Check Resend docs: https://resend.com/docs/send-with-python

The database schema and form handlers are already complete. You just need to add the email sending logic!

