#!/usr/bin/env python3
import requests
import json

api_key = "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"
url = "https://api.groq.com/openai/v1/chat/completions"

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

data = {
    "model": "llama-3.3-70b-versatile",
    "messages": [{"role": "user", "content": "Hello, please respond with OK"}],
    "max_tokens": 10
}

print("Testing Groq API...")
try:
    response = requests.post(url, headers=headers, json=data, timeout=10)
    print(f"Status: {response.status_code}")
    result = response.json()
    if response.status_code == 200:
        print(f"Success! Response: {result['choices'][0]['message']['content']}")
    else:
        print(f"Error: {result}")
except Exception as e:
    print(f"Connection error: {e}")