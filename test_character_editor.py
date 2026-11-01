"""Character writes are tested against isolated fake memory, never the game."""
import copy
import struct
import time
import unittest
from unittest.mock import patch

import character_probe as backend


def fixture():
    row = dict(name='Health', offset='0x58', current_address='0x3064', base=100., current=80.)
    state = dict(pid=123, module=dict(base=0x140000000), world='0x1000',
                 game_instance='0x1100', local_player='0x1200', controller='0x2000',
                 pawn='0x2800', asc='0x2900', attributes='0x3000', values=[row])
    class Memory:
        pid = 123
        def __init__(self):
            self.data = bytearray(struct.pack('<fff', 100., 80., 777.))
            self.writes = []
        def u64(self, a):
            return {0x140000000+0xDF71B18:0x1000, 0x22F0:0x2800,
                    0x2F58:0x3000, 0x3020:0x2800}[a]
        def read(self, a, n):
            return bytes(self.data[a-0x3060:a-0x3060+n])
        def write_f32(self, a, value):
            self.writes.append((a, value))
            self.data[a-0x3060:a-0x3060+4] = struct.pack('<f', value)
    return state, row, Memory()


class CharacterTests(unittest.TestCase):
    def test_guarded_write_and_neighbors(self):
        for field, address in [('current', 0x3064), ('base', 0x3060)]:
            state, row, mem = fixture()
            before = bytes(mem.data)
            with patch.object(backend, 'read_snapshot', return_value=copy.deepcopy(state)):
                result = backend.write_attribute(mem, state, row, field, 90.)
            self.assertEqual(result['after'], 90.)
            self.assertEqual(mem.writes, [(address, 90.)])
            offset = address-0x3060
            self.assertEqual(mem.data[:offset], before[:offset])
            self.assertEqual(mem.data[offset+4:], before[offset+4:])

    def test_stale_and_invalid_rejected(self):
        for case in ('pid', 'world', 'pawn', 'attributes', 'value', 'address', 'nan', 'field', 'race'):
            with self.subTest(case=case):
                state, row, mem = fixture()
                fresh = copy.deepcopy(state)
                field, value = 'current', 90.
                if case in ('pid', 'world', 'pawn', 'attributes'):
                    fresh[case] = 999 if case == 'pid' else '0x999'
                elif case == 'value':
                    fresh['values'][0]['current'] = 70.
                elif case == 'address':
                    row = dict(row, current_address='0x999')
                elif case == 'nan':
                    value = float('nan')
                elif case == 'field':
                    field = 'unknown'
                elif case == 'race':
                    mem.data[4:8] = struct.pack('<f', 70.)
                with patch.object(backend, 'read_snapshot', return_value=fresh):
                    with self.assertRaises(RuntimeError):
                        backend.write_attribute(mem, state, row, field, value)
                self.assertEqual(mem.writes, [])

    def test_gui_modify_undo_and_failure(self):
        from inventory_gui import InventoryApp
        state, _, _ = fixture()
        class Fake:
            fail = False
            def snapshot(self):
                return copy.deepcopy(state)
            def change_attribute(self, snapshot, row, field, value):
                if self.fail:
                    raise RuntimeError('stale value')
                before = state['values'][0][field]
                state['values'][0][field] = value
                return dict(before=before, after=value)
        fake = Fake()
        app = InventoryApp(auto_connect=False, character_service=fake)
        app.withdraw()
        panel = app.character_panel
        def settle():
            deadline = time.monotonic()+5
            while panel.busy and time.monotonic()<deadline:
                app.update()
                time.sleep(.01)
            app.update()
            self.assertFalse(panel.busy)
        try:
            panel.refresh(); settle()
            panel.tree.selection_set('Health'); panel.select()
            panel.amount.set('90'); panel.apply(); settle()
            self.assertEqual(panel.selected['current'], 90.)
            panel.undo(); settle()
            self.assertEqual(panel.selected['current'], 80.)
            panel.field.set('基础值'); panel.select()
            panel.amount.set('120'); panel.apply(); settle()
            self.assertEqual(panel.selected['base'], 120.)
            fake.fail = True
            panel.undo(); settle()
            self.assertIsNone(panel.snapshot)
            self.assertIsNone(panel.undo_state)
            self.assertEqual(str(panel.apply_button['state']), 'disabled')
        finally:
            app.destroy()


if __name__ == '__main__':
    unittest.main()
