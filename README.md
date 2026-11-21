# Aural

Python client and examples for the [Aural Developer API](https://aural-ai.com/docs/developer-api) — an AI-powered interview platform for voice, chat, and video interviews.

## Setup

1. Create an API key in the [Aural dashboard](https://aural-ai.com) under **Settings → API Keys** (keys look like `dlv_...`).
2. Export it as an environment variable:

```bash
export AURAL_API_KEY="dlv_your_key_here"
```

No external dependencies — standard library only, Python 3.8+.

## Quick start

```python
from aural import AuralClient

client = AuralClient()  # reads AURAL_API_KEY from the environment

# 1. Create a draft interview
interview = client.create_interview(
    "Senior Backend Engineer — Screen",
    description="45-minute technical screen with system design follow-ups.",
    chatEnabled=True,
    voiceEnabled=True,
    videoEnabled=False,
    aiTone="PROFESSIONAL",
    followUpDepth="MODERATE",
    language="en",
)
interview_id = interview["id"]

# 2. Add questions (single object or array)
client.add_questions(interview_id, [
    {
        "text": "Walk me through a recent API you designed. What were the main tradeoffs?",
        "type": "OPEN_ENDED",
        "required": True,
        "followUpEnabled": True,
    },
    {
        "text": "Which persistence layer would you pick for write-heavy analytics?",
        "type": "SINGLE_CHOICE",
        "required": True,
        "options": ["Row store (Postgres)", "Column store", "Key-value", "Data warehouse"],
        "followUpEnabled": True,
    },
])

# 3. Publish for a public share link
published = client.publish_interview(interview_id)
print(published["url"])  # https://aural-ai.com/i/{publicSlug}
```

For named participants instead of a public link, skip publishing and create candidates (`POST /interviews/{id}/candidates`, up to 500 per request) and send each person their `inviteUrl`.

## Reading results

Transcripts and AI analytics live on **sessions**, not on the interview:

```python
for summary in client.iter_sessions(interview_id):  # cursor pagination handled for you
    session = client.wait_for_ai_fields(
        summary["id"], fields=("summary", "insights", "themes", "sentiment"), timeout_seconds=300
    )
    for message in session.get("messages", []):
        print(message["role"], message["content"], message["timestamp"])
    print(session.get("summary"), session.get("themes"), session.get("sentiment"))
```

AI fields populate asynchronously — `wait_for_ai_fields` handles the backoff-and-retry loop for you.

## Running the example

```bash
python examples/public_interview.py --title "Backend Screen" --poll
```

## Behavior notes

- **Rate limit**: 60 requests/minute per API key; the client retries 429s and 5xx with exponential backoff (`AuralError` is raised when retries are exhausted).
- **Plan limits**: `POST /interviews` is metered against templates and `POST .../candidates` against session hours. On `403 PLAN_LIMIT_EXCEEDED`, the raised `AuralError.payload` carries the full error object (including `limit` and `upgrade_url`); check `client.get_usage()` for live figures.
- **Pagination**: `list_interviews`/`list_sessions` return `(items, cursor)`; the `iter_*` variants loop until `cursor` is `None`.
- **Archives, not deletion**: `archive_interview` (DELETE) deactivates an interview; question DELETE is a hard delete.
- **Idempotency**: creation endpoints are not idempotent — de-duplicate before retrying prevents duplicate interviews/questions/candidates.
- **Exact schemas**: see the [OpenAPI spec](https://aural-ai.com/api/v1/openapi.json) and [full API reference](https://aural-ai.com/llms-full.txt).

## Tests

```bash
python -m unittest discover -s tests -v
```
