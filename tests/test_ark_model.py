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
            pieces = [piece async for piece in ArkModel(Config(api_key="test-placeholder", base_url="https://example.invalid/v3", model="test-model")).stream([])]
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
                _ = [piece async for piece in ArkModel(Config(api_key="test-placeholder", base_url="https://example.invalid/v3", model="test-model")).stream([])]
            self.assertTrue(stream.closed)
            client.close.assert_awaited_once()

    async def test_auth_error_does_not_forward_provider_exception(self):
        client = client_with(None)
        response = httpx.Response(401, request=httpx.Request("POST", "https://example.invalid/chat"))
        client.chat.completions.create.side_effect = AuthenticationError("PRIVATE_PROVIDER_TEXT", response=response, body=None)
        with patch("openai.AsyncOpenAI", return_value=client), self.assertRaises(ModelError) as caught:
            _ = [piece async for piece in ArkModel(Config(api_key="test-placeholder", base_url="https://example.invalid/v3", model="test-model")).stream([])]
        self.assertEqual(caught.exception.code, "model_http_401")
        self.assertNotIn("PRIVATE_PROVIDER_TEXT", str(caught.exception))
        client.close.assert_awaited_once()

    async def test_missing_settings_fail_before_opening_a_client(self):
        for field in ("api_key", "base_url", "model"):
            config = Config(api_key="test-placeholder", base_url="https://example.invalid/v3", model="test-model")
            setattr(config, field, " ")
            with self.subTest(field=field), patch("openai.AsyncOpenAI") as constructor:
                with self.assertRaises(ModelError) as caught:
                    _ = [piece async for piece in ArkModel(config).stream([])]
                constructor.assert_not_called()
                self.assertIn(field, str(caught.exception))

    async def test_fragmented_parallel_tool_calls_and_thinking_override(self):
        def tool_chunk(index, call_id=None, name=None, arguments=None, finish=None):
            delta = SimpleNamespace(index=index, id=call_id,
                                    function=SimpleNamespace(name=name, arguments=arguments))
            return SimpleNamespace(usage=None, choices=[SimpleNamespace(
                delta=SimpleNamespace(content=None, tool_calls=[delta]), finish_reason=finish)])
        stream = FakeStream([
            tool_chunk(0, 'call-a', 'create_subagent', '{"instructions":'),
            tool_chunk(1, 'call-b', 'get_subagent', '{"agent_id":2}'),
            tool_chunk(0, arguments='"整理"}', finish='tool_calls'),
        ])
        client = client_with(stream)
        with patch('openai.AsyncOpenAI', return_value=client):
            pieces = [p async for p in ArkModel(Config(api_key='placeholder', base_url='https://example.invalid', model='test')).stream(
                [], tools=[{'type': 'function'}], thinking='enabled')]
        self.assertEqual(len(pieces), 1)
        self.assertEqual([c['id'] for c in pieces[0].tool_calls], ['call-a', 'call-b'])
        self.assertEqual(pieces[0].tool_calls[0]['function']['arguments'], '{"instructions":"整理"}')
        self.assertEqual(client.chat.completions.create.call_args.kwargs['extra_body']['thinking']['type'], 'enabled')
        self.assertTrue(stream.closed)

    async def test_incomplete_tool_arguments_never_escape_interrupted_stream(self):
        delta = SimpleNamespace(index=0, id='call-a', function=SimpleNamespace(name='run_shell', arguments='{"command":'))
        stream = FakeStream([SimpleNamespace(usage=None, choices=[SimpleNamespace(
            delta=SimpleNamespace(content=None, tool_calls=[delta]), finish_reason=None)])])
        client = client_with(stream)
        with patch('openai.AsyncOpenAI', return_value=client), self.assertRaises(ModelError):
            _ = [p async for p in ArkModel(Config(api_key='placeholder', base_url='https://example.invalid', model='test')).stream([])]
