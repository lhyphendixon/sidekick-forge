# Deployment Guide

## Prerequisites

- Ubuntu 20.04+ or similar Linux distribution
- Docker and Docker Compose installed
- Domain name with DNS configured
- SSL certificate (or use Let's Encrypt)
- Supabase account
- LiveKit account

## Production Deployment

### 1. Server Setup

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Add user to docker group
sudo usermod -aG docker $USER
```

### 2. Clone Repository

```bash
git clone https://github.com/yourusername/autonomite-agent-platform.git
cd autonomite-agent-platform
```

### 3. Environment Configuration

```bash
# Copy example environment file
cp .env.example .env

# Edit with your configuration
nano .env
```

Required environment variables:
```env
# Production settings
APP_ENV=production
DEBUG=false
SECRET_KEY=<generate-strong-secret>

# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-service-key

# LiveKit
LIVEKIT_URL=wss://your-server.livekit.cloud
LIVEKIT_API_KEY=your-api-key
LIVEKIT_API_SECRET=your-api-secret

# Redis
REDIS_PASSWORD=<generate-strong-password>
```

### 4. SSL Certificate

#### Option A: Let's Encrypt (Recommended)

```bash
# Install Certbot
sudo apt install certbot python3-certbot-nginx

# Generate certificate
sudo certbot certonly --standalone -d your-domain.com

# Certificates will be in:
# /etc/letsencrypt/live/your-domain.com/
```

#### Option B: Custom Certificate

Place your certificate files in:
- `./ssl/cert.pem`
- `./ssl/key.pem`

### 5. Nginx Configuration

Update `nginx/conf.d/autonomite.conf`:

```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.com;
    
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;
    
    # ... rest of configuration
}
```

### 6. Build and Deploy

```bash
# Build images
docker-compose -f docker-compose.production.yml build

# Start services
docker-compose -f docker-compose.production.yml up -d

# Check logs
docker-compose -f docker-compose.production.yml logs -f
```

### 7. Database Migrations

```bash
# Run migrations
docker-compose -f docker-compose.production.yml exec fastapi python -m app.migrations
```

### 8. Create Admin User

```bash
# Access container
docker-compose -f docker-compose.production.yml exec fastapi bash

# Create admin
python -m app.scripts.create_admin
```

## Monitoring

### Health Checks

```bash
# Basic health check
curl https://your-domain.com/health

# Detailed health check
curl https://your-domain.com/health/detailed
```

### Logs

```bash
# View all logs
docker-compose -f docker-compose.production.yml logs

# View specific service
docker-compose -f docker-compose.production.yml logs fastapi

# Follow logs
docker-compose -f docker-compose.production.yml logs -f
```

### Metrics

If Prometheus is enabled:
```bash
# Access metrics
curl http://localhost:9090/metrics
```

## Backup

### Database Backup

```bash
# Manual backup
docker-compose -f docker-compose.production.yml exec postgres pg_dump -U postgres autonomite > backup.sql

# Restore
docker-compose -f docker-compose.production.yml exec -T postgres psql -U postgres autonomite < backup.sql
```

### Automated Backups

Add to crontab:
```bash
# Daily backup at 2 AM
0 2 * * * /path/to/backup-script.sh
```

## Scaling

### Horizontal Scaling

1. **Load Balancer**: Use Nginx or HAProxy
2. **Multiple FastAPI Workers**: Increase `WORKERS` in .env
3. **Redis Cluster**: For high availability
4. **Database Replication**: Configure Supabase read replicas

### Vertical Scaling

Recommended specifications:
- **Small**: 2 CPU, 4GB RAM (up to 100 agents)
- **Medium**: 4 CPU, 8GB RAM (up to 500 agents)
- **Large**: 8+ CPU, 16GB+ RAM (1000+ agents)

## Troubleshooting

### Common Issues

1. **Container won't start**
   ```bash
   # Check logs
   docker-compose logs fastapi
   
   # Check permissions
   ls -la ./logs
   ```

2. **Database connection errors**
   ```bash
   # Test connection
   docker-compose exec fastapi python -c "from app.core.database import engine; engine.connect()"
   ```

3. **SSL issues**
   ```bash
   # Check certificate
   openssl x509 -in /path/to/cert.pem -text -noout
   ```

### Performance Tuning

1. **Nginx**: Adjust worker processes and connections
2. **FastAPI**: Tune worker count and type
3. **Redis**: Configure maxmemory policy
4. **Database**: Optimize connection pooling

## Security Checklist

- [ ] Change all default passwords
- [ ] Enable firewall (allow only 80, 443, 22)
- [ ] Disable root SSH access
- [ ] Set up fail2ban
- [ ] Regular security updates
- [ ] Monitor access logs
- [ ] Implement backup encryption
- [ ] Use secrets management system

## Maintenance

### Updates

```bash
# Pull latest changes
git pull origin main

# Rebuild and restart
docker-compose -f docker-compose.production.yml build
docker-compose -f docker-compose.production.yml up -d
```

### Log Rotation

Configure logrotate:
```
/path/to/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 www-data www-data
    sharedscripts
}
```