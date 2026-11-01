"""Weapon editor writes are tested only in fixtures and an isolated child process."""
import copy
import json
import struct
import subprocess
import sys
import time
import unittest

from inventory_gui_backend import InventoryReader
from weapon_backend import WeaponReader


class Memory:
    pid = 123

    def __init__(self):
        self.data = {}
        self.writes = []

    def put(self, address, data):
        self.data.update({address+i: value for i, value in enumerate(data)})

    def read(self, address, count):
        return bytes(self.data[address+i] for i in range(count))

    def q(self, address, value):
        self.put(address, struct.pack('<Q', value))

    def u64(self, address):
        return struct.unpack('<Q', self.read(address, 8))[0]

    def i32(self, address):
        return struct.unpack('<i', self.read(address, 4))[0]

    def alive(self):
        pass

    def write_i32(self, address, value):
        self.writes.append(address)
        self.put(address, struct.pack('<i', value))

    def write_f32(self, address, value):
        self.writes.append(address)
        self.put(address, struct.pack('<f', value))


def fixture():
    m = Memory()
    base, owner, obj, row, upgrades = 0x140000000, 0x200000, 0x300000, 0x400000, 0x500000
    m.put(base+0x695A113, bytes.fromhex('8b 40 28'))
    m.q(base+0xDF71B18, 0x600000)
    m.q(obj, base+0xB18C480)
    m.q(obj+0x20, owner)
    m.q(obj+0x10, 0x900000)
    m.q(base+0xE018AC8, 0x900000)
    m.put(obj+0x28, struct.pack('<ii', 2, 777))
    m.q(row, base+0xC0FB778)
    m.q(row+8, 0x123456789)
    m.put(row+0x330, struct.pack('<Qii', upgrades, 3, 4))
    m.put(upgrades+0x4C, struct.pack('<IfI', 0xDEADBEEF, 1.1, 0xCAFEBABE))
    m.put(upgrades+0x168, struct.pack('<f', 1.2))
    m.put(upgrades+0x280, struct.pack('<f', 1.3))
    for offset, text in [(0x28, 'Default'), (0x38, 'Weapon_Test')]:
        pointer = row+0x1000+offset*0x10
        data = (text+'\0').encode('utf-16le')
        m.put(pointer, data)
        m.put(row+offset, struct.pack('<Qii', pointer, len(text)+1, len(text)+1))
    containers = {
        owner+0x718: [(0, 0x123456789, obj)], owner+0x780: [],
        base+0xE01CA78: [(0, 0x123456789, row)], base+0xE01C9E8: [],
    }
    state = dict(pid=m.pid, module_base=hex(base), owner=hex(owner), world='0x600000', entries=[])
    class Reader(InventoryReader):
        def inventory(self):
            return copy.deepcopy(state)

        def sparse(self, address, maximum):
            return 0, len(containers[address]), containers[address]

        def unchanged(self, address, data, count):
            if count != len(containers[address]):
                raise RuntimeError('container changed')

        def resolve_names(self, state):
            for e in state['entries']:
                e['name_zh'] = '测试武器'
    return WeaponReader(Reader(m, base)), m, state, containers


