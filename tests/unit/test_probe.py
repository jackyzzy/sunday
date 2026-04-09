"""Probe 单元测试（mock httpx，无真实网络请求）"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sunday.tools.health_store import ErrorType
from sunday.tools.probe import probe_tavily


@pytest.mark.asyncio
async def test_probe_tavily_no_api_key():
    with patch.dict(os.environ, {}, clear=False):
        # 确保 key 不存在
        env = {k: v for k, v in os.environ.items() if k != "TAVILY_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            result = await probe_tavily()
    assert not result.success
    assert result.error_type == ErrorType.AUTH_ERROR
    assert "TAVILY_API_KEY" in result.detail


@pytest.mark.asyncio
async def test_probe_tavily_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"results": []}'

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch.dict(os.environ, {"TAVILY_API_KEY": "sk-fake-key"}):
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await probe_tavily()

    assert result.success
    assert result.error_type is None


@pytest.mark.asyncio
async def test_probe_tavily_401_auth_error():
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized: invalid api key"

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch.dict(os.environ, {"TAVILY_API_KEY": "sk-wrong-key"}):
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await probe_tavily()

    assert not result.success
    assert result.error_type == ErrorType.AUTH_ERROR


@pytest.mark.asyncio
async def test_probe_tavily_429_rate_limit():
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = "Too Many Requests: rate limit exceeded"

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch.dict(os.environ, {"TAVILY_API_KEY": "sk-fake-key"}):
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await probe_tavily()

    assert not result.success
    assert result.error_type == ErrorType.RATE_LIMIT


@pytest.mark.asyncio
async def test_probe_tavily_network_error():
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=ConnectionError("Connection refused"))

    with patch.dict(os.environ, {"TAVILY_API_KEY": "sk-fake-key"}):
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await probe_tavily()

    assert not result.success
    assert result.error_type == ErrorType.NETWORK_ERROR
