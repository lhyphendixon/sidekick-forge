# Sidekick Forge Marketing Site - Deployment Guide

## 🎉 What's Been Built

A complete marketing website for `sidekickforge.com` featuring:

### Pages Created:
- ✅ **Homepage** (`/`) - Hero's journey themed with clear value prop
- ✅ **Pricing** (`/pricing`) - 3-tier pricing table (Starter, Professional, Enterprise)
- ✅ **Features** (`/features`) - Comprehensive feature showcase
- ✅ **About** (`/about`) - Mission and story
- ✅ **Contact** (`/contact`) - Contact form with demo booking
- ✅ **Signup** (`/signup`) - Early access registration

### Technical Stack:
- **Backend**: FastAPI (Python)
- **Frontend**: HTMX + Tailwind CSS + Alpine.js
- **Templates**: Jinja2
- **Design**: Your brand colors (#01a4a6 teal, #fc7244 orange, #f56453 salmon)

## 📁 Files Created

```
/root/sidekick-forge/
├── app/
│   ├── marketing/
│   │   ├── __init__.py
│   │   └── routes.py              # Marketing site routes
│   └── templates/
│       └── marketing/
│           ├── base.html           # Base template with nav/footer
│           ├── home.html           # Homepage
│           ├── pricing.html        # Pricing page
│           ├── features.html       # Features page
│           ├── about.html          # About page
│           ├── contact.html        # Contact form
│           └── signup.html         # Early access signup
└── nginx/
    └── conf.d/
        └── sidekickforge-base.conf # Nginx config for base domain
```

## 🚀 Deployment Steps

### Step 1: Get SSL Certificate for Base Domain

Since `sidekickforge.com` already points to your server, you need to get an SSL certificate:

```bash
# SSH into your server
ssh root@your-server-ip

# Navigate to project
cd /root/sidekick-forge

# Get SSL certificate for base domain
docker-compose run --rm certbot certonly \
  --webroot \
  --webroot-path=/var/www/certbot \
  -d sidekickforge.com \
  -d www.sidekickforge.com \
  --email hello@sidekickforge.com \
  --agree-tos \
  --no-eff-email
```

If you don't have certbot in your docker-compose, use:

```bash
# Stop nginx temporarily
docker-compose stop nginx

# Run certbot standalone
docker run -it --rm \
  -v "/etc/letsencrypt:/etc/letsencrypt" \
  -v "/var/lib/letsencrypt:/var/lib/letsencrypt" \
  -p 80:80 \
  certbot/certbot certonly \
  --standalone \
  -d sidekickforge.com \
  -d www.sidekickforge.com \
  --email hello@sidekickforge.com \
  --agree-tos \
  --no-eff-email

# Start nginx again
docker-compose start nginx
```

### Step 2: Deploy Updated Code

```bash
# Navigate to project
cd /root/sidekick-forge

# Pull latest code (or commit your changes first)
# If you made manual changes, commit them:
# git add -A
# git commit -m "Add marketing site"
# git push

# Restart services
docker-compose restart fastapi
docker-compose restart nginx
```

### Step 3: Verify Deployment

1. **Check FastAPI logs**:
   ```bash
   docker-compose logs -f fastapi | grep -i marketing
   ```
   You should see: `✅ Marketing site routes loaded successfully`

2. **Test locally** (if you want):
   ```bash
   curl -I https://sidekickforge.com
   ```

3. **Visit in browser**:
   - https://sidekickforge.com - Should show homepage
   - https://sidekickforge.com/pricing
   - https://sidekickforge.com/features
   - https://sidekickforge.com/signup

### Step 4: Test All Functionality

✅ **Navigation**: Click through all pages  
✅ **Signup Form**: Fill out early access form (check logs)  
✅ **Contact Form**: Submit contact form (check logs)  
✅ **Book Demo**: Click "Book a Demo" buttons (modal should appear)  
✅ **Mobile**: Test on mobile device (responsive design)  
✅ **Admin Redirect**: Go to `/admin/` - should redirect to staging subdomain

## 🔧 Troubleshooting

### Issue: 404 on homepage
**Solution**: Check FastAPI logs. Marketing routes may not have loaded.
```bash
docker-compose logs fastapi | grep -i "marketing"
```

### Issue: SSL Certificate error
**Solution**: Verify certificate exists:
```bash
ls -la /etc/letsencrypt/live/sidekickforge.com/
```

If missing, run Step 1 again.

### Issue: Forms don't submit
**Solution**: Check browser console for HTMX errors. Verify API endpoints:
```bash
curl -X POST https://sidekickforge.com/api/signup/early-access \
  -F "name=Test" \
  -F "email=test@example.com" \
  -F "stage=solo" \
  -F "use_case=Testing"
```

### Issue: Styles not loading
**Solution**: Check Tailwind CDN loads. View page source, verify CDN scripts are present.

### Issue: Base domain shows JSON response
**Solution**: The old root endpoint may not be commented out. Check `app/main.py` line 274-283.

## 📊 Form Submissions

Currently, form submissions are logged but not saved to database. To see submissions:

```bash
# Watch FastAPI logs for form submissions
docker-compose logs -f fastapi | grep -i "signup\|contact\|demo"
```

### Next Steps for Form Data:

1. **Add to Supabase**:
   - Create `early_access_signups` table
   - Create `contact_submissions` table
   - Update routes.py to save to database

2. **Email Notifications**:
   - Set up SendGrid/Mailgun
   - Send confirmation emails to users
   - Notify team of new signups

3. **CRM Integration**:
   - Connect to your CRM
   - Auto-create leads from forms

## 🎨 Customization

### Update Brand Colors

Edit `app/templates/marketing/base.html` line 18-26:

```javascript
colors: {
    'brand-teal': '#01a4a6',      // Primary color
    'brand-orange': '#fc7244',     // Secondary color
    'brand-salmon': '#f56453',     // Accent color
    'brand-dark': '#0a0a0a',       // Background
    'brand-dark-elevated': '#1a1a1a', // Card background
}
```

### Update Content

- **Homepage hero**: Edit `app/templates/marketing/home.html` line 22-33
- **Pricing**: Edit `app/templates/marketing/pricing.html` line 20-120
- **Features**: Edit `app/templates/marketing/features.html` line 20-90
- **About**: Edit `app/templates/marketing/about.html` line 15-70

### Add New Pages

1. Create template: `app/templates/marketing/newpage.html`
2. Add route in `app/marketing/routes.py`:
   ```python
   @router.get("/newpage", response_class=HTMLResponse)
   async def newpage(request: Request):
       return templates.TemplateResponse("marketing/newpage.html", {...})
   ```
3. Add to navigation in `base.html`

## 🔐 Security Notes

- ✅ HTTPS enforced (HTTP redirects to HTTPS)
- ✅ Security headers configured in nginx
- ✅ CORS properly configured
- ✅ Rate limiting on API endpoints
- ⚠️  Forms currently don't validate input (add validation)
- ⚠️  No CAPTCHA (add if spam becomes an issue)

## 📈 Analytics

To add analytics:

1. **Google Analytics**: Add to `base.html` `<head>`:
   ```html
   <!-- Google Analytics -->
   <script async src="https://www.googletagmanager.com/gtag/js?id=G-XXXXXXXXXX"></script>
   ```

2. **Plausible/Fathom**: Add script tag similarly

3. **Form conversion tracking**: Add events in form submission handlers

## ✅ Launch Checklist

Before going live:

- [ ] SSL certificate installed and working
- [ ] All pages load correctly
- [ ] Forms submit successfully (test all forms)
- [ ] Mobile responsive (test on phone)
- [ ] Social media links updated (currently placeholder)
- [ ] Analytics installed (optional)
- [ ] Form data being saved (or logged intentionally)
- [ ] Email notifications working (optional)
- [ ] Privacy Policy & Terms pages added (legal requirement)
- [ ] 404 page customized (optional)
- [ ] Favicon updated (`/app/static/favicon.ico`)

## 🎯 What Happens After Deployment

1. **Homepage** becomes your public face at `sidekickforge.com`
2. **Admin interface** stays at `staging.sidekickforge.com/admin/`
3. **API** continues working at both domains under `/api/`
4. **Forms** collect early access signups (currently logged, add DB later)
5. **Demo requests** tracked in logs

## 💡 Pro Tips

1. **Test in incognito** - Avoid cached assets
2. **Check mobile first** - Most visitors are mobile
3. **Monitor logs** - Watch for errors after deployment
4. **Iterate quickly** - Easy to update templates and redeploy
5. **Add real testimonials** - Replace placeholder content when ready

## 🆘 Need Help?

If something doesn't work:

1. Check logs: `docker-compose logs -f fastapi nginx`
2. Verify nginx config: `docker-compose exec nginx nginx -t`
3. Restart services: `docker-compose restart`
4. Check this guide's troubleshooting section above

## 🎉 You're Ready!

Run the deployment steps and your marketing site will be live!

The homepage emphasizes the "hero's journey" theme, targets mission-driven entrepreneurs, 
and includes clear CTAs for early access and demo booking.

Good luck with your launch! 🚀

