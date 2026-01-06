# Intentive Voice Agent

AI Accountability Coach using LiveKit + Gemini Live API.

## Setup

1. Create a virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # On macOS/Linux
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Copy environment variables:
```bash
cp .env.example .env
```

4. Fill in your API keys in `.env`:
   - `GOOGLE_API_KEY` - From Google AI Studio
   - `LIVEKIT_API_KEY` - From LiveKit Cloud
   - `LIVEKIT_API_SECRET` - From LiveKit Cloud
   - `LIVEKIT_URL` - Your LiveKit Cloud URL

## Development

Run locally in dev mode:
```bash
python agent.py dev
```

## Deploy to LiveKit Cloud

```bash
livekit-cli deploy
```

## Testing

Ask the agent to use the `hello_world` tool to verify tool calling works:
> "Hey, can you test the hello world tool with my name?"
