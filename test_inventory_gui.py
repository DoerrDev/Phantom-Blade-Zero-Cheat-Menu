"""Smoke tests: all quantity writes use isolated mock memory, never the game."""
import copy
import json
import time
import unittest

import inventory_gui_backend as backend
from inventory_gui import InventoryApp


class InventoryTests(unittest.TestCase):
    def test_native_memory_adapter(self):
        import subprocess
        import sys
        from windows_memory import ProcessMemory
        code = "import ctypes as c,json,os,sys; a=(c.c_int32*3)(123,981,777); print(json.dumps([os.getpid(),c.addressof(a)]),flush=True); sys.stdin.readline(); print(list(a),flush=True)"
        target = subprocess.Popen([sys.executable, '-u', '-c', code], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True)
        mem = None
        try:
            pid, address = json.loads(target.stdout.readline())
            mem = ProcessMemory(pid, writable=True)
            self.assertEqual(mem.i32(address + 4), 981)
            mem.write_i32(address + 4, 1000)
            self.assertEqual(mem.i32(address + 4), 1000)
            self.assertEqual(mem.i32(address), 123)
            self.assertEqual(mem.i32(address + 8), 777)
            self.assertTrue(mem.path().lower().endswith('python.exe'))
            self.assertGreater(mem.module('python.exe')['base'], 0)
            with self.assertRaises(OSError):
                mem.read(1, 4)
            output, _ = target.communicate('done\n', timeout=5)
            self.assertEqual(json.loads(output), [123, 1000, 777])
            with self.assertRaises(RuntimeError):
                mem.alive()
        finally:
            if mem:
                mem.close()
            if target.poll() is None:
                target.kill()
            target.communicate()

    def test_native_inventory_guards(self):
        import struct
        class Memory:
            pid = 123
            def __init__(self):
                self.bytes, self.writes = {}, []
            def alive(self):
                pass
            def put(self, a, data):
                self.bytes.update({a+i: v for i, v in enumerate(data)})
            def read(self, a, n):
                return bytes(self.bytes[a+i] for i in range(n))
            def q(self, a, v):
                self.put(a, struct.pack('<Q', v))
            def u64(self, a):
                return struct.unpack('<Q', self.read(a, 8))[0]
            def i32(self, a):
                return struct.unpack('<i', self.read(a, 4))[0]
            def write_i32(self, a, v):
                self.writes.append((a, v))
                self.put(a, struct.pack('<i', v))
            def sparse(self, a, data, key, obj):
                raw = bytearray(0x30)
                struct.pack_into('<Q', raw, 0, data)
                for offset in (8, 0x10, 0x28):
                    struct.pack_into('<i', raw, offset, 1)
                self.put(a, raw)
                self.put(data, struct.pack('<QQQ', key, obj, 0))
        for case in ('valid', 'pid', 'world', 'owner', 'object', 'key', 'quantity', 'signature',
                     'write_failure', 'readback_failure'):
            with self.subTest(case=case):
                m = Memory()
                b, w, o, obj, cls, key = 0x140000000, 0x200000, 0x300000, 0x400000, 0x500000, 0x123456789
                m.put(b+0x6304DE0, bytes.fromhex('48 8B 05 31 CD C6 07 C3'))
                m.q(b+0xDF71B18, w); m.q(b+0xE019F88, cls)
                m.sparse(w+0x9E0, 0x600000, cls, o)
                for offset, value in ((0,b+0xC0CF830),(0x10,cls),(0x20,w),(0x28,w+0x9D8)):
                    m.q(o+offset, value)
                m.sparse(o+0x510, 0x700000, key, obj)
                m.q(obj+0x388, key)
                m.put(obj+0x380, struct.pack('<ii', 981, 777))
                reader = backend.InventoryReader(m, b)
                snapshot = reader.inventory()
                entry = dict(snapshot['entries'][0])
                if case in ('pid', 'world', 'owner'):
                    snapshot[case] = 124 if case == 'pid' else '0x111111'
                elif case == 'object':
                    entry['object'] = '0x111111'
                elif case == 'key':
                    entry['key'] = '0x111111'
                elif case == 'quantity':
                    entry['quantity'] = 980
                elif case == 'signature':
                    m.put(b+0x6304DE0, bytes(8))
                elif case == 'write_failure':
                    def fail_write(a, v):
                        raise OSError('simulated write failure')
                    m.write_i32 = fail_write
                elif case == 'readback_failure':
                    m.write_i32 = lambda a, v: m.writes.append((a, v))
                if case == 'valid':
                    result = reader.change(snapshot, entry, 1000)
                    self.assertEqual((result['before'], result['after']), (981, 1000))
                    self.assertEqual(m.writes, [(obj+0x380, 1000)])
                else:
                    with self.assertRaises((RuntimeError, OSError)):
                        reader.change(snapshot, entry, 1000)
                    self.assertEqual(m.writes, [(obj+0x380, 1000)] if case == 'readback_failure' else [])
                self.assertEqual(m.i32(obj+0x384), 777)
                for value in (-1, 2147483648, True, 1.2):
                    with self.assertRaises(RuntimeError):
                        reader.change(snapshot, entry, value)

    def test_gui_with_fake_backend(self):
        data = json.loads((backend.ROOT / 'inventory-names-27612.json').read_text(encoding='utf-8'))
        class Fake:
            calls = []
            def attach(self):
                return {}
            def read_inventory(self):
                return copy.deepcopy(data)
            def change_quantity(self, snapshot, entry, value):
                self.calls.append((entry['quantity'], value))
                return dict(before=entry['quantity'], after=value)
        fake = Fake()
        app = InventoryApp(service=fake, auto_connect=False)
        app.withdraw()
        def settle():
            deadline = time.monotonic() + 5
            while app.busy and time.monotonic() < deadline:
                app.update()
                time.sleep(.01)
            app.update()
            self.assertFalse(app.busy)
        try:
            app.connect(); settle()
            self.assertEqual(len(app.tree.get_children()), len(data['entries']))
            app.search.set('溶剂'); app.update()
            self.assertEqual(len(app.tree.get_children()), 1)
            app.tree.selection_set(app.tree.get_children()[0]); app.select()
            old = app.selected['quantity']
            app.amount.set(str(old + 10)); app.apply(); settle()
            self.assertEqual(app.selected['quantity'], old + 10)
            app.undo(); settle()
            self.assertEqual(app.selected['quantity'], old)
            self.assertEqual(fake.calls, [(old, old + 10), (old + 10, old)])
            app.amount.set('-1'); app.apply()
            self.assertEqual(len(fake.calls), 2)
        finally:
            app.destroy()


if __name__ == '__main__':
    unittest.main(verbosity=2)
