# Marketing Site Deployment - Staging Environment

## ✅ Understanding the Architecture

**Same structure on BOTH staging and production:**
- Marketing site: `/` (root, `/pricing`, `/features`, etc.)
- Admin platform: `/admin/*`
- API: `/api/*`

**Staging (Current):**
- Domain: `staging.sidekickforge.com`
- Marketing: `staging.sidekickforge.com/` ← Homepage
- Admin: `staging.sidekickforge.com/admin/` ← Platform

**Production (Future):**
- Domain: `sidekickforge.com`
- Marketing: `sidekickforge.com/` ← Homepage
- Admin: `sidekickforge.com/admin/` ← Platform

## 🚀 Deploy on Staging (Now)

### Step 1: Restart Services

```bash
cd /root/sidekick-forge
docker-compose restart fastapi
```

### Step 2: Verify Deployment

Check logs:
```bash
docker-compose logs fastapi | grep -i marketing
```

You should see:
```
✅ Marketing site routes loaded successfully
```

### Step 3: Test

Visit: **https://staging.sidekickforge.com/**

You should see the marketing homepage!

### Step 4: Verify Admin Still Works

Visit: **https://staging.sidekickforge.com/admin/**

Admin interface should work normally.

## 🧪 Test All Pages

- ✅ https://staging.sidekickforge.com/ → Homepage
- ✅ https://staging.sidekickforge.com/pricing → Pricing
- ✅ https://staging.sidekickforge.com/features → Features
- ✅ https://staging.sidekickforge.com/about → About
- ✅ https://staging.sidekickforge.com/contact → Contact
- ✅ https://staging.sidekickforge.com/signup → Signup
- ✅ https://staging.sidekickforge.com/admin/ → Admin (unchanged)

## 🎯 Test Forms

### Signup Form:
1. Go to https://staging.sidekickforge.com/signup
2. Fill out form
3. Submit
4. Check logs: `docker-compose logs -f fastapi | grep "Early access"`

### Contact Form:
1. Go to https://staging.sidekickforge.com/contact
2. Fill out form
3. Submit
4. Check logs: `docker-compose logs -f fastapi | grep "Contact form"`

### Demo Modal:
1. Go to homepage
2. Click "Book a Demo"
3. Modal should appear
4. Fill and submit
5. Check logs: `docker-compose logs -f fastapi | grep "Demo request"`

## ⚠️ Important Notes

1. **Admin Unaffected**: `/admin/*` routes load AFTER marketing routes, so admin continues working
2. **API Unaffected**: `/api/*` routes are prefixed, so APIs work normally
3. **No SSL Changes Needed**: Already have SSL for `staging.sidekickforge.com`
4. **Forms Currently Log**: Not saved to database (add later if needed)

## 🔧 Troubleshooting

### Homepage shows JSON instead of marketing site

**Cause**: Marketing routes didn't load

**Solution**:
```bash
docker-compose logs fastapi | grep -i marketing
```

If you see errors, check:
```bash
docker-compose logs fastapi | tail -50
```

### Admin login page not working

**Cause**: Unlikely - admin routes load after marketing

**Solution**: Check if `/admin/` is in the URL (with trailing slash)

### Forms don't submit

**Cause**: HTMX or API endpoint issue

**Test API directly**:
```bash
curl -X POST https://staging.sidekickforge.com/api/signup/early-access \
  -F "name=Test User" \
  -F "email=test@example.com" \
  -F "stage=solo" \
  -F "use_case=Testing the form"
```

Should return HTML with success message.

### Styling looks broken

**Cause**: Tailwind CDN not loading

**Solution**: Check browser console for errors. Verify internet connectivity.

## 📊 What Changed

### Files Modified:
- `app/main.py` → Added marketing router inclusion

### Files Added:
- `app/marketing/routes.py` → Marketing routes
- `app/marketing/__init__.py` → Module init
- `app/templates/marketing/*.html` → All page templates

### No Changes To:
- ✅ Admin routes (still at `/admin/*`)
- ✅ API routes (still at `/api/*`)
- ✅ Nginx config (already correct for staging)
- ✅ SSL certificates (already exist)
- ✅ Database (no migrations)

## 🚢 Deploy to Production (Later)

When ready for production:

1. **Set up production server** with Docker
2. **Clone repo** to production server
3. **Get SSL certificate** for `sidekickforge.com`:
   ```bash
   docker-compose stop nginx
   docker run -it --rm -v "/etc/letsencrypt:/etc/letsencrypt" -p 80:80 \
     certbot/certbot certonly --standalone \
     -d sidekickforge.com -d www.sidekickforge.com \
     --email hello@sidekickforge.com --agree-tos
   docker-compose start nginx
   ```
4. **Copy nginx config** from `nginx/conf.d/sidekickforge-base.conf` to production
5. **Deploy same code** - same structure, different domain!

## ✅ That's It!

The marketing site is now live on staging. Same structure will work on production - just different domain name!

**Current Status:**
- ✅ Marketing site: `staging.sidekickforge.com/`
- ✅ Admin platform: `staging.sidekickforge.com/admin/`
- ✅ API: `staging.sidekickforge.com/api/`

All three work together harmoniously! 🎉

