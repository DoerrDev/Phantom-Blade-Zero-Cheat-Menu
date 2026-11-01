"""影之刃 · 玄锋控制台：背包 / 武器 / 人物属性。

读写与读回验证全部沿用 inventory_gui_backend、weapon_backend、character_probe；
本文件只负责界面。所有游戏读写在后台线程执行，界面不会卡住；任何失败都会作废
当前快照，必须重新读取后才能再次写入。视觉规范见 designmd.md。
"""
import math
import os
import queue
import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, font as tkfont

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = getattr(sys, 'frozen', False)
if not FROZEN:
    sys.path.insert(0, os.path.dirname(HERE))  # 源码运行：后端模块位于上一级目录
import inventory_gui_backend as inv  # noqa: E402
import weapon_backend as wp  # noqa: E402
import character_probe as cp  # noqa: E402
from character_gui import LABELS as ATTR_LABELS, COMMON as ATTR_COMMON  # noqa: E402

if FROZEN:
    # 打包后模块位于临时解包目录，修改日志改写到 exe 所在目录。
    from pathlib import Path
    inv.ROOT = Path(sys.executable).resolve().parent

try:
    from PIL import Image, ImageDraw, ImageTk, ImageEnhance
except ImportError:
    Image = None

# designmd.md · Onyx / Graphite / Obsidian 三层表面，Cobalt 是唯一的强调色。
CANVAS = '#171721'
SIDEBAR = '#121219'
CARD = '#1e1e2a'
RAISED = '#272735'
HOVER = '#303040'
STRIPE = '#21212e'
LINE = '#2e2e3c'
SLATE = '#70707d'
TXT = '#ededf3'
MUTED = '#c3c3cc'
DIM = '#8b8b99'
COBALT = '#5266eb'
COBALT_HI = '#6678f2'
COBALT_DIM = '#2b3266'
ERROR = '#f09595'
WHITE = '#ffffff'
FONT = 'Microsoft YaHei UI'
INT32_MAX = 2147483647
SPINNER = '◐◓◑◒'


def fmt(value):
    if value is None:
        return '—'
    if isinstance(value, float):
        return f'{value:.6g}'
    return str(value)


def rounded(canvas, x1, y1, x2, y2, r, **kw):
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


class Pill(tk.Canvas):
    """胶囊按钮：primary（Cobalt 填充）、ghost（描边）、soft（Obsidian 填充）、on（选中段）。"""

    def __init__(self, master, text, command, kind='ghost', bg=CARD, height=36, padx=20, size=10):
        self.font = tkfont.Font(family=FONT, size=size)
        self.text, self.command, self.kind = text, command, kind
        self.height, self.padx = height, padx
        super().__init__(master, height=height, bg=bg, highlightthickness=0, bd=0,
                         cursor='hand2', takefocus=1)
        self.enabled, self.hover, self.focused = True, False, False
        self.bind('<Enter>', lambda _: self._set(hover=True))
        self.bind('<Leave>', lambda _: self._set(hover=False))
        self.bind('<FocusIn>', lambda _: self._set(focused=True))
        self.bind('<FocusOut>', lambda _: self._set(focused=False))
        self.bind('<ButtonRelease-1>', self._click)
        self.bind('<Return>', lambda _: self.invoke())
        self.bind('<space>', lambda _: self.invoke())
        self.configure(text=text)

    def configure(self, text=None, kind=None, **kw):
        if text is not None:
            self.text = text
            kw['width'] = self.font.measure(text) + self.padx * 2
        if kind is not None:
            self.kind = kind
        if kw:
            super().configure(**kw)
        self.draw()

    def set_enabled(self, enabled):
        if enabled != self.enabled:
            self.enabled = enabled
            super().configure(cursor='hand2' if enabled else 'arrow')
            self.draw()

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self.draw()

    def _click(self, event):
        if 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            self.invoke()

    def invoke(self):
        if self.enabled and self.command:
            self.command()

    def draw(self):
        self.delete('all')
        w, h = int(self['width']), self.height
        hot = self.hover and self.enabled
        if not self.enabled:
            fill, outline, fg = (RAISED if self.kind != 'ghost' else self['bg']), LINE, DIM
        elif self.kind == 'primary':
            fill, outline, fg = (COBALT_HI if hot else COBALT), '', WHITE
        elif self.kind == 'on':
            fill, outline, fg = COBALT_DIM, COBALT, TXT
        elif self.kind == 'soft':
            fill, outline, fg = (HOVER if hot else RAISED), '', TXT
        else:
            fill, outline, fg = (RAISED if hot else self['bg']), (TXT if hot else SLATE), TXT
        if self.focused and self.enabled:
            rounded(self, 0, 0, w - 1, h - 1, h / 2, fill='', outline=COBALT_HI, width=2)
            rounded(self, 3, 3, w - 4, h - 4, h / 2 - 3, fill=fill, outline=outline or fill)
        else:
            rounded(self, 1, 1, w - 2, h - 2, h / 2 - 1, fill=fill, outline=outline or fill)
        self.create_text(w / 2, h / 2, text=self.text, fill=fg, font=self.font)


