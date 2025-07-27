# Domain-Agnostic Deployment Guide

## Overview

The Sidekick Forge platform has been updated to support deployment on any domain without requiring code changes. All domain-specific configuration is now managed through environment variables.

## Key Changes

### 1. Environment Variables

The following environment variables control the platform deployment:

```bash
DOMAIN_NAME="sidekickforge.com"      # Your domain name
APP_NAME="sidekick-forge"            # Application identifier (used for containers, services)
PLATFORM_NAME="Sidekick Forge"       # Human-readable platform name
PROJECT_ROOT="/root/sidekick-forge"  # Project directory path
```

### 2. Dynamic Configuration

- **Nginx**: Uses template system with environment variable substitution
- **Docker**: Container names are dynamically generated using `${APP_NAME}`
- **Application**: Domain is loaded from environment, not hardcoded
- **SSL**: Certificates are generated for the configured domain

### 3. Migration Scripts

#### Quick Migration to Sidekick Forge
```bash
# Run the migration script
./scripts/migrate-to-sidekick-forge.sh

# Then deploy with your domain
cd /root/sidekick-forge
./scripts/deploy.sh sidekickforge.com
```

#### Deploy to Any Domain
```bash
# Deploy to a custom domain
./scripts/deploy.sh yourdomain.com your-app-name "Your Platform Name"

# Examples:
./scripts/deploy.sh agents.company.com company-agents "Company AI Agents"
./scripts/deploy.sh ai.startup.io startup-ai "Startup AI Platform"
```

## Deployment Process

### 1. Initial Setup

```bash
# Clone or copy the platform to your desired location
cp -r /root/autonomite-agent-platform /path/to/your-platform

# Navigate to the platform directory
cd /path/to/your-platform

# Update .env file with your configuration
nano .env
```

### 2. Configure Environment

Edit `.env` file:
```env
# Platform Configuration
DOMAIN_NAME="yourdomain.com"
APP_NAME="your-app-name"
PLATFORM_NAME="Your Platform Name"
PROJECT_ROOT="/path/to/your-platform"

# ... rest of configuration
```

### 3. Run Deployment

```bash
# Make scripts executable
chmod +x scripts/*.sh

# Run the deployment script
./scripts/deploy.sh yourdomain.com
```

### 4. SSL Certificate (Production)

For production deployments with HTTPS:
```bash
# The deploy script will ask if you want SSL
# Answer 'y' to set up Let's Encrypt certificate
```

## Testing Different Domains

### Local Development
```bash
# Deploy locally without SSL
DOMAIN_NAME=localhost ./scripts/deploy.sh localhost local-dev "Local Development"
```

### Staging Environment
```bash
# Deploy to staging
./scripts/deploy.sh staging.yourdomain.com staging "Staging Environment"
```

### Production
```bash
# Deploy to production with SSL
./scripts/deploy.sh yourdomain.com production "Production Platform"
```

## Multi-Domain Support

The platform can support multiple domains on the same server:

1. Deploy first domain:
```bash
./scripts/deploy.sh domain1.com app1 "App 1"
```

2. Copy platform to new directory:
```bash
cp -r /root/sidekick-forge /root/app2-platform
cd /root/app2-platform
```

3. Deploy second domain:
```bash
./scripts/deploy.sh domain2.com app2 "App 2"
```

## Rollback

If you need to rollback:

1. The migration script creates a backup at `/root/autonomite-agent-platform.backup`
2. Remove the symlink: `rm /root/autonomite-agent-platform`
3. Restore the backup: `mv /root/autonomite-agent-platform.backup /root/autonomite-agent-platform`
4. Restart services with old configuration

## Troubleshooting

### Domain Not Resolving
- Ensure DNS A record points to your server IP
- Wait for DNS propagation (can take up to 48 hours)
- Test with: `dig yourdomain.com`

### SSL Certificate Issues
- Ensure port 80 is open for Let's Encrypt validation
- Check domain is correctly configured in DNS
- Review certbot logs: `journalctl -u certbot`

### Service Not Starting
- Check environment variables: `cat .env`
- Review logs: `docker-compose logs -f`
- Verify paths in systemd service: `systemctl status platform-fastapi`

## Benefits

1. **Portability**: Deploy anywhere without code changes
2. **Multi-tenancy**: Run multiple instances with different domains
3. **Easy staging**: Test with different domains before production
4. **CI/CD friendly**: Configure through environment variables
5. **No hardcoding**: All configuration externalized