#!/bin/bash
# Setup SSL certificate for any domain using Certbot

# Load environment variables
if [ -f "${PROJECT_ROOT:-/root/sidekick-forge}/.env" ]; then
    export $(cat "${PROJECT_ROOT:-/root/sidekick-forge}/.env" | grep -v '^#' | xargs)
fi

# Set defaults
DOMAIN_NAME=${DOMAIN_NAME:-"sidekickforge.com"}
SSL_EMAIL=${SSL_EMAIL:-"admin@$DOMAIN_NAME"}

echo "Setting up SSL certificate for domain: $DOMAIN_NAME"
echo "Using email: $SSL_EMAIL"

# Check if certbot is installed
if ! command -v certbot &> /dev/null; then
    echo "Installing certbot..."
    apt-get update
    apt-get install -y certbot python3-certbot-nginx
fi

# Create webroot directory for Let's Encrypt challenges
mkdir -p /var/www/certbot

# Generate SSL certificate
certbot certonly \
    --nginx \
    -d "$DOMAIN_NAME" \
    --non-interactive \
    --agree-tos \
    --email "$SSL_EMAIL" \
    --redirect \
    --expand

if [ $? -eq 0 ]; then
    echo "SSL certificate generated successfully for $DOMAIN_NAME"
    
    # Set up automatic renewal
    echo "Setting up automatic renewal..."
    (crontab -l 2>/dev/null; echo "0 0,12 * * * certbot renew --quiet --post-hook 'systemctl reload nginx'") | crontab -
    
    echo "SSL setup complete!"
else
    echo "ERROR: Failed to generate SSL certificate"
    exit 1
fi