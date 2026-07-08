# -*- coding: utf-8 -*-
"""harden-onsite-redlines Task 3.1: LLM 4xx 快速失败, 429/5xx 重试不变."""

from __future__ import annotations

import pytest

from javert.tools.llm_provider import (
    LlmClientError,
    LlmUnavailableError,
    Qwen35Provider,
)


class _FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}], "usage": None}


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]):
        self.responses = responses
        self.n_posts = 0

    def post(self, url, json=None):
        self.n_posts += 1
        return self.responses[min(self.n_posts - 1, len(self.responses) - 1)]


def _provider_with(monkeypatch, client: _FakeClient) -> Qwen35Provider:
    p = Qwen35Provider()
    p._http_client = client
    monkeypatch.setattr("javert.tools.llm_provider.time.sleep", lambda s: None)
    return p


def test_400_raises_client_error_with_status_and_body(monkeypatch):
    client = _FakeClient([_FakeResponse(400, '{"error": "context length exceeded"}')])
    p = _provider_with(monkeypatch, client)
    with pytest.raises(LlmClientError) as ei:
        p.chat([{"role": "user", "content": "hi"}])
    assert "400" in str(ei.value)
    assert "context length exceeded" in str(ei.value)


def test_400_not_retried_by_chat_with_retry(monkeypatch):
    client = _FakeClient([_FakeResponse(400, "too long")])
    p = _provider_with(monkeypatch, client)
    with pytest.raises(LlmClientError):
        p.chat_with_retry([{"role": "user", "content": "hi"}], retries=3)
    assert client.n_posts == 1, "4xx 不应触发重试"


def test_429_still_retried(monkeypatch):
    client = _FakeClient([_FakeResponse(429, "rate limited")])
    p = _provider_with(monkeypatch, client)
    with pytest.raises(LlmUnavailableError):
        p.chat_with_retry([{"role": "user", "content": "hi"}], retries=3)
    assert client.n_posts == 3, "429 应照旧重试"


def test_503_still_retried_then_succeeds(monkeypatch):
    client = _FakeClient([_FakeResponse(503, "busy"), _FakeResponse(200)])
    p = _provider_with(monkeypatch, client)
    out = p.chat_with_retry([{"role": "user", "content": "hi"}], retries=3)
    assert out["content"] == "ok"
    assert client.n_posts == 2
