"""Skill popup routing and network streaming tests; no real provider requests."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from concurrent.futures import Future

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from interfaces.application import Runtime
from interfaces.material_bridge import MaterialBridge
import config_loader


class Stream:
    def __init__(self, items):
        self.items = items
        self.close = AsyncMock()
    def __aiter__(self):
        async def generate():
            for item in self.items:
                yield item
        return generate()


class RequestRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_openai_streams_never_copy_when_output_callback_present(self):
        for interface in ('responses','chat_completions'):
            with self.subTest(interface=interface):
                delta = NS(type='response.output_text.delta',delta='Hello')
                chunk = NS(choices=[NS(delta=NS(content='Hello'))])
                stream = Stream([delta if interface=='responses' else chunk])
                client = NS(close=AsyncMock(),responses=NS(create=AsyncMock(return_value=stream)),
                            chat=NS(completions=NS(create=AsyncMock(return_value=stream))))
                callback = Mock()
                runtime = NS(executor=NS(post=lambda fn,*args:fn(*args)),set_clipboard=Mock())
                state = NS(backend='openai',openai_api_key='mock',openai_base_url='https://mock.invalid',
                    openai_model='test',openai_api_interface=interface,openai_reasoning_effort='high',
                    openai_streaming=True,specs=[{'name':'Skill','prompt':'Test','popup_mode':True}])
                with patch('openai_client.get_openai_client',return_value=client):
                    result = await Runtime._request(runtime,state,0,'input',on_delta=callback)
                self.assertEqual(result,'Hello')
                callback.assert_called_once_with('Hello')
                runtime.set_clipboard.assert_not_called()
                stream.close.assert_awaited_once()
                client.close.assert_awaited_once()

    async def test_clipboard_stream_mode_still_updates(self):
        stream=Stream([NS(choices=[NS(delta=NS(content='Text'))])])
        client=NS(chat=NS(completions=NS(create=AsyncMock(return_value=stream))),close=AsyncMock())
        runtime=NS(executor=NS(post=lambda f,*a:f(*a)),set_clipboard=Mock())
        state=NS(backend='openai',openai_api_key='mock',openai_base_url='',openai_model='m',
            openai_reasoning_effort='default',openai_api_interface='chat_completions',openai_streaming=True,
            specs=[{'prompt':''}])
        with patch('openai_client.get_openai_client',return_value=client):
            await Runtime._request(runtime,state,0,'input')
        runtime.set_clipboard.assert_called_once_with('Text')

    async def test_gemini_popup_stream_avoids_clipboard(self):
        stream=Stream([NS(text_delta='One'),NS(text_delta=' two')])
        chat=NS(send_message_stream=Mock(return_value=stream))
        client=NS(start_chat=Mock(return_value=chat),close=AsyncMock())
        runtime=NS(executor=NS(post=lambda f,*a:f(*a)),set_clipboard=Mock())
        state=NS(backend='gemini',model='unspecified',specs=[{'name':'Skill','prompt':''}])
        output=Mock()
        with patch('backend.initialize_client',new=AsyncMock(return_value=client)), \
                patch('gemini_client.get_or_create_gem',new=AsyncMock(return_value=(object(),False))), \
                patch('gemini_client.update_gem',new=AsyncMock(return_value=object())), \
                patch('gemini_client.delete_chat_after',new=AsyncMock()):
            result=await Runtime._request(runtime,state,0,'input',on_delta=output)
        self.assertEqual(result,'One two')
        self.assertEqual(output.call_count,2)
        runtime.set_clipboard.assert_not_called()


class RuntimePopupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_popup_option_roundtrip_and_runtime_force_stream_without_clipboard(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config_loader,'CONFIG_PATH',Path(folder)/'config.json'), \
                patch('interfaces.application.CONFIG_PATH',Path(folder)/'config.json'):
            config_loader.save_config([{'name':'Skill','prompt':''}],{})
            bridge=MaterialBridge(Mock())
            self.assertTrue(bridge.saveSkill(0,{'name':'Skill','prompt':'','popup_mode':True}))
            self.assertTrue(config_loader.load_config()[0][0]['popup_mode'])
            runtime=Runtime(self.app,hooks=False,show=False)
            captured={}
            def submit(coro,**kwargs):
                state=coro.cr_frame.f_locals['state']
                captured['streaming']=state.openai_streaming
                coro.close()
                captured.update(kwargs)
                return Future()
            try:
                self.app.clipboard().setText('Unchanged clipboard')
                with patch.object(runtime.executor,'submit',side_effect=submit):
                    runtime.run_skill(0)
                self.assertTrue(captured['streaming'])
                popup=next(iter(runtime.skill_popups))
                popup.append_text('Returned text')
                QTest.qWait(80)
                self.assertEqual(self.app.clipboard().text(),'Unchanged clipboard')
                captured['on_result']('Final response')
                self.assertEqual(self.app.clipboard().text(),'Unchanged clipboard')
                popup.close()
                self.assertFalse(runtime.skill_popups)
            finally:
                runtime.close()


if __name__=='__main__':
    unittest.main()
