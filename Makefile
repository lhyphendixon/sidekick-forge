.PHONY: help install dev test lint format clean build deploy

# Default target
help:
	@echo "Available commands:"
	@echo "  make install    - Install dependencies"
	@echo "  make dev        - Start development server"
	@echo "  make test       - Run tests"
	@echo "  make lint       - Run linters"
	@echo "  make format     - Format code"
	@echo "  make clean      - Clean up temporary files"
	@echo "  make build      - Build Docker images"
	@echo "  make deploy     - Deploy to production"

# Install dependencies
install:
	pip install -r requirements.txt
	pre-commit install

# Start development server
dev:
	docker-compose up -d
	docker-compose logs -f fastapi

# Run tests
test:
	pytest

test-coverage:
	pytest --cov=app --cov-report=html

# Lint code
lint:
	black --check app/
	isort --check-only app/
	pylint app/

# Format code
format:
	black app/
	isort app/

# Clean temporary files
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name ".coverage" -delete
	rm -rf htmlcov/
	rm -rf .pytest_cache/

# Build Docker images
build:
	docker-compose build

# Deploy to production
deploy:
	docker-compose -f docker-compose.production.yml build
	docker-compose -f docker-compose.production.yml up -d

# Database migrations
migrate:
	docker-compose exec fastapi python -m app.migrations

# Create admin user
create-admin:
	docker-compose exec fastapi python -m app.scripts.create_admin

# Backup database
backup:
	@echo "Creating database backup..."
	docker-compose exec postgres pg_dump -U postgres autonomite > backups/backup_$(shell date +%Y%m%d_%H%M%S).sql

# View logs
logs:
	docker-compose logs -f

logs-fastapi:
	docker-compose logs -f fastapi

logs-nginx:
	docker-compose logs -f nginx

# Health check
health:
	curl http://localhost:8000/health

# Stop all services
stop:
	docker-compose down

# Restart all services
restart: stop dev