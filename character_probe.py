"""Read player attributes and perform guarded Win32 float writes; no injection."""
import argparse
import json
import math
import struct
from datetime import datetime, timezone
from pathlib import Path

import inventory_gui_backend as b
from windows_memory import ProcessMemory


def snapshot():
    game = b.discover_game()
    mem = ProcessMemory(game['ProcessId'], writable=False)
    try:
        return read_snapshot(mem)
    finally:
        mem.close()


def read_snapshot(mem):
    module = mem.module('PBZ.exe')
    path = mem.path().replace('/', '\\').casefold()
    b.require(path == module['path'].replace('/', '\\').casefold() and path.endswith(b.GAME_SUFFIX),
              'Process/module identity mismatch')
    base = module['base']
    reader = b.InventoryReader(mem, base)
    reader.inventory()  # Existing build signature and world/subsystem validation.
    world = mem.u64(base + 0xDF71B18)
    instance = mem.u64(world + 0x228)
    players = mem.u64(instance + 0x38)
    b.require(mem.i32(instance + 0x40) == 1, 'Expected one local player')
    player = mem.u64(players)
    controller = mem.u64(player + 0x30)
    pawn = mem.u64(controller + 0x2F0)
    b.require(mem.u64(pawn) == base + 0xBFBA000, 'Unsupported player pawn vtable')
    asc = mem.u64(pawn + 0x750)
    attributes = mem.u64(pawn + 0x758)
    b.require(mem.u64(asc) == base + 0xBF91A20, 'Unexpected ability component')
    b.require(mem.u64(attributes) == base + 0xBC80180, 'Unexpected attribute object')
    b.require(mem.u64(asc + 0x20) == pawn and mem.u64(attributes + 0x20) == pawn,
              'Attribute/component owner does not match local player pawn')
    b.require(mem.u64(attributes + 0x10) == mem.u64(base + 0xDFF2D60),
              'BasicAttributeSet class mismatch')
    data = mem.read(attributes, 0xE38)
    metadata = json.loads(Path(__file__).with_name(
        'character-basic-attribute-metadata.json').read_text(encoding='utf-8'))
    values = []
    for field in metadata:
        offset = int(field['packed_offset'], 16) >> 16
        if field['kind'] != '0x19' or not 0x48 <= offset <= 0xE28:
            continue
        basic, current = struct.unpack_from('<ff', data, offset + 8)
        b.require(math.isfinite(basic) and math.isfinite(current), 'Invalid attribute value')
        values.append(dict(name=field['name'], offset=hex(offset), base=basic,
                           current=current, current_address=hex(attributes + offset + 12)))
    b.require(mem.u64(base + 0xDF71B18) == world
              and mem.u64(controller + 0x2F0) == pawn
              and mem.u64(pawn + 0x758) == attributes,
              'Player changed while sampling; retry')
    mem.alive()
    return dict(timestamp=datetime.now(timezone.utc).isoformat(), pid=mem.pid,
                module=module, world=hex(world), game_instance=hex(instance),
                local_player=hex(player), controller=hex(controller), pawn=hex(pawn),
                asc=hex(asc), attributes=hex(attributes), values=values)


def change_attribute(previous, entry, field, value):
    """Re-resolve and validate the local player before one four-byte write."""
    b.require(field in ('base', 'current'), '不支持的属性字段。')
    b.require(type(value) in (int, float) and math.isfinite(value), '请输入有限数字。')
    try:
        packed = struct.pack('<f', value)
        actual = struct.unpack('<f', packed)[0]
    except (OverflowError, struct.error):
        raise RuntimeError('数值超出 float32 范围。') from None
    b.require(math.isfinite(actual), '数值超出 float32 范围。')
    game = b.discover_game()
    b.require(game['ProcessId'] == previous['pid'], '游戏进程已改变，请刷新。')
    mem = ProcessMemory(previous['pid'], writable=True)
    try:
        return write_attribute(mem, previous, entry, field, actual)
    finally:
        mem.close()


def write_attribute(mem, previous, entry, field, value):
    b.require(field in ('base', 'current'), '不支持的属性字段。')
    b.require(type(value) in (int, float) and math.isfinite(value), '请输入有限数字。')
    packed = struct.pack('<f', value)
    b.require(math.isfinite(struct.unpack('<f', packed)[0]), '数值超出 float32 范围。')
    b.require(mem.pid == previous['pid'], '游戏进程已改变，请刷新。')
    fresh = read_snapshot(mem)
    identity = ('pid', 'module', 'world', 'game_instance', 'local_player', 'controller',
                'pawn', 'asc', 'attributes')
    b.require(all(fresh[k] == previous[k] for k in identity), '玩家或场景已改变，请刷新。')
    matches = [row for row in fresh['values'] if row['name'] == entry['name']]
    b.require(len(matches) == 1, '属性不存在或标识不唯一。')
    row = matches[0]
    b.require(row['offset'] == entry['offset'] and row['current_address'] == entry['current_address'],
              '属性布局已变化，请刷新。')
    before = struct.pack('<f', entry[field])
    b.require(struct.pack('<f', row[field]) == before, '属性值已被游戏改变，请刷新后重试。')
    obj = int(fresh['attributes'], 0)
    address = obj + int(row['offset'], 0) + (8 if field == 'base' else 12)
    base = fresh['module']['base']
    b.require(mem.u64(base + 0xDF71B18) == int(fresh['world'], 0)
              and mem.u64(int(fresh['controller'], 0) + 0x2F0) == int(fresh['pawn'], 0)
              and mem.u64(int(fresh['pawn'], 0) + 0x758) == obj
              and mem.u64(obj + 0x20) == int(fresh['pawn'], 0)
              and mem.read(address, 4) == before, '写入前玩家或属性已变化，请刷新。')
    mem.write_f32(address, value)
    b.require(mem.read(address, 4) == packed, '读回不一致，请刷新检查，不要自动重试。')
    return dict(name=row['name'], field=field, before=row[field],
                after=struct.unpack('<f', packed)[0], address=hex(address))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='character-runtime.json')
    args = parser.parse_args()
    result = snapshot()
    Path(args.output).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('PID', result['pid'], 'pawn', result['pawn'], 'attributes', result['attributes'])
    selected = {'Health', 'Health_Max', 'Attack', 'Shachi', 'Shachi_Max', 'WalkSpeed',
                'JogSpeed', 'SprintSpeed', 'UltimateCharge', 'SpiritShadow'}
    for row in result['values']:
        if row['name'] in selected:
            print(f"{row['name']}: base={row['base']:.6g} current={row['current']:.6g} "
                  f"address={row['current_address']}")
