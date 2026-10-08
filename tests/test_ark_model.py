"""Provider edge cases without network or real credentials."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from openai import AuthenticationError
from backend.config import Config
from backend.harness import ArkModel, ModelError


class FakeStream:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.chunks)
        except StopIteration:
            raise StopAsyncIteration

    async def close(self):
        self.closed = True


def chunk(text=None, finish=None):
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=SimpleNamespace(content=text), finish_reason=finish)])


def client_with(stream):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=stream))), close=AsyncMock())


class ArkModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_choices_and_text_stream_cleanup(self):
        stream = FakeStream([SimpleNamespace(usage=None, choices=[]), chunk("你好"), chunk(finish="stop")])
        client = client_with(stream)
        with patch("openai.AsyncOpenAI", return_value=client) as constructor:
            pieces = [piece async for piece in ArkModel(Config(api_key="test-placeholder")).stream([])]
        self.assertEqual([p.text for p in pieces], ["你好"])
        self.assertTrue(stream.closed)
        client.close.assert_awaited_once()
        self.assertEqual(constructor.call_args.kwargs["max_retries"], 0)
        call = client.chat.completions.create.call_args.kwargs
        self.assertTrue(call["stream"])
        self.assertEqual(call["extra_body"], {"thinking": {"type": "disabled"}})

    async def test_eof_and_length_finish_are_not_success(self):
        for finish in (None, "length", "content_filter"):
            stream = FakeStream([chunk("部分", finish)])
            client = client_with(stream)
            with patch("openai.AsyncOpenAI", return_value=client), self.assertRaises(ModelError):
                _ = [piece async for piece in ArkModel(Config(api_key="test-placeholder")).stream([])]
            self.assertTrue(stream.closed)
            client.close.assert_awaited_once()

    async def test_auth_error_does_not_forward_provider_exception(self):
        client = client_with(None)
        response = httpx.Response(401, request=httpx.Request("POST", "https://example.invalid/chat"))
        client.chat.completions.create.side_effect = AuthenticationError("PRIVATE_PROVIDER_TEXT", response=response, body=None)
        with patch("openai.AsyncOpenAI", return_value=client), self.assertRaises(ModelError) as caught:
            _ = [piece async for piece in ArkModel(Config(api_key="test-placeholder")).stream([])]
        self.assertEqual(caught.exception.code, "model_http_401")
        self.assertNotIn("PRIVATE_PROVIDER_TEXT", str(caught.exception))
        client.close.assert_awaited_once()
