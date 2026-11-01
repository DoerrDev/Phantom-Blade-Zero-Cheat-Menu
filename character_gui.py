"""Character attribute tab for the unified PBZ tool."""
import queue
import threading
import tkinter as tk
from tkinter import ttk

import character_probe


LABELS = {
    'Level': '等级字段', 'Health': '生命', 'Health_Max': '生命上限',
    'Shield': '护盾', 'Shield_Max': '护盾上限', 'Attack': '攻击字段',
    'WalkSpeed': '行走速度', 'JogSpeed': '跑步速度', 'SprintSpeed': '冲刺速度',
    'CrouchSpeed': '蹲行速度', 'FinalWalkSpeed': '最终移动速度',
    'UltimateCharge': '终极技能充能', 'UltimateCharge_Max': '终极技能充能上限',
    'Awakening': '觉醒字段', 'Awakening_Max': '觉醒上限字段',
}
COMMON = set(LABELS) | {'Shachi', 'Shachi_Max', 'SpiritShadow', 'SpiritShadow_Max'}


class CharacterPanel(ttk.Frame):
    def __init__(self, master, service=character_probe):
        super().__init__(master, padding=24)
        self.service = service
        self.snapshot = None
        self.busy = False
        self.selected = None
        self.undo_state = None
        self.amount = tk.StringVar()
        self.field = tk.StringVar(value='当前值')
        self.events = queue.Queue()
        self.search = tk.StringVar()
        self.show_all = tk.BooleanVar(value=False)
        header = ttk.Frame(self)
        header.pack(fill='x')
        ttk.Label(header, text='人物属性', style='Title.TLabel').pack(side='left')
        self.refresh_button = ttk.Button(header, text='连接 / 刷新属性', command=self.refresh)
        self.refresh_button.pack(side='right')
        self.connection = ttk.Label(self, text='进入游戏存档后刷新人物属性。')
        self.connection.pack(anchor='w', pady=(14, 10))
        tools = ttk.Frame(self)
        tools.pack(fill='x', pady=(0, 12))
        ttk.Label(tools, text='搜索属性').pack(side='left', padx=(0, 8))
        ttk.Entry(tools, textvariable=self.search, width=38).pack(side='left')
        ttk.Checkbutton(tools, text='显示全部属性', variable=self.show_all,
                        command=self.render).pack(side='left', padx=16)
        table = ttk.Frame(self)
        table.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(table, columns=('label', 'name', 'current', 'base'),
                                 show='headings', selectmode='browse')
        for col, title, width in [('label', '属性', 190), ('name', '原始名称', 430),
                                  ('current', '当前值', 145), ('base', '基础值', 145)]:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, minwidth=85,
                             anchor='e' if col in ('current', 'base') else 'w')
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.tree.bind('<<TreeviewSelect>>', self.select)
        self.selection_label = ttk.Label(self, text='选择属性以修改')
        self.selection_label.pack(anchor='w', pady=(14, 6))
        editor = ttk.Frame(self)
        editor.pack(fill='x')
        self.field_box = ttk.Combobox(editor, textvariable=self.field, values=('当前值', '基础值'),
                                      width=10, state='readonly')
        self.field_box.pack(side='left')
        self.field_box.bind('<<ComboboxSelected>>', self.select)
        self.amount_entry = ttk.Entry(editor, textvariable=self.amount, width=20)
        self.amount_entry.pack(side='left', padx=10)
        self.apply_button = ttk.Button(editor, text='应用修改', command=self.apply)
        self.apply_button.pack(side='left')
        self.undo_button = ttk.Button(editor, text='撤销上次修改', command=self.undo)
        self.undo_button.pack(side='left', padx=10)
        ttk.Label(self, text='当前值可能被装备或状态重新计算覆盖。修改的实际效果与存档持久化尚待验证。',
                  foreground='#64748b', wraplength=970).pack(anchor='w', pady=(14, 6))
        self.status = ttk.Label(self, text='准备就绪', wraplength=970)
        self.status.pack(anchor='w')
        self.search.trace_add('write', lambda *_: self.render())
        self.poll_id = self.after(80, self.poll)
        self.update_controls()

    def update_controls(self):
        self.refresh_button.configure(state='disabled' if self.busy else 'normal')
        enabled = not self.busy and self.selected is not None
        for control in (self.apply_button, self.amount_entry):
            control.configure(state='normal' if enabled else 'disabled')
        self.field_box.configure(state='readonly' if enabled else 'disabled')
        self.undo_button.configure(state='normal' if not self.busy and self.undo_state else 'disabled')

    def select(self, _event=None):
        ids = self.tree.selection()
        self.selected = next((r for r in (self.snapshot or {}).get('values', [])
                              if ids and r['name'] == ids[0]), None)
        if self.selected:
            key = 'current' if self.field.get() == '当前值' else 'base'
            self.amount.set(repr(self.selected[key]))
            self.selection_label.configure(text=f"{LABELS.get(self.selected['name'], self.selected['name'])} · {self.field.get()}")
        else:
            self.amount.set('')
            self.selection_label.configure(text='选择属性以修改')
        self.update_controls()

    def apply(self):
        if self.busy or not self.selected:
            return
        try:
            value = float(self.amount.get())
            import math
            if not math.isfinite(value):
                raise ValueError()
        except ValueError:
            self.status.configure(text='请输入有限数字。', foreground='#b91c1c')
            return
        field = 'current' if self.field.get() == '当前值' else 'base'
        self.perform_write(dict(self.selected), field, value, False)

    def undo(self):
        if self.busy or not self.undo_state:
            return
        entry, field, value = self.undo_state
        self.perform_write(entry, field, value, True)

    def perform_write(self, entry, field, value, undoing):
        snapshot = self.snapshot
        def done(result):
            for row in snapshot['values']:
                if row['name'] == entry['name']:
                    row[field] = result['after']
                    self.undo_state = None if undoing else (dict(row), field, result['before'])
            self.render()
            self.status.configure(text=f"{'已撤销' if undoing else '已修改'} {entry['name']}："
                                  f"{result['before']:.7g} → {result['after']:.7g}，已读回验证。",
                                  foreground='#334155')
        self.job(lambda: self.service.change_attribute(snapshot, entry, field, value), done,
                 '正在校验并修改人物属性…')

    def job(self, task, done, message):
        if self.busy:
            return
        self.busy = True
        self.update_controls()
        self.status.configure(text=message, foreground='#334155')
        def work():
            try:
                self.events.put((done, task(), None))
            except Exception as exc:
                self.events.put((done, None, str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def refresh(self):
        if self.busy:
            return
        def done(value):
            self.snapshot = value
            self.undo_state = None
            self.render()
            self.connection.configure(text=f"已连接 · PID {value['pid']} · 本地玩家人物属性")
            self.status.configure(text=f"已刷新 {len(value['values'])} 项属性。", foreground='#334155')
        self.job(self.service.snapshot, done, '正在读取人物属性…')

    def poll(self):
        try:
            done, value, error = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            if error:
                self.snapshot = None
                self.undo_state = None
                self.render()
                self.connection.configure(text='操作失败，请刷新后重试')
                self.status.configure(text=error, foreground='#b91c1c')
            else:
                done(value)
            self.update_controls()
        self.poll_id = self.after(80, self.poll)

    def render(self):
        selected = self.selected['name'] if self.selected else None
        self.tree.delete(*self.tree.get_children())
        query = self.search.get().strip().casefold()
        for row in (self.snapshot or {}).get('values', []):
            name = row['name']
            label = LABELS.get(name, name)
            if query:
                if query not in f'{label} {name}'.casefold():
                    continue
            elif not self.show_all.get() and name not in COMMON:
                continue
            self.tree.insert('', 'end', iid=name, values=(label, name, f"{row['current']:.7g}",
                                                 f"{row['base']:.7g}"))
            if name == selected:
                self.tree.selection_set(name)
        self.select()

    def destroy(self):
        if self.poll_id:
            self.after_cancel(self.poll_id)
            self.poll_id = None
        super().destroy()
