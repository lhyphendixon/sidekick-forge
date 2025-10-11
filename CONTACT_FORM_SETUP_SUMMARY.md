# Contact Form Setup - Summary

## ✅ What I've Done

### 1. **Created Database Schema**
- **File:** `migrations/20241011_add_contact_submissions.sql`
- **Table:** `contact_submissions`
- Stores all form submissions (contact, demo, early access)
- Includes lead tracking, metadata, UTM params
- Has proper indexes and RLS policies

### 2. **Updated All Form Handlers**
- **File:** `app/marketing/routes.py`
- All 3 forms now save to database:
  - Early access signup
  - Contact form
  - Demo booking
- Captures IP, user agent, referrer for tracking
- Demo requests automatically marked as HIGH priority

### 3. **Created Documentation**
- `CONTACT_FORM_SETUP_COMPLETE.md` - Full guide
- `FOR_DEV_AGENT_EMAIL_NOTIFICATIONS.md` - Email setup instructions for dev agent
- `migrations/APPLY_CONTACT_FORM_MIGRATION.md` - Quick migration steps

## 🚀 What You Need to Do

### Step 1: Apply the Migration (5 minutes)
1. Go to your Supabase project SQL Editor

2. Copy the entire contents of:
   `migrations/20241011_add_contact_submissions.sql`

3. Paste into SQL editor and click **"Run"**

4. Verify it worked:
   ```sql
   SELECT * FROM contact_submissions LIMIT 1;
   ```
   Should return empty table (no errors)

### Step 2: Restart FastAPI
```bash
docker restart sidekick-forge-fastapi
```

### Step 3: Test It! 🎉
1. Go to: https://staging.sidekickforge.com/contact
2. Fill out the form
3. Submit
4. Check database:
   ```sql
   SELECT full_name, email, submission_type, created_at 
   FROM contact_submissions 
   ORDER BY created_at DESC;
   ```
   You should see your test submission!

## 📧 Email Notifications

Give the dev agent this file: **`FOR_DEV_AGENT_EMAIL_NOTIFICATIONS.md`**

It has complete instructions for:
- Setting up Resend (recommended, free tier works great)
- Code to add to the platform
- Testing email delivery

## 📊 Viewing Submissions

Until you build an admin UI, use SQL queries:

### See all new submissions:
```sql
SELECT 
    full_name,
    email,
    submission_type,
    priority,
    created_at
FROM contact_submissions
WHERE status = 'new'
ORDER BY priority DESC, created_at DESC;
```

### See just demo requests:
```sql
SELECT * FROM contact_submissions 
WHERE submission_type = 'demo' 
AND status = 'new';
```

### Mark as contacted:
```sql
UPDATE contact_submissions
SET status = 'contacted', first_contact_at = NOW()
WHERE id = '<their-submission-id>';
```

## 🎯 Status After You Apply Migration

Once you apply the migration and restart:
- ✅ All forms save to database automatically
- ✅ You can query submissions anytime
- ✅ Lead tracking ready (status, priority, notes)
- ⏳ Email notifications (dev agent handles this)

## Files to Review

1. **`migrations/20241011_add_contact_submissions.sql`** - The migration to apply
2. **`CONTACT_FORM_SETUP_COMPLETE.md`** - Full documentation
3. **`FOR_DEV_AGENT_EMAIL_NOTIFICATIONS.md`** - For dev agent
4. **`app/marketing/routes.py`** - Updated form handlers (already deployed)

That's it! Apply the migration, restart, and you're good to go! 🚀

