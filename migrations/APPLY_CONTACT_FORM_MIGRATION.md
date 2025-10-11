# Apply Contact Form Migration

## 🎯 Quick Steps

1. **Go to your Supabase project SQL Editor**

2. **Copy the contents of:** `migrations/20241011_add_contact_submissions.sql`

3. **Paste and click "Run"**

4. **Verify:**
   ```sql
   SELECT table_name, column_name, data_type 
   FROM information_schema.columns 
   WHERE table_name = 'contact_submissions'
   ORDER BY ordinal_position;
   ```

## ✅ What This Creates

- **Table:** `contact_submissions` - stores all form submissions
- **Indexes:** For fast queries by status, type, email, date
- **RLS Policies:** Service role can insert, authenticated users can view/update
- **Triggers:** Auto-updates `updated_at` timestamp

## 📊 Test It

After migration, test with:
```sql
-- Insert test submission
INSERT INTO public.contact_submissions (
    email, 
    full_name, 
    message, 
    submission_type
) VALUES (
    'test@example.com',
    'Test User',
    'Test message',
    'contact'
);

-- Query it
SELECT * FROM public.contact_submissions LIMIT 1;
```

Should return your test record!

## 🔧 For Dev Agent

Once the table is created, the marketing forms will automatically start saving submissions to it. The dev agent needs to set up email notifications separately using the SMTP integration.

