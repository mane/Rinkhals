import types
import unittest
from unittest import mock

from test_mmu_ace import load_mmu_ace_module


class MmuFilamentOperationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = load_mmu_ace_module()
        self.patcher = self.module.MmuAcePatcher.__new__(self.module.MmuAcePatcher)
        self.patcher.ace = self.module.MmuAce()
        # The production class currently shares this default across instances.
        self.patcher.ace.filament = self.module.MmuAceFilament()
        self.patcher.ace.loaded_gate = 0
        self.patcher.ace.gate = 0
        self.patcher.ace.filament.pos = self.module.FILAMENT_POS_LOADED
        self.patcher._ensure_extruder_temp = mock.AsyncMock(return_value=True)
        self.patcher._send_gcode_response = mock.AsyncMock()
        self.printer = types.SimpleNamespace(send_gcode=mock.AsyncMock())
        self.patcher.ace_controller = types.SimpleNamespace(
            printer=self.printer,
            _handle_status_update=mock.Mock(),
        )

    async def test_failed_unload_prevents_loading_another_gate(self):
        self.printer.send_gcode.side_effect = RuntimeError('unwind failed')
        await self.patcher._on_gcode_mmu_load({'GATE': '1'}, None)
        self.printer.send_gcode.assert_awaited_once_with(
            'UNWIND_FILAMENT ID=0 INDEX=0 LENGTH=100 SPEED=20'
        )
        self.assertEqual(self.patcher.ace.loaded_gate, 0)
        self.assertEqual(self.patcher.ace.filament.pos, self.module.FILAMENT_POS_LOADED)
        self.patcher._send_gcode_response.assert_any_await(
            'MMU_LOAD: Unload failed - aborting gate change'
        )

    async def test_unload_heating_failure_prevents_gate_change(self):
        self.patcher._ensure_extruder_temp.return_value = False
        await self.patcher._on_gcode_mmu_load({'GATE': '1'}, None)
        self.printer.send_gcode.assert_not_awaited()
        self.assertEqual(self.patcher.ace.loaded_gate, 0)

    async def test_successful_unload_precedes_new_load(self):
        await self.patcher._on_gcode_mmu_load({'GATE': '1'}, None)
        self.assertEqual(self.printer.send_gcode.await_args_list, [
            mock.call('UNWIND_FILAMENT ID=0 INDEX=0 LENGTH=100 SPEED=20'),
            mock.call('FEED_FILAMENT ID=0 INDEX=1 LENGTH=100 SPEED=25'),
        ])
        self.assertEqual(self.patcher.ace.loaded_gate, 1)
        self.assertEqual(self.patcher.ace.filament.pos, self.module.FILAMENT_POS_LOADED)

    async def test_manual_unload_keeps_gcode_handler_response(self):
        result = await self.patcher._on_gcode_mmu_unload({'GATE': '0'}, None)
        self.assertIsNone(result)
        self.assertEqual(self.patcher.ace.loaded_gate, self.module.TOOL_GATE_UNKNOWN)

    async def test_successful_eject_clears_loaded_state(self):
        await self.patcher._on_gcode_mmu_eject({'GATE': '0'}, None)
        self.assertEqual(self.patcher.ace.loaded_gate, self.module.TOOL_GATE_UNKNOWN)
        self.assertEqual(self.patcher.ace.filament.pos, self.module.FILAMENT_POS_UNLOADED)

    async def test_failed_eject_keeps_loaded_state(self):
        self.printer.send_gcode.side_effect = RuntimeError('eject failed')
        await self.patcher._on_gcode_mmu_eject({'GATE': '0'}, None)
        self.assertEqual(self.patcher.ace.loaded_gate, 0)
        self.assertEqual(self.patcher.ace.filament.pos, self.module.FILAMENT_POS_LOADED)

    async def test_ejecting_other_gate_does_not_clear_threaded_gate(self):
        await self.patcher._on_gcode_mmu_eject({'GATE': '1'}, None)
        self.assertEqual(self.patcher.ace.loaded_gate, 0)
        self.assertEqual(self.patcher.ace.filament.pos, self.module.FILAMENT_POS_LOADED)


if __name__ == '__main__':
    unittest.main()
