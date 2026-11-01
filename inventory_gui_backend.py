"""Native Win32 inventory backend; no CE, MCP, debugger, or game calls."""
from __future__ import annotations

import json
import struct
import threading
from datetime import datetime
from pathlib import Path

from windows_memory import ProcessMemory, processes

ROOT = Path(__file__).resolve().parent
_memory = None
_base = None
_lock = threading.RLock()
GAME_SUFFIX = '\\platform\\steam\\pbz.exe'


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def discover_game():
    games = []
    for entry in processes():
        if entry.szExeFile.casefold() != 'pbz.exe':
            continue
        mem = ProcessMemory(entry.th32ProcessID)
        try:
            path = mem.path()
            if path.replace('/', '\\').casefold().endswith(GAME_SUFFIX):
                games.append(dict(ProcessId=mem.pid, ExecutablePath=path))
        finally:
            mem.close()
    require(len(games) == 1, '请启动游戏并进入存档，需要找到唯一的 PBZ 游戏进程。')
    return games[0]


def attach():
    global _memory, _base
    with _lock:
        game = discover_game()
        mem = ProcessMemory(game['ProcessId'], writable=True)
        try:
            module = mem.module('PBZ.exe')
            require(mem.path().casefold() == game['ExecutablePath'].casefold() == module['path'].casefold(),
                    '进程身份不匹配，请重新连接。')
        except Exception:
            mem.close()
            raise
        if _memory:
            _memory.close()
        _memory, _base = mem, module['base']
        return game


