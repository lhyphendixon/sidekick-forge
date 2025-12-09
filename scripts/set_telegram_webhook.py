#!/usr/bin/env python3
"""Utility to set the Telegram webhook for this deployment."""
import os
import sys
import argparse
import httpx

DEFAULT_PATH = "/webhooks/telegram"


def main() -> int:
    parser = argparse.ArgumentParser(description="Set Telegram webhook URL")
    parser.add_argument("--url", help="Public base URL (e.g. https://api.example.com)", required=False)
    parser.add_argument("--path", help="Webhook path (default: /webhooks/telegram)", default=DEFAULT_PATH)
    parser.add_argument("--token", help="Bot token (fallback to TELEGRAM_BOT_TOKEN)", required=False)
    parser.add_argument("--secret", help="Webhook secret token (fallback to TELEGRAM_WEBHOOK_SECRET)", required=False)
    args = parser.parse_args()

    bot_token = args.token or os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        print("Missing bot token (set TELEGRAM_BOT_TOKEN or pass --token)", file=sys.stderr)
        return 1

    base_url = args.url or os.getenv("PUBLIC_BASE_URL")
    if not base_url:
        print("Missing public base URL (set PUBLIC_BASE_URL or pass --url)", file=sys.stderr)
        return 1

    webhook_url = base_url.rstrip("/") + (args.path or DEFAULT_PATH)
    secret_token = args.secret or os.getenv("TELEGRAM_WEBHOOK_SECRET")

    payload = {"url": webhook_url}
    if secret_token:
        payload["secret_token"] = secret_token

    api_url = f"https://api.telegram.org/bot{bot_token}/setWebhook"
    print(f"Setting Telegram webhook to {webhook_url}")
    if secret_token:
        print("Using secret token for validation")

    try:
        resp = httpx.post(api_url, data=payload, timeout=10.0)
        print(f"Status: {resp.status_code}")
        print(resp.text)
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to set webhook: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
