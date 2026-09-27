"""Verify only that the local NVIDIA API key can call the hosted model."""

import os

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()
key = os.getenv("NVIDIA_API_KEY", "")
if not key or key == "your_nvidia_api_key_here":
    raise SystemExit("NVIDIA_API_KEY is not set in .env")

model = os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")
client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=key, timeout=45.0)
response = client.chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "Reply with exactly: NVIDIA API OK"}],
    max_tokens=32,
    temperature=0,
)
print(f"MODEL={model}")
print(f"RESPONSE={(response.choices[0].message.content or '').strip()[:100]}")