class Page(tk.Frame):
    title = sub = ''
    columns = ()        # (列 id, 标题, 宽度, 是否数值)
    fields = ()         # dict(key, label, kind=int|float, lo, hi)
    note = ''

    def __init__(self, app):
        super().__init__(app.main, bg=CANVAS)
        self.app = app
        self.snapshot = None
        self.selected = None
        self.undo_state = None
        self.wants_load = True
        self.field = self.fields[0]
        self.sort = (None, False)
        self.query = tk.StringVar()
        self.value = tk.StringVar()
        self.parsed = None
        self.build()
        self.query.trace_add('write', lambda *_: self.render())
        self.value.trace_add('write', lambda *_: self.validate())
        self.render()
        self.load_editor()
        self.set_status('尚未读取。启动游戏并进入存档后按 F5 读取。', 'info')

    # ---- 子类实现 ----
    def load(self):
        raise NotImplementedError

    def entries(self, snapshot):
        return snapshot['entries']

    def ident(self, e):
        raise NotImplementedError

    def row(self, e):
        raise NotImplementedError

    def name(self, e):
        raise NotImplementedError

    def detail(self, e):
        return ''

    def haystack(self, e):
        return ' '.join(str(v) for v in self.row(e))

    def keep(self, e):
        return True

    def available(self, e, field):
        return e.get(field['key']) is not None

    def write(self, snapshot, entry, field, value):
        raise NotImplementedError

    def build_filters(self, bar):
        pass

    # ---- 布局 ----
    def build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        head = tk.Frame(self, bg=CANVAS)
        head.grid(row=0, column=0, sticky='ew', pady=(0, 20))
        head.columnconfigure(0, weight=1)
        tk.Label(head, text=self.title, bg=CANVAS, fg=TXT, font=(FONT, 22)).grid(row=0, column=0, sticky='w')
        tk.Label(head, text=self.sub, bg=CANVAS, fg=MUTED, font=(FONT, 10)).grid(row=1, column=0, sticky='w', pady=(4, 0))
        self.refresh_btn = Pill(head, '刷新  F5', self.refresh, kind='ghost', bg=CANVAS)
        self.refresh_btn.grid(row=0, column=1, rowspan=2, sticky='e')

        bar = tk.Frame(self, bg=CARD, padx=20, pady=12)
        bar.grid(row=1, column=0, sticky='ew', pady=(0, 12))
        bar.columnconfigure(1, weight=1)
        tk.Label(bar, text='搜索', bg=CARD, fg=DIM, font=(FONT, 10)).grid(row=0, column=0, padx=(0, 12))
        self.search = ttk.Entry(bar, textvariable=self.query, style='Field.TEntry', font=(FONT, 10))
        self.search.grid(row=0, column=1, sticky='ew')
        self.search.bind('<Escape>', lambda _: self.query.set(''))
        self.placeholder = tk.Label(bar, text='名称、标识或类型…   Ctrl+F', bg=RAISED, fg=DIM, font=(FONT, 10))
        self.placeholder.bind('<Button-1>', lambda _: self.search.focus_set())
        self.search.bind('<FocusIn>', lambda _: self.update_placeholder(), add='+')
        self.search.bind('<FocusOut>', lambda _: self.update_placeholder(), add='+')
        self.filters = tk.Frame(bar, bg=CARD)
        self.filters.grid(row=0, column=2, padx=(16, 0))
        self.build_filters(self.filters)
        self.count = tk.Label(bar, text='', bg=CARD, fg=DIM, font=(FONT, 9))
        self.count.grid(row=0, column=3, padx=(16, 0))

        box = tk.Frame(self, bg=CARD, padx=2, pady=2)
        box.grid(row=2, column=0, sticky='nsew')
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(box, show='headings', selectmode='browse',
                                 columns=[c[0] for c in self.columns])
        for cid, label, width, numeric in self.columns:
            self.tree.heading(cid, text=label, anchor='e' if numeric else 'w',
                              command=lambda c=cid: self.sort_by(c))
            self.tree.column(cid, width=width, minwidth=60, anchor='e' if numeric else 'w',
                             stretch=not numeric)
        self.tree.tag_configure('odd', background=STRIPE)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(box, orient='vertical', command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>', self.select)
        self.tree.bind('<Double-1>', lambda _: self.focus_value())
        self.tree.bind('<Return>', lambda _: self.focus_value())
        self.empty = tk.Label(box, bg=CARD, fg=DIM, font=(FONT, 11), justify='center')

        ed = tk.Frame(self, bg=CARD, padx=24, pady=18)
        ed.grid(row=3, column=0, sticky='ew', pady=(12, 0))
        ed.columnconfigure(1, weight=1)
        self.sel_title = tk.Label(ed, bg=CARD, fg=TXT, font=(FONT, 13), anchor='w')
        self.sel_title.grid(row=0, column=0, columnspan=2, sticky='w')
        self.sel_detail = tk.Label(ed, bg=CARD, fg=DIM, font=(FONT, 9), anchor='w')
        self.sel_detail.grid(row=1, column=0, columnspan=2, sticky='w', pady=(2, 14))
        self.undo_btn = Pill(ed, '撤销上次  Ctrl+Z', self.undo, kind='ghost')
        self.undo_btn.grid(row=0, column=2, rowspan=2, sticky='ne')

        row = tk.Frame(ed, bg=CARD)
        row.grid(row=2, column=0, columnspan=3, sticky='ew')
        self.segments = []
        if len(self.fields) > 1:
            for f in self.fields:
                b = Pill(row, f['label'], lambda f=f: self.pick_field(f), kind='soft', height=32, padx=16, size=9)
                b.pack(side='left', padx=(0, 6))
                self.segments.append(b)
            tk.Frame(row, bg=CARD, width=10).pack(side='left')
        self.entry = ttk.Entry(row, textvariable=self.value, width=16, style='Field.TEntry',
                               font=(FONT, 11), justify='right')
        self.entry.pack(side='left', ipady=2)
        self.entry.bind('<Return>', lambda _: self.apply())
        self.entry.bind('<KP_Enter>', lambda _: self.apply())
        self.entry.bind('<Escape>', lambda _: self.load_editor())
        self.entry.bind('<Up>', lambda e: self.step(10 if e.state & 1 else 1))
        self.entry.bind('<Down>', lambda e: self.step(-10 if e.state & 1 else -1))
        self.chips = tk.Frame(row, bg=CARD)
        self.chips.pack(side='left', padx=(10, 0))
        for d in (-10, -1, 1, 10, 100):
            Pill(self.chips, f'{d:+d}', lambda d=d: self.step(d), kind='soft', height=28, padx=10, size=9
                 ).pack(side='left', padx=2)
        self.preview = tk.Label(row, bg=CARD, fg=DIM, font=(FONT, 10))
        self.preview.pack(side='left', padx=(16, 0))
        self.apply_btn = Pill(row, '应用修改  Enter', self.apply, kind='primary')
        self.apply_btn.pack(side='right')

        foot = tk.Frame(self, bg=CANVAS)
        foot.grid(row=4, column=0, sticky='ew', pady=(12, 0))
        foot.columnconfigure(0, weight=1)
        self.status = tk.Label(foot, bg=CANVAS, fg=MUTED, font=(FONT, 10), anchor='w', justify='left')
        self.status.grid(row=0, column=0, sticky='ew')
        foot.bind('<Configure>', lambda e: self.status.configure(wraplength=max(200, e.width - 40)))
        if self.note:
            tk.Label(foot, text=self.note, bg=CANVAS, fg=DIM, font=(FONT, 9), anchor='w'
                     ).grid(row=1, column=0, sticky='w', pady=(4, 0))

    def update_placeholder(self):
        if self.query.get() or self.focus_get() is self.search:
            self.placeholder.place_forget()
        else:
            self.placeholder.place(in_=self.search, x=8, rely=0.5, anchor='w')

    # ---- 状态 ----
    def set_status(self, text, tone='info'):
        mark = {'ok': '✓  ', 'error': '✕  ', 'busy': '', 'info': ''}[tone]
        color = {'ok': TXT, 'error': ERROR, 'busy': MUTED, 'info': MUTED}[tone]
        self.status_message = text
        self.status.configure(text=mark + text, fg=color)

    def update_controls(self):
        idle = not self.app.busy
        self.refresh_btn.set_enabled(idle)
        self.undo_btn.set_enabled(idle and self.undo_state is not None)
        has = idle and self.selected is not None and self.snapshot is not None
        self.entry.configure(state='normal' if has else 'disabled')
        for b in self.chips.winfo_children():
            b.set_enabled(has and self.field['kind'] is int)
        for b, f in zip(self.segments, self.fields):
            b.set_enabled(has and self.available(self.selected, f))
            b.configure(kind='on' if f is self.field else 'soft')
        self.apply_btn.set_enabled(has and self.parsed is not None)

    # ---- 读取 ----
    def refresh(self):
        self.wants_load = False
        self.app.job(self, self.load, self.loaded, '正在读取游戏数据…')

    def loaded(self, snapshot, quiet=False):
        self.snapshot = snapshot
        if self.selected is not None:
            key = self.ident(self.selected)
            self.selected = next((e for e in self.entries(snapshot) if self.ident(e) == key), None)
        self.app.set_connection(snapshot['pid'])
        self.render()
        self.load_editor()
        if not quiet:
            note = f"已读取 {len(self.entries(snapshot))} 项 · {time.strftime('%H:%M:%S')}"
            if snapshot.get('name_error'):
                note += ' · 中文名称暂不可用，仍可按标识查看'
            self.set_status(note, 'ok')

    def fail(self, error):
        # 任何失败后都不允许基于旧快照继续写入。
        self.snapshot = self.selected = self.undo_state = None
        self.render()
        self.load_editor()
        self.set_status(error, 'error')
        self.app.set_connection(None, error=True)

    # ---- 列表 ----
    def sort_by(self, cid):
        col, rev = self.sort
        self.sort = (cid, not rev if col == cid else False)
        for c, label, _, numeric in self.columns:
            arrow = ('  ▼' if self.sort[1] else '  ▲') if c == cid else ''
            self.tree.heading(c, text=label + arrow)
        self.render()

    def render(self):
        self.update_placeholder()
        self.tree.delete(*self.tree.get_children())
        rows = list(enumerate(self.entries(self.snapshot))) if self.snapshot else []
        total = len(rows)
        q = self.query.get().strip().casefold()
        rows = [(i, e) for i, e in rows if self.keep(e) and (not q or q in self.haystack(e).casefold())]
        col, rev = self.sort
        if col:
            idx = [c[0] for c in self.columns].index(col)
            numeric = self.columns[idx][3]

            def key(item):
                v = self.row(item[1])[idx]
                if numeric:
                    return (0, v) if isinstance(v, (int, float)) else (1, 0)
                return (0, str(v).casefold())
            rows.sort(key=key, reverse=rev)
        target = self.ident(self.selected) if self.selected is not None else None
        for n, (i, e) in enumerate(rows):
            values = [fmt(v) for v in self.row(e)]
            self.tree.insert('', 'end', iid=str(i), values=values, tags=('odd',) if n % 2 else ())
            if target is not None and self.ident(e) == target:
                self.tree.selection_set(str(i))
                self.tree.see(str(i))
        if not self.snapshot:
            self.count.configure(text='')
            self.empty.configure(text='尚未读取数据\n\n启动游戏并进入存档，然后按 F5 或点击右上角「刷新」')
        elif not rows:
            self.count.configure(text=f'0 / {total}')
            self.empty.configure(text=f'没有匹配「{self.query.get().strip()}」的结果\n\n按 Esc 清空搜索'
                                 if q else '当前筛选下没有内容')
        else:
            self.count.configure(text=f'{len(rows)} / {total}' if len(rows) != total else f'共 {total} 项')
        if rows:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.45, anchor='center')

    def select(self, _=None):
        ids = self.tree.selection()
        if not ids or not self.snapshot:
            return
        entry = self.entries(self.snapshot)[int(ids[0])]
        if entry is not self.selected:
            self.selected = entry
            self.load_editor()

    # ---- 编辑 ----
    def load_editor(self):
        e = self.selected
        if e is None:
            self.sel_title.configure(text='未选择', fg=DIM)
            self.sel_detail.configure(text='在上方列表中选择一行；双击或按 Enter 直接编辑数值')
            self.value.set('')
        else:
            if not self.available(e, self.field):
                self.field = next((f for f in self.fields if self.available(e, f)), self.fields[0])
            self.sel_title.configure(text=self.name(e), fg=TXT)
            self.sel_detail.configure(text=self.detail(e))
            self.value.set(fmt(e.get(self.field['key'])) if self.available(e, self.field) else '')
        self.validate()

    def pick_field(self, field):
        self.field = field
        self.load_editor()
        self.focus_value()

    def focus_value(self):
        if self.selected is not None and str(self.entry['state']) != 'disabled':
            self.entry.focus_set()
            self.entry.select_range(0, 'end')
            self.entry.icursor('end')

    def parse(self, text):
        f = self.field
        try:
            v = f['kind'](text.strip().replace(',', ''))
        except ValueError:
            return None
        if f['kind'] is float:
            try:
                struct.pack('<f', v)
            except OverflowError:
                return None
            if not math.isfinite(v):
                return None
        if 'lo' in f and not f['lo'] <= v <= f['hi']:
            return None
        return v

    def validate(self):
        self.parsed = None
        e = self.selected
        if e is None or not self.available(e, self.field):
            self.preview.configure(text='' if e is None else f"此项没有{self.field['label']}", fg=DIM)
            self.entry.configure(style='Field.TEntry')
            self.update_controls()
            return
        v = self.parse(self.value.get())
        current = e[self.field['key']]
        if v is None:
            rule = '整数' if self.field['kind'] is int else '有限数字'
            if 'lo' in self.field:
                rule += f" {self.field['lo']}–{self.field['hi']}"
            self.preview.configure(text=f'请输入{rule}', fg=ERROR)
            self.entry.configure(style='Bad.TEntry')
        elif self.same(v, current):
            self.preview.configure(text=f"当前{self.field['label']} {fmt(current)}", fg=DIM)
            self.entry.configure(style='Field.TEntry')
        else:
            self.parsed = v
            self.preview.configure(text=f'{fmt(current)}  →  {fmt(v)}', fg=TXT)
            self.entry.configure(style='Field.TEntry')
        self.update_controls()

    def same(self, a, b):
        if self.field['kind'] is float:
            return struct.pack('<f', a) == struct.pack('<f', b)  # 游戏内是 float32
        return a == b

    def step(self, delta):
        if self.field['kind'] is not int or self.selected is None or self.app.busy:
            return 'break'
        v = self.parse(self.value.get())
        base = v if v is not None else self.selected[self.field['key']]
        lo, hi = self.field.get('lo', -INT32_MAX - 1), self.field.get('hi', INT32_MAX)
        self.value.set(str(min(hi, max(lo, base + delta))))
        return 'break'

    def apply(self):
        if self.app.busy or self.parsed is None or self.selected is None:
            return
        self.write_job(self.snapshot, dict(self.selected), self.field, self.parsed, undoing=False)

    def undo(self):
        if not self.app.busy and self.undo_state:
            self.write_job(*self.undo_state, undoing=True)

    def write_job(self, snapshot, entry, field, value, undoing):
        key = field['key']

        def task():
            result = self.write(snapshot, entry, key, value)
            try:
                return result, self.load(), None
            except Exception as exc:
                return result, None, str(exc)

        def done(out):
            result, fresh, error = out
            self.undo_state = None
            if fresh:
                self.loaded(fresh, quiet=True)
                matches = [e for e in self.entries(fresh) if self.ident(e) == self.ident(entry)]
                if not undoing and len(matches) == 1 and matches[0][key] == result['after']:
                    self.undo_state = (fresh, dict(matches[0]), field, result['before'])
                self.update_controls()
            else:
                self.snapshot = self.selected = None
                self.render()
                self.load_editor()
            msg = (f"{'已撤销' if undoing else '已修改'}「{self.name(entry)}」{field['label']}："
                   f"{fmt(result['before'])} → {fmt(result['after'])}，已读回验证")
            if result.get('log_error'):
                msg += ' · 修改日志保存失败'
            if error:
                self.set_status(msg + f'。写入成功，但随后刷新失败：{error}', 'error')
            else:
                self.set_status(msg, 'ok')

        verb = '撤销' if undoing else '写入'
        self.app.job(self, task, done, f"正在核对并{verb}「{self.name(entry)}」{field['label']}…")