class WeaponTests(unittest.TestCase):
    def test_record_outer_need_not_be_backpack(self):
        reader, memory, _, _ = fixture()
        memory.q(0x300020, 0x990000)
        state = reader.snapshot()
        self.assertEqual(state['entries'][0]['object_outer'], '0x990000')
        reader.change(state, state['entries'][0], 'level', 3)
        self.assertEqual(memory.writes, [0x300028])

    def test_wrong_record_class_and_changed_outer_rejected(self):
        for case in ('class', 'outer'):
            reader, memory, _, _ = fixture()
            state = reader.snapshot()
            memory.q(0x300010 if case == 'class' else 0x300020, 0x990000)
            with self.assertRaises(RuntimeError):
                reader.change(state, state['entries'][0], 'level', 3)
            self.assertEqual(memory.writes, [])

    def test_guarded_writes_and_unchanged_neighbors(self):
        for field, value in [('level', 3), ('multiplier', 2.5)]:
            reader, memory, _, _ = fixture()
            state = reader.snapshot()
            entry = state['entries'][0]
            address = int(entry[field+'_address'], 0)
            result = reader.change(state, entry, field, value)
            self.assertEqual(result['after'], value)
            self.assertEqual(memory.writes, [address])
            self.assertEqual(memory.i32(0x30002C), 777)
            self.assertEqual(memory.read(0x50004C, 4), bytes.fromhex('efbeadde'))
            self.assertEqual(memory.read(0x500054, 4), bytes.fromhex('bebafeca'))

    def test_stale_identity_and_invalid_inputs_never_write(self):
        for case in ('pid', 'world', 'owner', 'object', 'config', 'key', 'quantity_change',
                     'multiplier_change', 'membership', 'signature'):
            with self.subTest(case=case):
                reader, memory, _, containers = fixture()
                state = reader.snapshot()
                entry = dict(state['entries'][0])
                if case in ('pid', 'world', 'owner'):
                    state[case] = 0 if case == 'pid' else '0x111111'
                elif case in ('object', 'config', 'key'):
                    entry[case] = '0x111111'
                elif case == 'quantity_change':
                    memory.put(0x300028, struct.pack('<i', 3))
                    memory.put(0x500168, struct.pack('<f', 1.2))
                elif case == 'multiplier_change':
                    memory.put(0x500050, struct.pack('<f', 1.3))
                elif case == 'membership':
                    containers[0x200718] = []
                elif case == 'signature':
                    memory.put(reader.base+0x695A113, bytes(3))
                with self.assertRaises((RuntimeError, KeyError)):
                    reader.change(state, entry, 'multiplier', 2.5)
                self.assertFalse(memory.writes)
        reader, memory, _, _ = fixture()
        state = reader.snapshot()
        for field, value in [('level', 0), ('level', 5), ('level', True), ('level', 1.5),
                             ('multiplier', float('nan')), ('multiplier', float('inf')),
                             ('multiplier', 0), ('multiplier', 101), ('damage', 100)]:
            with self.assertRaises(RuntimeError):
                reader.change(state, state['entries'][0], field, value)
        self.assertFalse(memory.writes)

    def test_native_float_write_in_child(self):
        from windows_memory import ProcessMemory
        code = "import ctypes as c,json,os,sys; a=(c.c_float*3)(123,1.4,777); print(json.dumps([os.getpid(),c.addressof(a)]),flush=True); sys.stdin.readline(); print(list(a),flush=True)"
        child = subprocess.Popen([sys.executable, '-u', '-c', code], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, text=True)
        mem = None
        try:
            pid, address = json.loads(child.stdout.readline())
            mem = ProcessMemory(pid, writable=True)
            mem.write_f32(address+4, 2.5)
            self.assertEqual(struct.unpack('<3f', mem.read(address, 12)), (123, 2.5, 777))
            output, _ = child.communicate('\n', timeout=5)
            self.assertEqual(json.loads(output), [123, 2.5, 777])
        finally:
            if mem:
                mem.close()
            if child.poll() is None:
                child.kill()
            child.communicate()

    def test_gui_select_edit_and_undo(self):
        from inventory_gui import InventoryApp
        reader, _, _, _ = fixture()
        class Fake:
            attach = staticmethod(lambda: None)
            read_weapons = staticmethod(reader.snapshot)
            change_weapon = staticmethod(reader.change)
        class FakeInventory:
            attach = staticmethod(lambda: None)
            read_inventory = staticmethod(lambda: dict(pid=123, entries=[]))
        root = InventoryApp(service=FakeInventory, weapon_service=Fake, auto_connect=False)
        root.withdraw()
        window = root.weapon_panel
        def settle():
            deadline = time.monotonic()+5
            while window.busy and time.monotonic() < deadline:
                root.update()
                time.sleep(.01)
            root.update()
            self.assertFalse(window.busy)
        try:
            self.assertEqual([root.notebook.tab(t, 'text') for t in root.notebook.tabs()],
                             ['玩家背包', '武器属性', '人物属性'])
            root.open_weapons(); root.update(); settle()
            self.assertEqual(root.notebook.select(), str(window))
            self.assertEqual(window.winfo_toplevel(), root)
            window.tree.selection_set('0'); window.select()
            window.multiplier.set('2.5'); window.apply('multiplier'); settle()
            self.assertEqual(window.selected['multiplier'], 2.5)
            root.notebook.select(root.inventory_tab); root.update()
            root.open_weapons(); root.update()
            self.assertEqual(window.selected['multiplier'], 2.5)
            self.assertIsNotNone(window.undo_state)
            window.undo(); settle()
            self.assertAlmostEqual(window.selected['multiplier'], 1.1, places=5)
            window.level.set('3'); window.apply('level'); settle()
            self.assertEqual(window.selected['level'], 3)
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main(verbosity=2)
