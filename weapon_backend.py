"""Validated weapon records and level configuration, using native Win32 memory."""
import json
import math
import struct
from datetime import datetime

import inventory_gui_backend as inventory

require = inventory.require
attach = inventory.attach
KINDS = {
    'main': dict(label='主武器', container=0x718, level=0x28,
                 config_root=0xE01CA70, upgrades=0x330, name=0x28, record_class=0xE018AC8),
    'shaqi': dict(label='杀气武器', container=0x780, level=0x2C,
                  config_root=0xE01C9E0, upgrades=0x3B0, name=0x30, record_class=0xE018AB0),
}


class WeaponReader:
    def __init__(self, reader):
        self.reader, self.mem, self.base = reader, reader.mem, reader.base

    def snapshot(self, names=True):
        r, m, b = self.reader, self.mem, self.base
        state = r.inventory()  # Includes version, world and subsystem identity checks.
        require(m.read(b + 0x695A113, 3) == bytes.fromhex('8b 40 28'), '武器代码版本不匹配。')
        owner = int(state['owner'], 0)
        state['entries'] = []
        for kind, layout in KINDS.items():
            container = owner + layout['container']
            data, count, entries = r.sparse(container, 512)
            root = b + layout['config_root'] + 8
            cfgdata, cfgcount, configs = r.sparse(root, 4096)
            lookup = {}
            for _, key, row in configs:
                require(key not in lookup, '武器配置标识不唯一。')
                lookup[key] = row
            for index, key, obj in entries:
                expected_class = m.u64(b + layout['record_class'])
                require(obj and expected_class and m.u64(obj + 0x10) == expected_class
                        and m.u64(obj) == b + 0xB18C480,
                        f"武器实例类型不匹配（{layout['label']}，槽位 {index}）。")
                row = lookup.get(key)
                require(row and m.u64(row + 8) == key, '武器配置身份不匹配。')
                require(r.fstring(row + layout['name'], 256) == 'Default', '武器名称命名空间不匹配。')
                keyname = r.fstring(row + layout['name'] + 0x10, 1024)
                upgrades, n, cap = struct.unpack('<Qii', m.read(row + layout['upgrades'], 16))
                require(0 <= n <= 1000 and n <= cap <= 10000 and (upgrades or not n),
                        '武器升级配置无效。')
                level = m.i32(obj + layout['level'])
                require(1 <= level <= n + 1, '武器等级超出配置范围。')
                multiplier_address = upgrades + (level - 2) * 0x118 + 0x50 if level >= 2 else None
                scalar = struct.unpack('<f', m.read(multiplier_address, 4))[0] if multiplier_address else None
                require(scalar is None or math.isfinite(scalar) and 0 <= scalar <= 10000,
                        '武器倍率配置无效。')
                state['entries'].append(dict(kind=kind, kind_label=layout['label'],
                    container_index=index, key=hex(key), object=hex(obj),
                    object_class=hex(m.u64(obj + 0x10)), config=hex(row),
                    object_outer=hex(m.u64(obj + 0x20)),
                    config_vtable=hex(m.u64(row)), upgrades=hex(upgrades), upgrade_count=n,
                    name_key=keyname, level=level, max_config_level=n+1,
                    level_address=hex(obj+layout['level']), multiplier=scalar,
                    multiplier_address=hex(multiplier_address) if multiplier_address else None))
            r.unchanged(container, data, count)
            r.unchanged(root, cfgdata, cfgcount)
        require(m.u64(b + 0xDF71B18) == int(state['world'], 0), '场景已变化，请刷新。')
        if names:
            try:
                r.resolve_names(state)
            except Exception as exc:
                state['name_error'] = str(exc)
        m.alive()
        return state

    def change(self, snapshot, entry, field, value):
        require(field in ('level', 'multiplier'), '不支持的武器字段。')
        if field == 'level':
            require(type(value) is int and 1 <= value <= entry['max_config_level'],
                    '等级必须是配置范围内的整数。')
        else:
            require(type(value) in (int, float) and math.isfinite(value) and 0 < value <= 100,
                    '倍率必须大于 0 且不超过 100。')
            require(entry.get('multiplier_address') is not None, '1 级武器没有此升级倍率字段。')
        require(self.mem.pid == snapshot['pid'], '游戏进程已改变。')
        fresh = self.snapshot(names=False)
        require(all(fresh[k] == snapshot[k] for k in ('pid', 'module_base', 'owner', 'world')),
                '场景或背包已改变，请刷新。')
        matches = [e for e in fresh['entries'] if e['kind'] == entry['kind'] and e['key'] == entry['key']]
        require(len(matches) == 1, '武器已不存在或标识不唯一。')
        current = matches[0]
        identity = ('object', 'object_class', 'object_outer', 'config', 'config_vtable', 'upgrades', 'upgrade_count',
                    'level', 'level_address', 'multiplier_address', 'multiplier')
        require(all(current[k] == entry[k] for k in identity), '武器等级、配置或对象已改变，请刷新。')
        address = int(current[field + '_address'], 0)
        before = self.mem.read(address, 4)
        expected = struct.pack('<i' if field == 'level' else '<f', current[field])
        require(before == expected, '写入前字段已变化。')
        # Recheck live membership immediately before the single four-byte write.
        layout = KINDS[entry['kind']]
        owner, obj = int(fresh['owner'], 0), int(current['object'], 0)
        data, count, entries = self.reader.sparse(owner + layout['container'], 512)
        require(sum(k == int(entry['key'], 0) and o == obj for _, k, o in entries) == 1,
                '写入前武器容器已变化。')
        self.reader.unchanged(owner + layout['container'], data, count)
        require(self.mem.u64(self.base + 0xDF71B18) == int(fresh['world'], 0)
                and self.mem.u64(obj + 0x20) == int(current['object_outer'], 0)
                and self.mem.u64(obj + 0x10) == int(current['object_class'], 0)
                and self.mem.u64(self.base + layout['record_class']) == int(current['object_class'], 0)
                and self.mem.u64(obj) == self.base + 0xB18C480
                and self.mem.read(address, 4) == before,
                '写入前场景或武器已变化。')
        packed = struct.pack('<i' if field == 'level' else '<f', value)
        if field == 'level':
            self.mem.write_i32(address, value)
        else:
            self.mem.write_f32(address, value)
        require(self.mem.read(address, 4) == packed, '读回不一致，请刷新检查，不要自动重试。')
        actual = struct.unpack('<i' if field == 'level' else '<f', packed)[0]
        return dict(pid=self.mem.pid, kind=entry['kind'], key=entry['key'],
                    object=entry['object'], config=entry['config'], field=field,
                    address=hex(address), before=current[field], after=actual,
                    name=entry.get('name_zh') or entry['name_key'],
                    time=datetime.now().isoformat(timespec='seconds'))


def read_weapons():
    with inventory._lock:
        return WeaponReader(inventory.reader()).snapshot()


def change_weapon(snapshot, entry, field, value):
    with inventory._lock:
        result = WeaponReader(inventory.reader()).change(snapshot, entry, field, value)
        try:
            with (inventory.ROOT / 'weapon-changes.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps(result, ensure_ascii=False) + '\n')
        except OSError as exc:
            result['log_error'] = str(exc)
        return result
