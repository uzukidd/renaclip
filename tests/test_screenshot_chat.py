import asyncio
from copy import deepcopy
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from openai_client.screenshot_chat import ScreenshotConversation


IMAGE = "data:image/png;base64,dGVzdA=="


def response(text="answer", status="completed"):
    return NS(status=status, error=None, output=[
        {"type": "reasoning", "id": "rs_test", "summary": [], "encrypted_content": "opaque"},
        {"type": "message", "id": "msg_test", "role": "assistant", "status": "completed",
         "content": [{"type": "output_text", "text": text, "annotations": []}]},
    ])


def chat(text="answer", reason="stop"):
    return NS(choices=[NS(message=NS(content=text), finish_reason=reason)])


class Stream:
    def __init__(self, events):
        self.events = events
        self.close = AsyncMock()

    def __aiter__(self):
        return self.iterate()

    async def iterate(self):
        for event in self.events:
            if isinstance(event, BaseException):
                raise event
            yield event


def success(interface, streaming, text="answer"):
    if interface == "responses":
        value = response(text)
        return Stream([NS(type="response.output_text.delta", delta=text),
                       NS(type="response.completed", response=value)]) if streaming else value
    return Stream([NS(choices=[NS(delta=NS(content=text), finish_reason=None)]),
                   NS(choices=[NS(delta=NS(content=None), finish_reason="stop")]),
                   NS(choices=[])]) if streaming else chat(text)


class ScreenshotChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.factory = patch("openai_client.screenshot_chat.get_openai_client")
        self.make_client = self.factory.start()
        self.addCleanup(self.factory.stop)
        self.client = NS(close=AsyncMock(), responses=NS(create=AsyncMock()),
                         chat=NS(completions=NS(create=AsyncMock())))
        self.make_client.return_value = self.client

    def conversation(self, interface, streaming=False):
        return ScreenshotConversation({"api_interface": interface, "streaming": streaming,
                                       "model": "mock-model", "api_key": "mock-key",
                                       "base_url": "https://mock.invalid/v1"}, IMAGE)

    def create(self, interface):
        return (self.client.responses.create if interface == "responses"
                else self.client.chat.completions.create)

    async def test_both_apis_modes_multiturn_and_first_image(self):
        for interface in ("responses", "chat_completions"):
            for streaming in (False, True):
                with self.subTest(interface=interface, streaming=streaming):
                    conversation = self.conversation(interface, streaming)
                    first = success(interface, streaming)
                    second = success(interface, streaming, "followup")
                    create = self.create(interface)
                    create.reset_mock()
                    create.side_effect = [first, second]
                    self.client.close.reset_mock()
                    deltas = []
                    self.assertEqual(await conversation.send("  question  ", deltas.append), "answer")
                    committed = conversation.history
                    self.assertEqual(await conversation.send("next", deltas.append), "followup")
                    key = "input" if interface == "responses" else "messages"
                    calls = [call.kwargs for call in create.call_args_list]
                    initial = calls[0][key][0]
                    self.assertEqual(initial["content"][0], {
                        "type": "input_text" if interface == "responses" else "text", "text": "question"})
                    expected_image = ({"type": "input_image", "image_url": IMAGE}
                                      if interface == "responses" else
                                      {"type": "image_url", "image_url": {"url": IMAGE}})
                    self.assertEqual(initial["content"][1], expected_image)
                    self.assertEqual(calls[1][key][:-1], committed)
                    self.assertEqual(len(calls[1][key][-1]["content"]), 1)
                    self.assertEqual(str(calls[1][key]).count(IMAGE), 1)
                    self.assertEqual(calls[0]["stream"], streaming)
                    self.assertEqual(calls[0]["model"], "mock-model")
                    if interface == "responses":
                        self.assertIs(calls[1]["store"], False)
                        self.assertNotIn("previous_response_id", calls[1])
                        self.assertIn("reasoning.encrypted_content", calls[1]["include"])
                        self.assertEqual(calls[1][key][1]["encrypted_content"], "opaque")
                    self.assertEqual(deltas, ["answer", "followup"] if streaming else [])
                    self.assertEqual(self.client.close.await_count, 2)
                    if streaming:
                        first.close.assert_awaited_once()
                        second.close.assert_awaited_once()

    async def test_optional_web_search_both_interfaces_and_streaming_modes(self):
        for interface in ("responses", "chat_completions"):
            for streaming in (False, True):
                for enabled in (False, True):
                    with self.subTest(interface=interface, streaming=streaming, enabled=enabled):
                        provider = {"api_interface": interface, "streaming": streaming,
                                    "model": "mock-model", "web_search": enabled}
                        conversation = ScreenshotConversation(provider, IMAGE)
                        provider["web_search"] = not enabled
                        create = self.create(interface)
                        create.reset_mock()
                        create.side_effect = None
                        create.return_value = success(interface, streaming)
                        await conversation.send("question")
                        kwargs = create.call_args.kwargs
                        if enabled:
                            self.assertEqual(kwargs["tools"], [{"type": "web_search"}])
                        else:
                            self.assertNotIn("tools", kwargs)
                        key = "input" if interface == "responses" else "messages"
                        self.assertEqual(len(kwargs[key][0]["content"]), 2)

    async def test_reasoning_effort_parameters(self):
        for interface in ("responses", "chat_completions"):
            for streaming in (False, True):
                for effort in ("default", "low", "medium", "high", "invalid"):
                    with self.subTest(interface=interface, streaming=streaming, effort=effort):
                        create = self.create(interface)
                        create.side_effect = None
                        create.return_value = success(interface, streaming)
                        conversation = ScreenshotConversation({"api_interface": interface,
                            "streaming": streaming, "reasoning_effort": effort}, IMAGE)
                        await conversation.send("question")
                        params = create.call_args.kwargs
                        key = "reasoning" if interface == "responses" else "reasoning_effort"
                        if effort in ("default", "invalid"):
                            self.assertNotIn(key, params)
                        else:
                            self.assertEqual(params[key], {"effort": effort} if interface == "responses" else effort)

    async def test_snapshot_and_defensive_history(self):
        provider = {"model": "original", "streaming": False, "models": ["original"]}
        conversation = ScreenshotConversation(provider, IMAGE)
        provider["model"] = "changed"
        provider["models"].append("changed")
        self.client.chat.completions.create.return_value = chat()
        await conversation.send("question")
        self.assertEqual(self.client.chat.completions.create.call_args.kwargs["model"], "original")
        history = conversation.history
        history[0]["content"].clear()
        self.assertEqual(len(conversation.history[0]["content"]), 2)

    async def test_empty_prompts_do_not_open_client(self):
        conversation = self.conversation("responses")
        for prompt in ("", " \n\t", None):
            with self.assertRaises(ValueError):
                await conversation.send(prompt)
        self.make_client.assert_not_called()
        self.assertEqual(conversation.history, [])

    async def test_create_failure_and_retry_preserve_first_image(self):
        for interface in ("responses", "chat_completions"):
            with self.subTest(interface=interface):
                conversation = self.conversation(interface)
                create = self.create(interface)
                create.side_effect = [OSError("offline"), success(interface, False)]
                self.client.close.reset_mock()
                with self.assertRaises(OSError):
                    await conversation.send("question")
                self.assertEqual(conversation.history, [])
                await conversation.send("retry")
                key = "input" if interface == "responses" else "messages"
                self.assertEqual(len(create.call_args.kwargs[key][0]["content"]), 2)
                self.assertEqual(self.client.close.await_count, 2)

    async def test_failed_followups_roll_back_for_all_modes(self):
        for interface in ("responses", "chat_completions"):
            for streaming in (False, True):
                with self.subTest(interface=interface, streaming=streaming):
                    conversation = self.conversation(interface, streaming)
                    failed = (Stream([OSError("interrupted")]) if streaming
                              else success(interface, False, "   "))
                    create = self.create(interface)
                    create.side_effect = [success(interface, streaming), failed,
                                          success(interface, streaming, "retried")]
                    await conversation.send("first")
                    before = conversation.history
                    with self.assertRaises((OSError, RuntimeError)):
                        await conversation.send("failed followup")
                    self.assertEqual(conversation.history, before)
                    self.assertEqual(await conversation.send("retry"), "retried")
                    key = "input" if interface == "responses" else "messages"
                    self.assertEqual(create.call_args.kwargs[key][:-1], before)
                    if streaming:
                        failed.close.assert_awaited_once()

    async def test_responses_failure_events_and_unterminated_stream(self):
        for kind in ("error", "response.failed", "response.incomplete", "truncated", "empty"):
            with self.subTest(kind=kind):
                events = [NS(type="response.output_text.delta", delta="partial")]
                if kind == "empty":
                    events.append(NS(type="response.completed", response=response("")))
                elif kind != "truncated":
                    events.append(NS(type=kind))
                stream = Stream(events)
                self.client.responses.create.return_value = stream
                self.client.close.reset_mock()
                conversation = self.conversation("responses", True)
                with self.assertRaises(RuntimeError):
                    await conversation.send("question")
                self.assertEqual(conversation.history, [])
                stream.close.assert_awaited_once()
                self.client.close.assert_awaited_once()

    async def test_responses_nonstream_bad_status(self):
        for status in ("failed", "incomplete", "in_progress", None):
            self.client.responses.create.return_value = response(status=status)
            conversation = self.conversation("responses")
            with self.assertRaises(RuntimeError):
                await conversation.send("question")
            self.assertEqual(conversation.history, [])

    async def test_chat_incomplete_or_empty(self):
        for streaming in (False, True):
            for reason in ("length", "content_filter", "tool_calls", None):
                with self.subTest(streaming=streaming, reason=reason):
                    result = (Stream([NS(choices=[NS(delta=NS(content="partial"),
                                                    finish_reason=reason)])])
                              if streaming else chat(reason=reason))
                    self.client.chat.completions.create.return_value = result
                    conversation = self.conversation("chat_completions", streaming)
                    with self.assertRaises(RuntimeError):
                        await conversation.send("question")
                    self.assertEqual(conversation.history, [])
                    if streaming:
                        result.close.assert_awaited_once()
        self.client.chat.completions.create.return_value = success("chat_completions", True, "")
        with self.assertRaises(RuntimeError):
            await self.conversation("chat_completions", True).send("question")

    async def test_async_callback_and_callback_failure(self):
        for interface in ("responses", "chat_completions"):
            conversation = self.conversation(interface, True)
            good = success(interface, True)
            bad = success(interface, True)
            self.create(interface).side_effect = [good, bad]
            callback = AsyncMock()
            await conversation.send("question", callback)
            callback.assert_awaited_once_with("answer")
            before = conversation.history
            with self.assertRaises(ValueError):
                await conversation.send("next", AsyncMock(side_effect=ValueError("callback")))
            self.assertEqual(conversation.history, before)
            bad.close.assert_awaited_once()

    async def test_concurrent_send_rejected_and_cancel_cleans_up(self):
        for interface in ("responses", "chat_completions"):
            with self.subTest(interface=interface):
                entered = asyncio.Event()
                release = asyncio.Event()

                async def callback(text):
                    entered.set()
                    await release.wait()

                stream = success(interface, True)
                self.create(interface).return_value = stream
                self.client.close.reset_mock()
                conversation = self.conversation(interface, True)
                task = asyncio.create_task(conversation.send("first", callback))
                await entered.wait()
                with self.assertRaises(RuntimeError):
                    await conversation.send("concurrent")
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(conversation.history, [])
                stream.close.assert_awaited_once()
                self.client.close.assert_awaited_once()
                self.create(interface).return_value = success(interface, True)
                self.assertEqual(await conversation.send("retry"), "answer")

    async def test_cancellation_during_client_cleanup_rolls_back(self):
        conversation = self.conversation("responses")
        self.client.responses.create.return_value = response()
        self.client.close.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await conversation.send("question")
        self.assertEqual(conversation.history, [])
        self.client.close.side_effect = None
        self.assertEqual(await conversation.send("retry"), "answer")

    async def test_missing_client_can_retry(self):
        self.make_client.return_value = None
        conversation = self.conversation("responses")
        with self.assertRaises(RuntimeError):
            await conversation.send("question")
        self.assertEqual(conversation.history, [])
        self.make_client.return_value = self.client
        self.client.responses.create.return_value = response()
        self.assertEqual(await conversation.send("retry"), "answer")

    async def test_sdk_response_output_serialization(self):
        from openai.types.responses import ResponseOutputMessage, ResponseReasoningItem

        value = response()
        original = deepcopy(value.output)
        value.output = [ResponseReasoningItem(**original[0]), ResponseOutputMessage(**original[1])]
        self.client.responses.create.return_value = value
        conversation = self.conversation("responses")
        self.assertEqual(await conversation.send("question"), "answer")
        self.assertEqual(conversation.history[1:], original)


if __name__ == "__main__":
    unittest.main()