class Inventory(Page):
    title = '玩家背包'
    sub = '读取背包物品 · 调整数量 · 写入后读回验证'
    columns = (('name', '物品名称', 280, False), ('qty', '数量', 120, True),
               ('name_key', '名称键', 220, False), ('key', '标识', 220, False))
    fields = (dict(key='quantity', label='数量', kind=int, lo=0, hi=INT32_MAX),)

    def build_filters(self, bar):
        self.hide_zero = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text='隐藏数量为 0', variable=self.hide_zero, style='Card.TCheckbutton',
                        command=self.render).pack()

    def load(self):
        inv.attach()
        return inv.read_inventory()

    def ident(self, e):
        return e['key']

    def name(self, e):
        return e.get('name_zh') or e.get('name_key') or e['key']

    def row(self, e):
        return (e.get('name_zh') or '名称待解析', e['quantity'], e.get('name_key', ''), e['key'])

    def detail(self, e):
        return ' · '.join(x for x in (f"当前数量 {e['quantity']}", e.get('name_key'), e['key']) if x)

    def keep(self, e):
        return not (self.hide_zero.get() and e['quantity'] == 0)

    def write(self, snapshot, entry, field, value):
        return inv.change_quantity(snapshot, entry, value)


class Weapon(Page):
    title = '武器配置'
    sub = '主武器与杀气武器 · 等级 / 当前等级攻击倍率'
    columns = (('name', '武器', 240, False), ('kind', '类型', 110, False), ('level', '等级', 90, True),
               ('mult', '攻击倍率', 120, True), ('key', '名称键', 240, False))
    fields = (dict(key='level', label='等级', kind=int, lo=0, hi=INT32_MAX),
              dict(key='multiplier', label='攻击倍率', kind=float))

    def load(self):
        inv.attach()
        return wp.read_weapons()

    def ident(self, e):
        return (e['kind'], e['key'])

    def name(self, e):
        return e.get('name_zh') or e['name_key']

    def row(self, e):
        return (self.name(e), e['kind_label'], e['level'], e.get('multiplier'), e['name_key'])

    def detail(self, e):
        text = f"{e['kind_label']} · Lv.{e['level']} · 攻击倍率 {fmt(e.get('multiplier'))}"
        if e.get('max_config_level'):
            text += f" · 配置等级 1–{e['max_config_level']}（非当前周目上限）"
        return text

    def write(self, snapshot, entry, field, value):
        return wp.change_weapon(snapshot, entry, field, value)


