#!/bin/bash
# Generate nginx configuration from template with environment variables

# Load environment variables
ENV_ROOT=${PROJECT_ROOT:-/root/sidekick-forge}
if [ -f "${ENV_ROOT}/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "${ENV_ROOT}/.env"
    set +a
fi

# Determine domain
DOMAIN_NAME_INPUT=${1:-${DOMAIN_NAME:-}}
if [ -z "$DOMAIN_NAME_INPUT" ]; then
    echo "ERROR: DOMAIN_NAME is not set. Pass it as the first argument or define it in .env."
    exit 1
fi
DOMAIN_NAME=$DOMAIN_NAME_INPUT

# Set defaults if not provided
PROJECT_ROOT=${PROJECT_ROOT:-"/root/sidekick-forge"}
CORS_ALLOWED_ORIGINS=${CORS_ALLOWED_ORIGINS:-"https?://(www\.)?(localhost:[0-9]+)"}
DOMAIN_REGEX=${DOMAIN_NAME//./\\.}

# Export for envsubst
export DOMAIN_NAME PROJECT_ROOT CORS_ALLOWED_ORIGINS DOMAIN_REGEX

# Create nginx config directory if it doesn't exist
mkdir -p /etc/nginx/sites-available
mkdir -p /etc/nginx/sites-enabled

# Generate nginx configuration from template
echo "Generating nginx configuration for domain: $DOMAIN_NAME"
envsubst '${DOMAIN_NAME} ${PROJECT_ROOT} ${CORS_ALLOWED_ORIGINS}' \
    < "${PROJECT_ROOT}/nginx/site.conf.template" \
    > "/etc/nginx/sites-available/platform.conf"

# Create symbolic link
ln -sf /etc/nginx/sites-available/platform.conf /etc/nginx/sites-enabled/

# Test nginx configuration
nginx -t

if [ $? -eq 0 ]; then
    echo "Nginx configuration generated successfully"
else
    echo "ERROR: Nginx configuration test failed"
    exit 1
fi
