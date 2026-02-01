# Sidekick Forge Email Templates for Supabase

Branded email templates that match the Sidekick Forge aesthetic.

## Templates Included

| Template | File | Supabase Template Type |
|----------|------|------------------------|
| Email Confirmation | `confirm-signup.html` | Confirm signup |
| Password Reset | `reset-password.html` | Reset password |
| Magic Link | `magic-link.html` | Magic link |

## Installation Instructions

### 1. Access Supabase Dashboard

Go to your Supabase project:
- **Production**: https://supabase.com/dashboard/project/eukudpgfpihxsypulopm
- **Staging**: https://supabase.com/dashboard/project/senzircaknleviasihav

### 2. Navigate to Email Templates

1. Click **Authentication** in the left sidebar
2. Click **Email Templates** tab

### 3. Configure Each Template

For each template type:

#### Confirm Signup
- **Subject**: `Welcome to Sidekick Forge - Confirm Your Email`
- **Body**: Copy contents of `confirm-signup.html`

#### Reset Password
- **Subject**: `Reset Your Password - Sidekick Forge`
- **Body**: Copy contents of `reset-password.html`

#### Magic Link
- **Subject**: `Your Magic Link - Sidekick Forge`
- **Body**: Copy contents of `magic-link.html`

### 4. Important Settings

Make sure these settings are configured in **Authentication** → **Providers** → **Email**:

- **Enable email confirmations**: ON (to require email verification)
- **Secure email change**: ON (recommended)
- **Double confirm email changes**: ON (recommended)

### 5. Update Logo URL

If using a different domain or staging environment, update the logo URL in each template:

```html
<!-- Change this URL to match your environment -->
<img src="https://sidekickforge.com/static/images/sidekick-forge-logo.png" ...>

<!-- For staging: -->
<img src="https://staging.sidekickforge.com/static/images/sidekick-forge-logo.png" ...>
```

## Template Variables

Supabase provides these variables for use in templates:

| Variable | Description |
|----------|-------------|
| `{{ .ConfirmationURL }}` | The URL the user clicks to confirm/reset |
| `{{ .Email }}` | User's email address |
| `{{ .SiteURL }}` | Your configured site URL |
| `{{ .Token }}` | The verification token |
| `{{ .TokenHash }}` | Hashed version of the token |
| `{{ .RedirectTo }}` | Where to redirect after confirmation |

## Brand Colors Reference

- **Background**: `#0a0a0f` (near black)
- **Card Background**: `rgba(30, 41, 59, 0.8)` to `rgba(15, 23, 42, 0.9)`
- **Primary Gradient**: `#3b82f6` (blue) to `#22d3ee` (cyan)
- **White Text**: `#ffffff`
- **Body Text**: `#d1d5db`
- **Muted Text**: `#94a3b8`, `#6b7280`
- **Border**: `rgba(255, 255, 255, 0.1)`

## Testing

After saving templates, test by:
1. Creating a new account (triggers confirm signup)
2. Using "Forgot Password" (triggers reset password)
3. Using passwordless login if enabled (triggers magic link)

Check emails arrive correctly and links work as expected.
