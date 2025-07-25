# Contributing to Autonomite Agent Platform

Thank you for your interest in contributing to the Autonomite Agent Platform! This document provides guidelines and instructions for contributing.

## Code of Conduct

By participating in this project, you agree to abide by our Code of Conduct. Please read it before contributing.

## Getting Started

1. Fork the repository on GitHub
2. Clone your fork locally
3. Set up the development environment (see README.md)
4. Create a new branch for your feature or bugfix

## Development Process

### 1. Branch Naming

Use descriptive branch names:
- `feature/add-new-endpoint`
- `bugfix/fix-auth-issue`
- `docs/update-readme`
- `refactor/improve-performance`

### 2. Making Changes

- Write clean, readable code
- Follow the existing code style
- Add tests for new functionality
- Update documentation as needed
- Keep commits small and focused

### 3. Code Style

We use the following tools to maintain code quality:

```bash
# Format code with Black
black app/

# Sort imports with isort
isort app/

# Check code with pylint
pylint app/
```

### 4. Testing

- Write tests for all new code
- Ensure all tests pass before submitting
- Aim for high test coverage

```bash
# Run tests
pytest

# Run tests with coverage
pytest --cov=app

# Run specific test file
pytest tests/test_agents.py
```

### 5. Commit Messages

Follow the Conventional Commits specification:

```
type(scope): subject

body

footer
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Maintenance tasks

Example:
```
feat(agents): add voice configuration endpoint

- Add new endpoint for updating agent voice settings
- Support multiple TTS providers
- Include validation for voice parameters

Closes #123
```

## Pull Request Process

1. Update the README.md with details of changes if applicable
2. Update the CHANGELOG.md with your changes
3. Ensure all tests pass
4. Update documentation if you're changing functionality
5. Request review from maintainers

### PR Title Format

Use the same format as commit messages:
- `feat(agents): add bulk update functionality`
- `fix(auth): resolve token expiration issue`

### PR Description Template

```markdown
## Description
Brief description of what this PR does.

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] Manual testing completed

## Checklist
- [ ] Code follows project style guidelines
- [ ] Self-review completed
- [ ] Comments added for complex code
- [ ] Documentation updated
- [ ] No new warnings generated
```

## API Design Guidelines

### RESTful Principles

- Use proper HTTP methods (GET, POST, PUT, DELETE)
- Return appropriate status codes
- Use consistent URL patterns
- Include proper error responses

### Endpoint Naming

```
GET    /api/v1/agents           # List agents
POST   /api/v1/agents           # Create agent
GET    /api/v1/agents/{id}      # Get specific agent
PUT    /api/v1/agents/{id}      # Update agent
DELETE /api/v1/agents/{id}      # Delete agent
```

### Request/Response Format

- Use JSON for request and response bodies
- Include proper content-type headers
- Use camelCase for JSON fields
- Include pagination for list endpoints

## Database Guidelines

### Migrations

- Always create migrations for schema changes
- Test migrations both up and down
- Include clear descriptions

### Naming Conventions

- Tables: plural, snake_case (e.g., `agents`, `client_settings`)
- Columns: snake_case (e.g., `created_at`, `voice_provider`)
- Indexes: `idx_table_column` (e.g., `idx_agents_client_id`)

## Security Guidelines

- Never commit secrets or API keys
- Use environment variables for configuration
- Validate all user input
- Use parameterized queries
- Implement proper authentication and authorization

## Documentation

### Code Documentation

- Add docstrings to all functions and classes
- Use type hints for function parameters and returns
- Include examples in docstrings when helpful

```python
def create_agent(
    client_id: str,
    agent_data: AgentCreate,
    db: Session = Depends(get_db)
) -> Agent:
    """
    Create a new agent for a client.
    
    Args:
        client_id: The ID of the client
        agent_data: Agent creation data
        db: Database session
        
    Returns:
        The created agent
        
    Raises:
        HTTPException: If client not found or agent already exists
        
    Example:
        >>> agent_data = AgentCreate(name="Assistant", slug="assistant")
        >>> agent = create_agent("client-123", agent_data)
    """
```

### API Documentation

- Update OpenAPI descriptions
- Include example requests/responses
- Document all parameters
- Add operation IDs

## Questions or Need Help?

- Check existing issues and PRs
- Join our Discord community
- Email the development team

Thank you for contributing!