class Character(Page):
    title = '人物属性'
    sub = '当前值 / 基础值 · 写入前核对玩家身份'
    columns = (('label', '属性', 200, False), ('name', '原始名称', 260, False),
               ('current', '当前值', 140, True), ('base', '基础值', 140, True))
    fields = (dict(key='current', label='当前值', kind=float),
              dict(key='base', label='基础值', kind=float))
    note = '当前值可能被装备或状态重新计算覆盖；修改的实际效果与存档持久化尚待验证。'

    def build_filters(self, bar):
        self.show_all = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text='显示全部属性', variable=self.show_all, style='Card.TCheckbutton',
                        command=self.render).pack()

    def load(self):
        return cp.snapshot()

    def entries(self, snapshot):
        return snapshot['values']

    def ident(self, e):
        return e['name']

    def name(self, e):
        label = ATTR_LABELS.get(e['name'])
        return f"{label}  ·  {e['name']}" if label else e['name']

    def row(self, e):
        return (ATTR_LABELS.get(e['name'], e['name']), e['name'], e['current'], e['base'])

    def detail(self, e):
        return f"当前值 {fmt(e['current'])} · 基础值 {fmt(e['base'])} · 偏移 {e['offset']}"

    def keep(self, e):
        return self.show_all.get() or bool(self.query.get().strip()) or e['name'] in ATTR_COMMON

    def write(self, snapshot, entry, field, value):
        return cp.change_attribute(snapshot, entry, field, float(value))


