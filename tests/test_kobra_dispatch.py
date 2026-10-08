import sys
import types
import unittest
from unittest import mock

from test_kobra import (
    DummyWebRequest,
    KLIPPY_CONNECTION_NAME,
    load_kobra_module,
)


class ScriptRequest(DummyWebRequest):
    def get_str(self, name, default=None):
        return self.get_args().get(name, default)


class KobraGcodeDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = load_kobra_module()
        self.forwarded = []
        forwarded = self.forwarded

        class KlippyConnection:
            async def request(self, request):
                forwarded.append(request.get_args()['script'])
                return 'ok'

        class KlippyAPI:
            async def run_gcode(self, script, default=None):
                # Exercise the real two-level call structure: API -> connection.
                return await KlippyConnection().request(
                    ScriptRequest('gcode/script', {'script': script})
                )

        api_name = 'testsupport.moonraker.components.klippy_apis'
        self.modules = mock.patch.dict(sys.modules, {
            KLIPPY_CONNECTION_NAME: types.SimpleNamespace(KlippyConnection=KlippyConnection),
            api_name: types.SimpleNamespace(KlippyAPI=KlippyAPI),
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.api = KlippyAPI()
        self.connection = KlippyConnection()
        self.kobra = self.module.Kobra.__new__(self.module.Kobra)
        self.kobra.gcode_handlers = {}
        self.kobra.server = types.SimpleNamespace(error=RuntimeError, send_event=mock.Mock())
        self.kobra.is_goklipper_running = lambda: True
        self.kobra.patch_gcode_handler()

    async def test_restart_guard_covers_mixed_script_and_lowercase(self):
        self.kobra.patch_klipper_restart()
        script = "G28\nfirmware_restart ; don't restart\nM84\n"
        await self.api.run_gcode(script)
        self.assertEqual(self.forwarded, ['G28\n', 'M84\n'])
        self.kobra.server.send_event.assert_called_once()

    async def test_handled_command_does_not_swallow_following_movement(self):
        handler = mock.AsyncMock(return_value=None)
        self.kobra.register_gcode_handler('MMU_LOAD', handler)
        await self.api.run_gcode('MMU_LOAD GATE=0\nG1 X10 Y20\n')
        self.assertEqual(self.forwarded, ['G1 X10 Y20\n'])
        self.assertEqual(handler.call_args.args[0], {'GATE': '0'})

    async def test_delegate_forwards_only_its_line_without_second_interception(self):
        calls = []

        async def handler(args, delegate):
            calls.append(args)
            return await delegate()

        self.kobra.register_gcode_handler('CANCEL_PRINT', handler)
        script = 'G28\nCANCEL_PRINT ; comment\nM84'
        await self.api.run_gcode(script)
        self.assertEqual(calls, [{}])
        self.assertEqual(self.forwarded, ['G28\n', 'CANCEL_PRINT ; comment\n', 'M84'])

    async def test_unknown_gcode_preserves_raw_payload(self):
        script = '; unmatched quote is fine: don\'t\r\nRESPOND MSG="a; b"\nG1 X1.0\n'
        await self.api.run_gcode(script)
        self.assertEqual(self.forwarded, [script])

    async def test_arguments_keep_quoted_values_and_ignore_comments(self):
        handler = mock.AsyncMock(return_value=None)
        self.kobra.register_gcode_handler('MMU_SELECT', handler)
        await self.api.run_gcode('mmu_select gate=2 NAME="CANCEL_PRINT; O\'Brien" ; ignored\n')
        self.assertEqual(handler.call_args.args[0], {'GATE': '2', 'NAME': "CANCEL_PRINT; O'Brien"})
        self.assertEqual(self.forwarded, [])

    async def test_direct_request_preserves_original_request_arguments(self):
        handler = mock.AsyncMock(return_value=None)
        self.kobra.register_gcode_handler('MMU_LOAD', handler)
        script = 'G28\nMMU_LOAD\nM84'
        request = ScriptRequest('gcode/script', {'script': script, 'other': 7})
        await self.connection.request(request)
        self.assertEqual(self.forwarded, ['G28\n', 'M84'])
        self.assertEqual(request.get_args(), {'script': script, 'other': 7})
        handler.assert_awaited_once()

    async def test_nested_handler_commands_are_still_intercepted(self):
        self.kobra.patch_klipper_restart()

        async def handler(args, delegate):
            await self.api.run_gcode('firmware_restart')

        self.kobra.register_gcode_handler('MMU_LOAD', handler)
        await self.api.run_gcode('MMU_LOAD')
        self.assertEqual(self.forwarded, [])
        self.kobra.server.send_event.assert_called_once()

    async def test_filename_normalization_survives_dispatch(self):
        captured = []

        async def handler(args, delegate):
            captured.append(args)
            return await delegate()

        self.kobra.register_gcode_handler('SDCARD_PRINT_FILE', handler)
        await self.api.run_gcode('SDCARD_PRINT_FILE FILENAME=""cube test.gcode""')
        self.assertEqual(captured, [{'FILENAME': 'cube test.gcode'}])
        self.assertEqual(self.forwarded, ['SDCARD_PRINT_FILE FILENAME="cube test.gcode"'])

    async def test_empty_and_comment_only_scripts_do_not_crash(self):
        for script in ('', ' \n', '; comment with an unmatched "\n'):
            with self.subTest(script=script):
                self.forwarded.clear()
                await self.api.run_gcode(script)
                self.assertEqual(self.forwarded, [script])


if __name__ == '__main__':
    unittest.main()
