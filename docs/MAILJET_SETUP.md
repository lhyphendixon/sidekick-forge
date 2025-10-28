# Mailjet Notification Setup

Sidekick Forge now supports sending marketing-site submission alerts through
Mailjet. Follow the steps below to activate the integration.

## 1. Gather credentials

From the Mailjet dashboard:

1. Navigate to **Account settings → API keys**.
2. Copy the **API Key** and **Secret Key** for the environment you plan to use.
3. Ensure your sender domain/address is verified under **Senders & Domains**.

## 2. Configure environment variables

Add the following entries to the deployment environment (see `.env` for
placeholders):

| Variable | Description |
| --- | --- |
| `MAILJET_API_KEY` | Mailjet API key |
| `MAILJET_API_SECRET` | Mailjet secret key |
| `MAILJET_SENDER_EMAIL` | Verified sender address Mailjet can use (e.g. `notifications@sidekickforge.com`) |
| `MAILJET_SENDER_NAME` | Optional display name for the sender |
| `MAILJET_NOTIFICATION_RECIPIENTS` | Comma-separated list of recipients. Supports `Name <email@domain>` format. |

Example:

```
MAILJET_NOTIFICATION_RECIPIENTS=Founder <founder@example.com>,alerts@example.com
```

## 3. Deploy and verify

1. Redeploy the application with the new environment variables.
2. Submit each marketing form (contact, demo, early access) using test data.
3. Confirm the notification email arrives and that the `Reply-To` header points
   to the submitter’s email address.

If notifications do not arrive, review the application logs for `Mailjet`
entries and confirm Mailjet’s event logs for the API key. The application
continues to store submissions even if email delivery fails.
