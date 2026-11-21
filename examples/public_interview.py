"""Create an Aural interview, add questions, publish it, and print the public link.

Usage:
    export AURAL_API_KEY="dlv_..."
    python examples/public_interview.py --title "Backend Screen" [--poll]
"""

import argparse
import sys

from aural import AuralClient, AuralError

QUESTIONS = [
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
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Create and publish a public Aural interview.")
    parser.add_argument("--title", default="Senior Backend Engineer — Screen")
    parser.add_argument("--description", default="45-minute technical screen with system design follow-ups.")
    parser.add_argument("--poll", action="store_true", help="Poll sessions and print completed results.")
    parser.add_argument("--poll-wait", type=float, default=60.0, help="Seconds between session polls.")
    parser.add_argument("--ai-wait", type=float, default=300.0, help="Max seconds to wait for AI fields per session.")
    args = parser.parse_args()

    try:
        client = AuralClient()
        interview = client.create_interview(
            args.title,
            description=args.description,
            chatEnabled=True,
            voiceEnabled=True,
            videoEnabled=False,
            aiTone="PROFESSIONAL",
            followUpDepth="MODERATE",
            language="en",
        )
        interview_id = interview["id"]
        print("Created interview: {}".format(interview_id))

        client.add_questions(interview_id, QUESTIONS)
        print("Added {} questions".format(len(QUESTIONS)))

        published = client.publish_interview(interview_id)
        print("Public link: {}".format(published.get("url") or published.get("publicSlug")))
    except AuralError as exc:
        if exc.is_plan_limit_exceeded:
            print("Plan limit hit ({}): {}".format(exc.code, exc.message), file=sys.stderr)
            print("Extra fields: {}".format(exc.payload), file=sys.stderr)
            try:
                usage = client.get_usage()
                print("Usage: {}".format(usage), file=sys.stderr)
            except AuralError:
                pass
            return 2
        print("{}: {}".format(exc.code, exc.message), file=sys.stderr)
        return 1

    if not args.poll:
        return 0

    seen = set()
    while True:
        for session_summary in client.iter_sessions(interview_id):
            session_id = session_summary["id"]
            if session_id in seen or session_summary.get("status") != "completed":
                continue
            seen.add(session_id)
            session = client.wait_for_ai_fields(session_id, timeout_seconds=args.ai_wait)
            print("\nSession {}".format(session_id))
            for message in session.get("messages", []):
                print("  [{}] {}: {}".format(message.get("timestamp"), message.get("role"), message.get("content")))
            print("  summary: {}".format(session.get("summary")))
            print("  insights: {}".format(session.get("insights")))
            print("  themes: {}".format(session.get("themes")))
            print("  sentiment: {}".format(session.get("sentiment")))

        print("\nWaiting {}s before next poll (Ctrl+C to stop)...".format(args.poll_wait))
        try:
            import time

            time.sleep(args.poll_wait)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
