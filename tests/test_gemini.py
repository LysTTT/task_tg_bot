import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr

from core.services import gemini


@pytest.mark.asyncio
async def test_gemini_classifies_messages_and_returns_aware_deadlines(monkeypatch):
    response = MagicMock(status=200, json=AsyncMock())
    request = MagicMock()
    request.post.return_value.__aenter__.return_value = response
    client = MagicMock()
    client.return_value.__aenter__.return_value = request
    monkeypatch.setattr(gemini.aiohttp, "ClientSession", client)
    settings = SimpleNamespace(gemini_api_key=SecretStr("test"), gemini_model="test-model")
    now = datetime(2026, 11, 18, 10, tzinfo=ZoneInfo("Europe/Moscow"))

    cases = (
        ("Купить хлеб 18 декабря в 6 вечера", "task", "Купить хлеб", "2026-12-18T18:00:00+03:00"),
        ("Купить хлеб", "task", "Купить хлеб", None),
        ("Привет!", "not_task", "", None),
        ("Купить хлеб когда-нибудь потом", "unclear_deadline", "Купить хлеб", None),
    )
    for text, kind, title, due_at in cases:
        raw = json.dumps({"kind": kind, "title": title, "due_at": due_at})
        response.json.return_value = {"candidates": [{"content": {"parts": [{"text": raw}]}}]}
        result = await gemini.parse_task(settings, now, "Europe/Moscow", text=text)
        assert (result.kind, result.title) == (kind, title)
        assert gemini.TaskDraft.model_validate_json(result.model_dump_json()) == result
        if due_at:
            assert result.due_at.astimezone(UTC) == datetime(2026, 12, 18, 15, tzinfo=UTC)
        else:
            assert result.due_at is None
        payload = request.post.call_args.kwargs["json"]
        assert payload["contents"] == [{"parts": [{"text": text}]}]
        assert "Europe/Moscow" in payload["systemInstruction"]["parts"][0]["text"]
