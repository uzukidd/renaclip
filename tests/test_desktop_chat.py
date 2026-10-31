import asyncio
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from openai_client.desktop_chat import DesktopChatStore, request_chat, provider_identity


class Stream:
    def __init__(self, events):
        self.events = events
        self.closed = False
    def __aiter__(self):
        return self.iterate()
    async def iterate(self):
        for event in self.events:
            yield event
    async def close(self):
        self.closed = True


def completed(text='hello', response_id='resp_1'):
    return {'type': 'response.completed', 'response': {'status': 'completed', 'id': response_id,
             'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}]}}


class RequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_chain_images_reasoning_and_resource_cleanup(self):
        stream = Stream([{'type': 'response.output_text.delta', 'delta': 'hel'}, completed()])
        client = Mock()
        client.responses.create = AsyncMock(return_value=stream)
        client.close = AsyncMock()
        provider = {'name': 'test', 'model': 'vision', 'reasoning_effort': 'high', 'api_key': 'secret', 'web_search': True}
        image = {'name': 'a.png', 'data_url': 'data:image/png;base64,YQ=='}
        original = deepcopy(provider)
        seen = []
        with patch('openai_client.desktop_chat.get_openai_client', return_value=client):
            result = await request_chat(provider, 'question', [image], 'resp_previous', seen.append)
        params = client.responses.create.call_args.kwargs
        self.assertEqual(params['previous_response_id'], 'resp_previous')
        self.assertTrue(params['store'])
        self.assertTrue(params['stream'])
        self.assertEqual(params['tools'], [{'type': 'web_search'}])
        self.assertEqual(params['reasoning'], {'effort': 'high'})
        self.assertEqual(len(params['input']), 1)
        self.assertEqual(params['input'][0]['content'][1]['image_url'], image['data_url'])
        self.assertEqual(result, {'text': 'hello', 'response_id': 'resp_1'})
        self.assertEqual(provider, original)
        self.assertEqual(seen, ['hel'])
        self.assertTrue(stream.closed)
        client.close.assert_awaited_once()

    async def test_websocket_only_id_falls_back_to_history_once(self):
        error = RuntimeError('transport restriction')
        error.status_code = 400
        error.body = {'message': 'previous_response_id is only supported on Responses WebSocket v2'}
        client = Mock()
        client.responses.create = AsyncMock(side_effect=[error, Stream([completed()])])
        client.close = AsyncMock()
        history = [{'role': 'user', 'text': 'first', 'images': [{'data_url': 'data:image/png;base64,YQ=='}]},
                   {'role': 'assistant', 'text': 'prior answer'}]
        with patch('openai_client.desktop_chat.get_openai_client', return_value=client):
            result = await request_chat({}, 'next', [], 'resp_old', history=history)
        calls = client.responses.create.call_args_list
        self.assertEqual(calls[0].kwargs['previous_response_id'], 'resp_old')
        self.assertNotIn('previous_response_id', calls[1].kwargs)
        self.assertEqual(len(calls[1].kwargs['input']), 3)
        self.assertEqual(calls[1].kwargs['input'][0]['content'][1]['type'], 'input_image')
        self.assertTrue(result['use_history'])
        self.assertEqual(len(result['replay_input']), 4)
        # Subsequent turns skip the rejected ID path and retain native reasoning.
        native = result['replay_input'] + [{'type': 'reasoning', 'encrypted_content': 'opaque'}]
        client.responses.create = AsyncMock(return_value=Stream([completed(response_id='resp_2')]))
        with patch('openai_client.desktop_chat.get_openai_client', return_value=client):
            await request_chat({}, 'third', [], 'resp_1', history=history, replay_input=native, use_history=True)
        self.assertNotIn('previous_response_id', client.responses.create.call_args.kwargs)
        self.assertEqual(client.responses.create.call_args.kwargs['input'][-2]['type'], 'reasoning')

    async def test_unrelated_400_is_not_retried(self):
        error = RuntimeError('bad tool')
        error.status_code = 400
        error.body = {'message': 'web_search unsupported'}
        client = Mock()
        client.responses.create = AsyncMock(side_effect=error)
        client.close = AsyncMock()
        with patch('openai_client.desktop_chat.get_openai_client', return_value=client):
            with self.assertRaises(RuntimeError):
                await request_chat({}, 'next', [], 'resp_old', history=[])
        self.assertEqual(client.responses.create.await_count, 1)

    async def test_no_previous_id_on_first_turn(self):
        client = Mock()
        client.responses.create = AsyncMock(return_value=Stream([completed()]))
        client.close = AsyncMock()
        with patch('openai_client.desktop_chat.get_openai_client', return_value=client):
            await request_chat({'model': 'test'}, 'question', [], None)
        self.assertNotIn('previous_response_id', client.responses.create.call_args.kwargs)
        self.assertNotIn('tools', client.responses.create.call_args.kwargs)

    async def test_incomplete_stream_and_missing_id_fail(self):
        for events in ([{'type': 'response.output_text.delta', 'delta': 'partial'}],
                       [{'type': 'response.failed'}], [completed(response_id='')]):
            stream = Stream(events)
            client = Mock()
            client.responses.create = AsyncMock(return_value=stream)
            client.close = AsyncMock()
            with patch('openai_client.desktop_chat.get_openai_client', return_value=client):
                with self.assertRaises(RuntimeError):
                    await request_chat({}, 'x', [], None)
            self.assertTrue(stream.closed)
            client.close.assert_awaited_once()

    async def test_cancel_closes_stream_and_client(self):
        stream = Stream([{'type': 'response.output_text.delta', 'delta': 'partial'}])
        client = Mock()
        client.responses.create = AsyncMock(return_value=stream)
        client.close = AsyncMock()
        def cancel(_):
            raise asyncio.CancelledError()
        with patch('openai_client.desktop_chat.get_openai_client', return_value=client):
            with self.assertRaises(asyncio.CancelledError):
                await request_chat({}, 'x', [], None, cancel)
        self.assertTrue(stream.closed)
        client.close.assert_awaited_once()


class StoreTests(unittest.TestCase):
    def test_persist_clear_credentials_and_corrupt_history(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'history.json'
            store = DesktopChatStore(path)
            state = store.load()
            state.update(messages=[{'role': 'user', 'text': 'hello', 'images': []}], previous_response_id='resp_1', api_key='SECRET')
            store.save(state)
            self.assertNotIn('SECRET', path.read_text(encoding='utf-8'))
            self.assertEqual(store.load()['previous_response_id'], 'resp_1')
            prior = path.read_bytes()
            with patch('openai_client.desktop_chat.os.replace', side_effect=OSError('failure')):
                with self.assertRaises(OSError):
                    store.save({'messages': []})
            self.assertEqual(path.read_bytes(), prior)
            path.write_text('{broken', encoding='utf-8')
            self.assertIn('load_warning', store.load())
            self.assertEqual(path.read_text(encoding='utf-8'), '{broken')
            store.clear()
            self.assertEqual(store.load()['messages'], [])
            self.assertIsNone(store.load()['previous_response_id'])

    def test_identity_does_not_contain_credentials(self):
        self.assertEqual(provider_identity({'name': 'a', 'api_key': 'x'}), provider_identity({'name': 'a', 'api_key': 'y'}))
        self.assertNotEqual(provider_identity({'name': 'a'}), provider_identity({'name': 'b'}))


if __name__ == '__main__':
    unittest.main()
