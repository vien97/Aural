"""Minimal Python client for the Aural Developer API (https://aural-ai.com).

Standard library only. See https://aural-ai.com/SKILL.md and
https://aural-ai.com/api/v1/openapi.json for the authoritative API reference.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

DEFAULT_BASE_URL = "https://aural-ai.com/api/v1"

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

Transport = Callable[[str, str, Dict[str, str], Optional[bytes], float], Tuple[int, str]]


class AuralError(Exception):
    """Error returned by the Aural API.

    ``payload`` holds the full ``error`` object, which for
    ``PLAN_LIMIT_EXCEEDED`` carries extra fields such as ``limit``,
    ``current``/``max`` and ``upgrade_url``.
    """

    def __init__(self, status_code: int, code: str, message: str, payload: Optional[Dict[str, Any]] = None) -> None:
        super().__init__("[{}] {}: {}".format(status_code, code, message))
        self.status_code = status_code
        self.code = code
        self.message = message
        self.payload = dict(payload or {})

    @property
    def is_plan_limit_exceeded(self) -> bool:
        return self.code == "PLAN_LIMIT_EXCEEDED"


def _default_transport(method: str, url: str, headers: Dict[str, str], body: Optional[bytes], timeout: float) -> Tuple[int, str]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


_INTERVIEW_FIELD_MAP = {
    "description": "description",
    "objective": "objective",
    "assessment_criteria": "assessmentCriteria",
    "assessmentCriteria": "assessmentCriteria",
    "chat_enabled": "chatEnabled",
    "chatEnabled": "chatEnabled",
    "voice_enabled": "voiceEnabled",
    "voiceEnabled": "voiceEnabled",
    "video_enabled": "videoEnabled",
    "videoEnabled": "videoEnabled",
    "ai_name": "aiName",
    "aiName": "aiName",
    "ai_tone": "aiTone",
    "aiTone": "aiTone",
    "follow_up_depth": "followUpDepth",
    "followUpDepth": "followUpDepth",
    "language": "language",
    "time_limit_minutes": "timeLimitMinutes",
    "timeLimitMinutes": "timeLimitMinutes",
    "anti_cheating_enabled": "antiCheatingEnabled",
    "antiCheatingEnabled": "antiCheatingEnabled",
}


class AuralClient:
    """Thin synchronous client for the Aural interview API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        user_agent: str = "aural-python-client/0.1.0",
        transport: Optional[Transport] = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("AURAL_API_KEY")
        if not resolved_key:
            raise ValueError(
                "An Aural API key is required: pass api_key or set the AURAL_API_KEY environment variable"
            )
        self._api_key = resolved_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._user_agent = user_agent
        self._transport = transport or _default_transport

    # ------------------------------------------------------------------ #
    # Low level
    # ------------------------------------------------------------------ #

    def _request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None, json_body: Any = None) -> Dict[str, Any]:
        """Return the full JSON envelope ({data, cursor} or data)."""
        url = self._base_url + path
        if params:
            query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
            url = "{}?{}".format(url, query)

        headers = {
            "Authorization": "Bearer {}".format(self._api_key),
            "Accept": "application/json",
            "User-Agent": self._user_agent,
        }
        body: Optional[bytes] = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        attempt = 0
        while True:
            status, raw = self._transport(method, url, headers, body, self._timeout)
            if status in RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                time.sleep(self._backoff_seconds * (2 ** attempt))
                attempt += 1
                continue
            return self._parse_envelope(method, url, status, raw)

    def _parse_envelope(self, method: str, url: str, status: int, raw: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    payload = parsed
            except ValueError:
                pass

        if 200 <= status < 300:
            return payload

        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        raise AuralError(
            status,
            error.get("code", "HTTP_ERROR"),
            error.get("message", "{} {} failed with status {}".format(method, url, status)),
            payload=error,
        )

    # ------------------------------------------------------------------ #
    # Interviews
    # ------------------------------------------------------------------ #

    def create_interview(self, title: str, **optional: Any) -> Dict[str, Any]:
        """Create a draft interview (subject to the templates plan limit)."""
        if not isinstance(title, str) or not title.strip():
            raise ValueError("create_interview requires a non-empty title")
        body: Dict[str, Any] = {"title": title}
        for name, value in optional.items():
            if value is None:
                continue
            field = _INTERVIEW_FIELD_MAP.get(name)
            if field is None:
                raise ValueError("Unknown interview field: {!r}".format(name))
            body[field] = value
        response = self._request("POST", "/interviews", json_body=body)
        return response.get("data", {})

    def get_interview(self, interview_id: str) -> Dict[str, Any]:
        """Fetch one interview including its ordered questions."""
        response = self._request("GET", "/interviews/{}".format(interview_id))
        return response.get("data", {})

    def list_interviews(self, limit: int = 20, cursor: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Return (interviews, next_cursor). Pass next_cursor back to page again."""
        limit = max(1, min(int(limit), 100))
        response = self._request("GET", "/interviews", params={"limit": limit, "cursor": cursor})
        return response.get("data") or [], response.get("cursor")

    def iter_interviews(self, limit: int = 20) -> Iterator[Dict[str, Any]]:
        cursor: Optional[str] = None
        while True:
            items, cursor = self.list_interviews(limit=limit, cursor=cursor)
            yield from items
            if not cursor:
                return

    def update_interview(self, interview_id: str, **fields: Any) -> Dict[str, Any]:
        """Patch selected interview fields. Note: invalid aiTone/followUpDepth return 400 on PATCH."""
        body = {}
        for name, value in fields.items():
            if value is None:
                continue
            field = _INTERVIEW_FIELD_MAP.get(name)
            if field is None:
                raise ValueError("Unknown interview field: {!r}".format(name))
            body[field] = value
        if not body:
            raise ValueError("update_interview requires at least one field to change")
        response = self._request("PATCH", "/interviews/{}".format(interview_id), json_body=body)
        return response.get("data", {})

    def archive_interview(self, interview_id: str) -> Dict[str, Any]:
        """Soft-delete (archive) an interview; DELETE is not a hard delete."""
        response = self._request("DELETE", "/interviews/{}".format(interview_id))
        return response.get("data", {})

    def publish_interview(self, interview_id: str) -> Dict[str, Any]:
        """Publish for public access. Returns {id, publicSlug, url}."""
        response = self._request("POST", "/interviews/{}/publish".format(interview_id), json_body={})
        return response.get("data", {})

    # ------------------------------------------------------------------ #
    # Questions
    # ------------------------------------------------------------------ #

    def add_questions(self, interview_id: str, questions: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Add one or more questions. API fields: text, type, required, options, followUpEnabled."""
        body = list(questions)
        if not body:
            raise ValueError("add_questions requires at least one question")
        response = self._request("POST", "/interviews/{}/questions".format(interview_id), json_body=body)
        data = response.get("data", [])
        if isinstance(data, list):
            return data
        return [data]

    def list_questions(self, interview_id: str) -> List[Dict[str, Any]]:
        response = self._request("GET", "/interviews/{}/questions".format(interview_id))
        data = response.get("data", [])
        return data if isinstance(data, list) else [data]

    # ------------------------------------------------------------------ #
    # Sessions
    # ------------------------------------------------------------------ #

    def list_sessions(self, interview_id: str, limit: int = 100, cursor: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Return (session_summaries, next_cursor) for one interview."""
        limit = max(1, min(int(limit), 100))
        response = self._request("GET", "/interviews/{}/sessions".format(interview_id), params={"limit": limit, "cursor": cursor})
        return response.get("data") or [], response.get("cursor")

    def iter_sessions(self, interview_id: str, limit: int = 100) -> Iterator[Dict[str, Any]]:
        cursor: Optional[str] = None
        while True:
            items, cursor = self.list_sessions(interview_id, limit=limit, cursor=cursor)
            yield from items
            if not cursor:
                return

    def get_session(self, session_id: str) -> Dict[str, Any]:
        """Full session: participant fields, messages transcript, AI analytics."""
        response = self._request("GET", "/sessions/{}".format(session_id))
        return response.get("data", {})

    def wait_for_ai_fields(
        self,
        session_id: str,
        fields: Iterable[str] = ("summary", "insights", "themes", "sentiment"),
        timeout_seconds: float = 300.0,
        poll_seconds: float = 5.0,
    ) -> Dict[str, Any]:
        """Poll a session until AI analytics fields are populated.

        summary/insights/themes/sentiment populate asynchronously; this returns
        the session as soon as every requested field is non-empty (missing or
        not provided), or the last seen session when the timeout elapses.
        """
        wanted = tuple(fields)
        deadline = time.monotonic() + timeout_seconds
        session: Dict[str, Any] = {}
        while True:
            session = self.get_session(session_id)
            if all(session.get(field) for field in wanted):
                return session
            if time.monotonic() >= deadline:
                return session
            time.sleep(poll_seconds)

    # ------------------------------------------------------------------ #
    # Usage
    # ------------------------------------------------------------------ #

    def get_usage(self) -> Dict[str, Any]:
        """Plan name and consumption against key limits (templates, session_hours, ai_tokens, seats)."""
        response = self._request("GET", "/usage")
        return response.get("data", {})
