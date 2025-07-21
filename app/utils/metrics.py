"""
Application-wide metrics definitions
"""
from prometheus_client import Counter, Histogram

# HTTP metrics (defined in middleware/metrics.py)
# Importing here would cause circular imports

# Application-specific metrics
AGENT_TRIGGERS = Counter(
    "agent_triggers_total",
    "Total agent trigger requests",
    ["agent_slug", "mode", "status"]
)

CONTAINER_OPERATIONS = Counter(
    "container_operations_total",
    "Container operations",
    ["operation", "client_id", "status"]
)