class Sidebar(tk.Canvas):
    """整栏海报背景的侧边导航：文字直接画在 Canvas 上，高亮用半透明层合成进图片。"""
    W = 250
    NAV_Y, NAV_H = 204, 44

    def __init__(self, master, items, command):
        super().__init__(master, width=self.W, bg=SIDEBAR, highlightthickness=0, bd=0)
        self.items, self.command = items, command
        self.active = self.hover = None
        self.conn = (None, False)
        self.src = self.base = None
        self.base_h = 0
        path = os.path.join(HERE, 'picture.png')
        if Image and os.path.exists(path):
            im = Image.open(path).convert('RGB')
            self.src = im.crop((0, 0, im.width, round(im.height * 0.84)))  # 去掉底部倒计时文字
        self.bind('<Configure>', lambda _: self.draw())
        self.bind('<Motion>', self.motion)
        self.bind('<Leave>', lambda _: self.set_hover(None))
        self.bind('<Button-1>', self.click)

    def nav_box(self, i):
        y = self.NAV_Y + i * (self.NAV_H + 2)
        return 12, y, self.W - 12, y + self.NAV_H

    def hit(self, x, y):
        for i in range(len(self.items)):
            x1, y1, x2, y2 = self.nav_box(i)
            if x1 <= x <= x2 and y1 <= y <= y2:
                return i
        return None

    def motion(self, e):
        self.set_hover(self.hit(e.x, e.y))

    def click(self, e):
        i = self.hit(e.x, e.y)
        if i is not None:
            self.command(i)

    def set_hover(self, i):
        if i != self.hover:
            self.hover = i
            self.configure(cursor='hand2' if i is not None else '')
            self.draw()

    def set_active(self, i):
        self.active = i
        self.draw()

    def set_connection(self, pid, error=False):
        self.conn = (pid, error)
        self.draw()

    def background(self, h):
        # 等比铺满整栏并偏向人物主体裁切；顶部压暗更多以保证菜单文字可读。
        if self.base is not None and self.base_h == h:
            return self.base
        src, w = self.src, self.W
        s = max(w / src.width, h / src.height)
        im = src.resize((max(w, round(src.width * s)), max(h, round(src.height * s))), Image.LANCZOS)
        left = min(max(0, round(im.width * 0.55 - w / 2)), im.width - w)
        top = min(max(0, round(im.height * 0.5 - h / 2)), im.height - h)
        im = im.crop((left, top, left + w, top + h))
        im = ImageEnhance.Color(im).enhance(0.45)
        im = ImageEnhance.Brightness(im).enhance(0.6)
        shade = Image.linear_gradient('L').resize((w, h)).point(lambda v: round(225 - v * 0.55))
        im = Image.composite(Image.new('RGB', (w, h), SIDEBAR), im, shade)
        self.base, self.base_h = im.convert('RGBA'), h
        return self.base

    def draw(self):
        h = max(self.winfo_height(), 1)
        w = self.W
        self.delete('all')
        conn_box = (18, 122, w - 18, 162)
        boxes = [(conn_box, (30, 30, 42, 200))]
        for i in range(len(self.items)):
            if i == self.active:
                boxes.append((self.nav_box(i), (39, 39, 53, 225)))
            elif i == self.hover:
                boxes.append((self.nav_box(i), (30, 30, 42, 160)))
        if self.src is not None:
            layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
            d = ImageDraw.Draw(layer)
            for box, fill in boxes:
                d.rounded_rectangle(box, radius=6, fill=fill)
            self.pic = ImageTk.PhotoImage(Image.alpha_composite(self.background(h), layer))
            self.create_image(0, 0, image=self.pic, anchor='nw')
        else:
            for box, fill in boxes:
                self.create_rectangle(*box, fill='#%02x%02x%02x' % fill[:3], outline='')
        self.create_line(w - 1, 0, w - 1, h, fill=LINE)

        self.create_text(28, 34, text='影之刃', fill=TXT, font=(FONT, 22), anchor='nw')
        self.create_text(29, 80, text='玄锋  ·  控制台', fill=DIM, font=(FONT, 10), anchor='nw')
        pid, error = self.conn
        color = COBALT if pid else (ERROR if error else SLATE)
        self.create_oval(32, 138, 40, 146, fill=color, outline=color)
        text = f'已连接  ·  PID {pid}' if pid else ('连接中断，请重新读取' if error else '未连接')
        self.create_text(52, 142, text=text, fill=TXT if pid else MUTED, font=(FONT, 9), anchor='w')

        self.create_text(28, 188, text='工作区', fill=DIM, font=(FONT, 9), anchor='w')
        for i, (label, hint) in enumerate(self.items):
            x1, y1, x2, y2 = self.nav_box(i)
            if i == self.active:
                self.create_rectangle(x1, y1, x1 + 3, y2, fill=COBALT, outline='')
            lit = i == self.active or i == self.hover
            self.create_text(x1 + 20, (y1 + y2) / 2, text=label, fill=TXT if lit else MUTED,
                             font=(FONT, 11), anchor='w')
            self.create_text(x2 - 12, (y1 + y2) / 2, text=hint, fill=DIM, font=(FONT, 8), anchor='e')
        _, _, _, last = self.nav_box(len(self.items) - 1)
        self.create_text(28, last + 28, text='每次写入前核对进程与对象身份，写入后读回验证；任何失败都需重新读取。',
                         fill=DIM, font=(FONT, 8), anchor='nw', width=190)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('影之刃  ·  玄锋控制台')
        self.set_icon()
        self.geometry('1280x820')
        self.minsize(1060, 700)
        self.configure(bg=CANVAS)
        self.busy = False
        self.events = queue.Queue()
        self.spin = 0
        self.styles()
        self.layout()
        self.bind_keys()
        self.after(60, self.poll)
        self.after(250, lambda: self.show(0))

    def styles(self):
        s = ttk.Style(self)
        s.theme_use('clam')
        s.configure('.', font=(FONT, 10), background=CANVAS, foreground=TXT)
        for name, border in (('Field.TEntry', LINE), ('Bad.TEntry', ERROR)):
            s.configure(name, fieldbackground=RAISED, foreground=TXT, insertcolor=TXT,
                        bordercolor=border, lightcolor=RAISED, darkcolor=RAISED, padding=(10, 6))
            s.map(name, bordercolor=[('focus', ERROR if name == 'Bad.TEntry' else COBALT)],
                  lightcolor=[('focus', RAISED)], fieldbackground=[('disabled', CARD)],
                  foreground=[('disabled', DIM)])
        s.configure('Treeview', background=CARD, fieldbackground=CARD, foreground=TXT,
                    rowheight=36, borderwidth=0, font=(FONT, 10))
        s.map('Treeview', background=[('selected', COBALT_DIM)], foreground=[('selected', WHITE)])
        s.layout('Treeview', [('Treeview.treearea', {'sticky': 'nswe'})])
        s.configure('Treeview.Heading', background=CARD, foreground=DIM, relief='flat',
                    borderwidth=0, padding=(10, 10), font=(FONT, 9))
        s.map('Treeview.Heading', background=[('active', RAISED)], foreground=[('active', TXT)])
        s.configure('Vertical.TScrollbar', troughcolor=CARD, background=RAISED, bordercolor=CARD,
                    lightcolor=RAISED, darkcolor=RAISED, arrowcolor=DIM, gripcount=0, arrowsize=12)
        s.map('Vertical.TScrollbar', background=[('active', HOVER)])
        s.configure('Card.TCheckbutton', background=CARD, foreground=MUTED, indicatorbackground=RAISED,
                    indicatorforeground=TXT, focuscolor=CARD, font=(FONT, 10))
        s.map('Card.TCheckbutton', background=[('active', CARD)], foreground=[('active', TXT)],
              indicatorbackground=[('selected', COBALT), ('active', HOVER)])

    def layout(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self.sidebar = Sidebar(self, [('背包总览', 'Ctrl+1'), ('武器配置', 'Ctrl+2'), ('人物属性', 'Ctrl+3')],
                               self.show)
        self.sidebar.grid(row=0, column=0, sticky='nsew')
        self.main = tk.Frame(self, bg=CANVAS, padx=36, pady=30)
        self.main.grid(row=0, column=1, sticky='nsew')
        self.main.columnconfigure(0, weight=1)
        self.main.rowconfigure(0, weight=1)
        self.pages = [Inventory(self), Weapon(self), Character(self)]
        self.current = None

    def set_icon(self):
        if sys.platform == 'win32':
            try:  # 让任务栏显示本程序图标，而不是 python.exe 的图标
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('ShadowBlade.XuanfengConsole')
            except (AttributeError, OSError):
                pass
        ico, png = (os.path.join(HERE, 'assets', n) for n in ('icon.ico', 'icon.png'))
        try:
            if sys.platform == 'win32' and os.path.exists(ico):
                self.iconbitmap(default=ico)
            if os.path.exists(png):
                self.icon_image = tk.PhotoImage(file=png)
                self.iconphoto(True, self.icon_image)
        except tk.TclError:
            pass

    def undo_shortcut(self):
        # 输入框里的 Ctrl+Z 不应触发游戏内存写入。
        if self.current and not isinstance(self.focus_get(), (tk.Entry, ttk.Entry)):
            self.current.undo()

    def bind_keys(self):
        self.bind_all('<F5>', lambda _: self.current and not self.busy and self.current.refresh())
        for key in ('<Control-f>', '<Control-F>'):
            self.bind_all(key, lambda _: self.current and self.current.search.focus_set())
        for key in ('<Control-z>', '<Control-Z>'):
            self.bind_all(key, lambda _: self.undo_shortcut())
        for i in range(3):
            self.bind_all(f'<Control-Key-{i + 1}>', lambda _, i=i: self.show(i))

    def show(self, i):
        page = self.pages[i]
        if self.current is page:
            return
        if self.current:
            self.current.grid_remove()
        self.current = page
        page.grid(row=0, column=0, sticky='nsew')
        self.sidebar.set_active(i)
        page.update_controls()
        if page.wants_load and not self.busy:
            page.refresh()

    def set_connection(self, pid, error=False):
        self.sidebar.set_connection(pid, error)

    # ---- 后台任务 ----
    def job(self, page, task, done, message):
        if self.busy:
            return False
        self.busy = True
        self.job_page, self.job_message = page, message
        page.set_status(message, 'busy')
        for p in self.pages:
            p.update_controls()
        self.configure(cursor='watch')

        def work():
            try:
                self.events.put((page, done, task(), None))
            except Exception as exc:
                self.events.put((page, done, None, str(exc) or exc.__class__.__name__))
        threading.Thread(target=work, daemon=True).start()
        return True

    def poll(self):
        try:
            while True:
                page, done, value, error = self.events.get_nowait()
                self.busy = False
                self.configure(cursor='')
                if error:
                    page.fail(error)
                else:
                    done(value)
                for p in self.pages:
                    p.update_controls()
                if self.current.wants_load:
                    self.current.refresh()
        except queue.Empty:
            pass
        if self.busy:
            self.spin = (self.spin + 1) % len(SPINNER)
            self.job_page.status.configure(text=f'{SPINNER[self.spin]}  {self.job_message}')
        self.after(90, self.poll)


if __name__ == '__main__':
    App().mainloop()
