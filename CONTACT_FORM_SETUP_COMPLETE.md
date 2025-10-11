# Contact Form Database Setup - Complete ✅

## What's Been Done

### ✅ Database Schema Created
**Migration file:** `migrations/20241011_add_contact_submissions.sql`

**Table:** `contact_submissions`
- Stores all form submissions (contact, demo, early access)
- Includes lead tracking (status, priority, notes)
- Captures metadata (IP, user agent, referrer, UTM params)
- Has RLS policies for security

**Features:**
- Auto-timestamps (created_at, updated_at)
- Indexes for fast queries
- Status tracking for sales pipeline
- Priority levels for urgent leads

### ✅ Form Handlers Updated
**File:** `app/marketing/routes.py`

All three forms now save to database:
1. **Early Access Signup** (`/api/signup/early-access`)
   - Saves: name, email, business, stage, use_case
   - Priority: normal
   
2. **Contact Form** (`/api/contact/submit`)
   - Saves: first_name, last_name, email, company, phone, message
   - Priority: normal
   
3. **Demo Request** (`/api/demo/submit`)
   - Saves: name, email, preferred time
   - Priority: **high** (demo requests are higher priority)

All forms capture:
- IP address
- User agent
- Referrer URL
- Timestamp

## 🚀 Next Steps

### Step 1: Apply the Migration
You need to manually apply the migration to create the table:

1. Go to your Supabase project SQL Editor
2. Copy contents of: `migrations/20241011_add_contact_submissions.sql`
3. Paste and click **"Run"**
4. Verify with:
   ```sql
   SELECT * FROM contact_submissions LIMIT 1;
   ```

### Step 2: Restart FastAPI
```bash
docker restart sidekick-forge-fastapi
```

### Step 3: Test a Form
1. Go to: https://staging.sidekickforge.com/contact
2. Fill out and submit
3. Check database:
   ```sql
   SELECT 
       full_name, 
       email, 
       submission_type, 
       status,
       created_at 
   FROM contact_submissions 
   ORDER BY created_at DESC 
   LIMIT 5;
   ```

### Step 4: Dev Agent - Email Notifications
**For the dev agent to implement:**

The dev agent needs to add email notifications when new submissions arrive. Options:

#### Option A: Resend (Recommended)
```python
# pip install resend
import resend
resend.api_key = os.getenv("RESEND_API_KEY")

# Send on new submission
resend.Emails.send({
    "from": "notifications@sidekickforge.com",
    "to": "hello@sidekickforge.com",
    "subject": f"New {submission_type} from {full_name}",
    "html": f"<strong>Email:</strong> {email}<br>..."
})
```

**Setup Resend:**
1. Sign up at https://resend.com (free 3k emails/month)
2. Verify domain: sidekickforge.com
3. Get API key
4. Add to `.env`: `RESEND_API_KEY=re_xxx`

#### Option B: SendGrid
Similar setup but more complex API.

#### Option C: Supabase Webhooks + External Service
Use Supabase database webhooks to trigger external notification service.

## 📊 View Submissions in Admin

**Quick SQL queries for the admin:**

### All new submissions
```sql
SELECT 
    full_name,
    email,
    submission_type,
    priority,
    created_at,
    message
FROM contact_submissions
WHERE status = 'new'
ORDER BY priority DESC, created_at DESC;
```

### Demo requests (high priority)
```sql
SELECT 
    full_name,
    email,
    message,
    created_at
FROM contact_submissions
WHERE submission_type = 'demo'
AND status = 'new'
ORDER BY created_at DESC;
```

### Early access signups
```sql
SELECT 
    full_name,
    email,
    stage,
    use_case,
    created_at
FROM contact_submissions
WHERE submission_type = 'early_access'
ORDER BY created_at DESC;
```

### Update status (after contacting)
```sql
UPDATE contact_submissions
SET 
    status = 'contacted',
    first_contact_at = NOW(),
    contact_count = 1,
    notes = 'Sent welcome email and demo link'
WHERE id = '<submission_id>';
```

## 🎯 Future Enhancements

### Admin Dashboard (Later)
Build admin UI to manage leads:
- View all submissions in table
- Filter by status, type, date
- Assign to team members
- Add notes and track follow-ups
- Mark as spam/archived

### Analytics (Later)
- Conversion rates by source (UTM tracking)
- Response time metrics
- Lead quality scoring

### Email Automation (Later)
- Auto-responder emails
- Welcome sequences for early access
- Demo confirmation emails with calendar links

## ✅ Current Status

- ✅ Migration created
- ✅ Form handlers updated
- ⏳ Migration needs to be applied (manual step)
- ⏳ FastAPI needs restart
- ⏳ Dev agent needs to add email notifications

Once the migration is applied and FastAPI restarted, all forms will start saving to the database automatically!