class InventoryReader:
    def __init__(self, memory, base):
        self.mem, self.base = memory, base

    def sparse(self, address, maximum):
        raw = self.mem.read(address, 0x30)
        data = struct.unpack_from('<Q', raw)[0]
        count = struct.unpack_from('<i', raw, 8)[0]
        bits = struct.unpack_from('<i', raw, 0x28)[0]
        require(0 <= count <= maximum and bits == count, '容器结构不匹配，请刷新。')
        require(data != 0 or count == 0, '容器数据为空。')
        bitmap = struct.unpack_from('<Q', raw, 0x20)[0] or address + 0x10
        flags = self.mem.read(bitmap, ((count + 31) // 32) * 4) if count else b''
        slots = self.mem.read(data, count * 24) if count else b''
        entries = []
        for i in range(count):
            if flags[i // 8] & (1 << (i % 8)):
                key, obj = struct.unpack_from('<QQ', slots, i * 24)
                entries.append((i, key, obj))
        return data, count, entries

    def unchanged(self, address, data, count):
        require(self.mem.u64(address) == data and self.mem.i32(address + 8) == count,
                '容器已变化，请刷新。')

    def inventory(self):
        m, b = self.mem, self.base
        m.alive()
        require(m.read(b + 0x6304DE0, 8) == bytes.fromhex('48 8B 05 31 CD C6 07 C3'),
                '当前游戏版本不受支持。')
        world, cls = m.u64(b + 0xDF71B18), m.u64(b + 0xE019F88)
        require(world != 0 and cls != 0, '请进入游戏存档。')
        sm = world + 0x9E0
        data, count, slots = self.sparse(sm, 2048)
        owners = [(i, obj) for i, key, obj in slots if key == cls and obj]
        require(len(owners) == 1, '背包子系统不存在或不唯一。')
        index, owner = owners[0]
        require(m.u64(owner) == b + 0xC0CF830 and m.u64(owner + 0x10) == cls
                and m.u64(owner + 0x20) == world and m.u64(owner + 0x28) == world + 0x9D8,
                '背包子系统身份不匹配。')
        self.unchanged(sm, data, count)
        container = owner + 0x510
        itemdata, itemcount, items = self.sparse(container, 4096)
        entries = []
        for i, key, obj in items:
            require(obj != 0 and m.u64(obj + 0x388) == key, '物品身份已变化，请刷新。')
            entries.append(dict(container_index=i, key=hex(key), object=hex(obj),
                                quantity_address=hex(obj + 0x380), quantity=m.i32(obj + 0x380)))
        self.unchanged(container, itemdata, itemcount)
        require(m.u64(b + 0xDF71B18) == world, '场景已变化，请刷新。')
        m.alive()
        return dict(pid=m.pid, module_base=hex(b), world=hex(world), owner=hex(owner),
                    root='PBZ.exe+0xDF71B18', subsystem_class='PBZBackPackSubsystem',
                    subsystem_map=hex(sm), subsystem_index=index, inventory_container=hex(container),
                    entries=entries)

    def fstring(self, address, maximum):
        pointer, length, capacity = struct.unpack('<Qii', self.mem.read(address, 16))
        require(0 <= length <= maximum and capacity >= length, 'FString 结构不匹配。')
        if length == 0:
            return ''
        require(pointer != 0, 'FString 指针为空。')
        return self.mem.read(pointer, length * 2).decode('utf-16-le').rstrip('\0')

    def names(self, snapshot):
        m = self.mem
        for e in snapshot['entries']:
            obj = int(e['object'], 0)
            require(m.u64(obj + 0x388) == int(e['key'], 0), '物品身份已变化。')
            e['name_namespace'] = self.fstring(obj + 0x50, 256)
            e['name_key'] = self.fstring(obj + 0x60, 1024)
            e['detail_key'] = self.fstring(obj + 0x90, 1024)
            e['description_key'] = self.fstring(obj + 0xC0, 1024)
            require(e['name_namespace'] == 'Default', '不支持的名称命名空间。')
        self.resolve_names(snapshot)

    def resolve_names(self, snapshot):
        m, b, world = self.mem, self.base, int(snapshot['world'], 0)
        sm = world + 0x9E0
        data, count, slots = self.sparse(sm, 2048)
        flows = [obj for _, _, obj in slots if obj and m.u64(obj) == b + 0xC0AEFA0]
        require(len(flows) == 1 and m.u64(flows[0] + 0x20) == world, '本地化子系统不可用。')
        dt = m.u64(flows[0] + 0x428)
        require(dt != 0 and m.u64(dt) == b + 0xB1684A8, '本地化表不可用。')
        wanted = {e['name_key'] for e in snapshot['entries']}
        rd, rn, rows = self.sparse(dt + 0x30, 100000)
        matches = {}
        for _, _, row in rows:
            require(row != 0 and m.u64(row) == b + 0xBF5AA78, '本地化行类型不匹配。')
            key = self.fstring(row + 8, 2048)
            if key in wanted:
                require(key not in matches, '本地化键不唯一。')
                matches[key] = (self.fstring(row + 0x38, 16384), hex(row))
        for e in snapshot['entries']:
            if e['name_key'] in matches:
                e['name_zh'], e['localization_row'] = matches[e['name_key']]
        self.unchanged(dt + 0x30, rd, rn)
        self.unchanged(sm, data, count)
        require(m.u64(b + 0xDF71B18) == world, '场景已变化，请刷新。')
        m.alive()
        snapshot['localization'] = dict(data_table=hex(dt), flow_subsystem=hex(flows[0]),
                                      row_slots=rn, matched_items=sum(bool(e.get('name_zh')) for e in snapshot['entries']))

    def change(self, snapshot, entry, new_quantity):
        require(not isinstance(new_quantity, bool) and isinstance(new_quantity, int)
                and 0 <= new_quantity <= 2147483647, '数量必须是 0 到 2147483647 的整数。')
        require(self.mem.pid == snapshot['pid'], '游戏进程已改变，请重新连接。')
        fresh = self.inventory()
        require(all(fresh[k] == snapshot[k] for k in ('pid', 'world', 'owner', 'module_base')),
                '场景或背包已改变，请刷新。')
        matches = [e for e in fresh['entries'] if int(e['key'], 0) == int(entry['key'], 0)]
        require(len(matches) == 1, '物品不存在或标识不唯一，请刷新。')
        selected = matches[0]
        require(all(selected[k] == entry[k] for k in ('object', 'quantity_address', 'quantity')),
                '物品对象或数量已改变，请刷新。')
        obj, address, key = (int(entry[k], 0) for k in ('object', 'quantity_address', 'key'))
        require(address == obj + 0x380 and self.mem.u64(obj + 0x388) == key,
                '写入前物品校验失败。')
        require(self.mem.u64(self.base + 0xDF71B18) == int(snapshot['world'], 0), '场景已变化。')
        require(self.mem.i32(address) == entry['quantity'], '数量已变化，请刷新。')
        self.mem.write_i32(address, new_quantity)
        actual = self.mem.i32(address)
        require(actual == new_quantity, '写入后读回不一致，请刷新检查；不要自动重试。')
        return dict(pid=self.mem.pid, object=entry['object'], key=entry['key'],
                    address=entry['quantity_address'], before=entry['quantity'], after=actual)


def reader():
    require(_memory is not None, '请先连接游戏。')
    _memory.alive()
    return InventoryReader(_memory, _base)


def read_inventory():
    with _lock:
        r = reader()
        snapshot = r.inventory()
        try:
            r.names(snapshot)
        except Exception as exc:
            snapshot['name_error'] = str(exc)
        return snapshot


def change_quantity(snapshot, entry, new_quantity):
    with _lock:
        result = reader().change(snapshot, entry, new_quantity)
        result['name'] = entry.get('name_zh') or entry.get('name_key') or entry['key']
        result['time'] = datetime.now().isoformat(timespec='seconds')
        try:
            with (ROOT / 'inventory-changes.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps(result, ensure_ascii=False) + '\n')
        except OSError as exc:
            result['log_error'] = str(exc)
        return result
