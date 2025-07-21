#!/usr/bin/env python3
"""
Populate agents data for the Autonomite client
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis
import json
from datetime import datetime

# Connect to Redis
redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)

# Define the agents for Autonomite
agents = [
    {
        "id": "agent-1",
        "client_id": "autonomite",
        "slug": "sales-assistant",
        "name": "Sales Assistant",
        "description": "Professional sales support agent with product knowledge and lead qualification capabilities",
        "agent_image": "https://example.com/sales-assistant.png",
        "system_prompt": "You are a professional sales assistant for Autonomite. You help customers understand our AI agent platform, answer questions about features and pricing, and qualify leads. Be helpful, professional, and focus on understanding customer needs.",
        "voice_settings": {
            "provider": "livekit",
            "voice_id": "alloy",
            "temperature": 0.7,
            "provider_config": {
                "model": "gpt-4",
                "temperature": 0.7
            }
        },
        "webhooks": {
            "voice_context_webhook_url": "https://n8n.autonomite.ai/webhook/voice-context",
            "text_context_webhook_url": "https://n8n.autonomite.ai/webhook/text-context"
        },
        "enabled": True,
        "tools_config": {
            "enabled_tools": ["calendar", "email", "crm"]
        },
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-07-14T00:00:00Z",
        "active": True
    },
    {
        "id": "agent-2",
        "client_id": "autonomite",
        "slug": "support-agent",
        "name": "Technical Support Agent",
        "description": "Expert technical support agent for troubleshooting and customer assistance",
        "agent_image": "https://example.com/support-agent.png",
        "system_prompt": "You are a technical support specialist for Autonomite. Help users troubleshoot issues with their AI agents, provide technical guidance, and escalate complex issues when needed. Be patient, clear, and solution-focused.",
        "voice_settings": {
            "provider": "livekit",
            "voice_id": "nova",
            "temperature": 0.5,
            "provider_config": {
                "model": "gpt-4",
                "temperature": 0.5
            }
        },
        "webhooks": {
            "voice_context_webhook_url": "https://n8n.autonomite.ai/webhook/support-voice",
            "text_context_webhook_url": "https://n8n.autonomite.ai/webhook/support-text"
        },
        "enabled": True,
        "tools_config": {
            "enabled_tools": ["ticketing", "knowledge_base", "screen_share"]
        },
        "created_at": "2025-01-15T00:00:00Z",
        "updated_at": "2025-07-14T00:00:00Z",
        "active": True
    },
    {
        "id": "agent-3",
        "client_id": "autonomite",
        "slug": "onboarding-specialist",
        "name": "Onboarding Specialist",
        "description": "Guides new customers through setup and best practices",
        "agent_image": "https://example.com/onboarding-agent.png",
        "system_prompt": "You are an onboarding specialist for Autonomite. Guide new customers through setting up their first AI agent, explain best practices, and ensure they have a smooth start. Be encouraging, thorough, and check for understanding.",
        "voice_settings": {
            "provider": "livekit",
            "voice_id": "echo",
            "temperature": 0.6,
            "provider_config": {
                "model": "gpt-4",
                "temperature": 0.6
            }
        },
        "webhooks": {
            "voice_context_webhook_url": "https://n8n.autonomite.ai/webhook/onboarding-voice",
            "text_context_webhook_url": "https://n8n.autonomite.ai/webhook/onboarding-text"
        },
        "enabled": True,
        "tools_config": {
            "enabled_tools": ["tutorial_videos", "documentation", "checklist"]
        },
        "created_at": "2025-02-01T00:00:00Z",
        "updated_at": "2025-07-14T00:00:00Z",
        "active": True
    },
    {
        "id": "agent-4",
        "client_id": "autonomite",
        "slug": "marketing-assistant",
        "name": "Marketing Assistant",
        "description": "Creative marketing support for content and campaign ideas",
        "agent_image": "https://example.com/marketing-agent.png",
        "system_prompt": "You are a creative marketing assistant for Autonomite. Help create compelling content, suggest marketing strategies, and provide insights on AI agent use cases for different industries. Be creative, data-driven, and brand-conscious.",
        "voice_settings": {
            "provider": "livekit",
            "voice_id": "fable",
            "temperature": 0.8,
            "provider_config": {
                "model": "gpt-4",
                "temperature": 0.8
            }
        },
        "webhooks": {
            "voice_context_webhook_url": None,
            "text_context_webhook_url": None
        },
        "enabled": True,
        "tools_config": {
            "enabled_tools": ["analytics", "content_generator", "social_media"]
        },
        "created_at": "2025-02-15T00:00:00Z",
        "updated_at": "2025-07-14T00:00:00Z",
        "active": False  # Example of inactive agent
    }
]

# Store agents in Redis
for agent in agents:
    # Store individual agent
    cache_key = f"agent:autonomite:{agent['slug']}"
    redis_client.setex(cache_key, 86400, json.dumps(agent))  # 24 hour TTL
    print(f"Stored agent: {agent['name']} ({agent['slug']})")

# Store list of agent slugs for the client
agent_slugs = [agent['slug'] for agent in agents]
client_agents_key = "agents:client:autonomite"
redis_client.setex(client_agents_key, 86400, json.dumps(agent_slugs))  # 24 hour TTL
print(f"\nStored {len(agents)} agents for client autonomite")

# Also create a configuration mapping to sync with agent_configurations table
# This maps agent slugs to their latest configuration
config_mapping = {}
for agent in agents:
    config_key = f"agent_config:autonomite:{agent['slug']}"
    config_data = {
        "slug": agent['slug'],
        "name": agent['name'],
        "system_prompt": agent['system_prompt'],
        "voice_settings": agent['voice_settings'],
        "webhooks": agent['webhooks'],
        "tools_config": agent['tools_config'],
        "enabled": agent['enabled'],
        "last_updated": datetime.utcnow().isoformat()
    }
    redis_client.setex(config_key, 86400, json.dumps(config_data))  # 24 hour TTL
    config_mapping[agent['slug']] = config_data

print(f"\nCreated configuration mapping for {len(config_mapping)} agents")
print("\nAgents successfully populated in Redis!")