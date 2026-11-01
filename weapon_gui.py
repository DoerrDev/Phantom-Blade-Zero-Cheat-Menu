"""Owned weapon tab for the unified inventory application."""
import queue
import threading
import tkinter as tk
from tkinter import ttk

import weapon_backend as backend


class WeaponPanel(ttk.Frame):
    def __init__(self, parent, service=backend, auto_refresh=True):
        super().__init__(parent, padding=22)
        self.service = service
        self.snapshot = self.selected = self.undo_state = None
        self.busy = False
        self.events = queue.Queue()
        self.search, self.level, self.multiplier = (tk.StringVar() for _ in range(3))
        body = self
        head = ttk.Frame(body)
        head.pack(fill='x')
        ttk.Label(head, text='已拥有武器', font=('Microsoft YaHei UI', 20, 'bold')).pack(side='left')
        self.refresh_button = ttk.Button(head, text='连接并刷新', command=self.refresh)
        self.refresh_button.pack(side='right')
        self.summary = ttk.Label(body, text='读取存档中的武器记录…')
        self.summary.pack(anchor='w', pady=(12, 8))
        search = ttk.Frame(body)
        search.pack(fill='x', pady=(0, 10))
        ttk.Label(search, text='搜索武器').pack(side='left')
        ttk.Entry(search, textvariable=self.search, width=38).pack(side='left', padx=10)
        table = ttk.Frame(body)
        table.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(table, columns=('name', 'kind', 'level', 'multiplier', 'key'),
                                 show='headings', selectmode='browse')
        for col, title, width in [('name', '武器名称', 230), ('kind', '类型', 95),
                                  ('level', '等级', 70), ('multiplier', '当前等级攻击倍率', 155),
                                  ('key', '名称键', 230)]:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, minwidth=60)
        scrollbar = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        self.tree.bind('<<TreeviewSelect>>', self.select)
        self.detail = ttk.Label(body, text='选择一把武器', wraplength=940)
        self.detail.pack(anchor='w', pady=(14, 10))
        row = ttk.Frame(body)
        row.pack(fill='x', pady=5)
        ttk.Label(row, text='目标等级').pack(side='left')
        self.level_entry = ttk.Entry(row, textvariable=self.level, width=12)
        self.level_entry.pack(side='left', padx=10)
        self.level_button = ttk.Button(row, text='应用等级', command=lambda: self.apply('level'))
        self.level_button.pack(side='left')
        row = ttk.Frame(body)
        row.pack(fill='x', pady=5)
        ttk.Label(row, text='攻击倍率').pack(side='left')
        self.multiplier_entry = ttk.Entry(row, textvariable=self.multiplier, width=12)
        self.multiplier_entry.pack(side='left', padx=10)
        self.multiplier_button = ttk.Button(row, text='应用当前等级倍率', command=lambda: self.apply('multiplier'))
        self.multiplier_button.pack(side='left')
        self.undo_button = ttk.Button(row, text='撤销上次修改', command=self.undo)
        self.undo_button.pack(side='left', padx=10)
        ttk.Label(body, text='倍率修改作用于该武器种类的当前等级配置，重新启动后通常恢复；1 级不开放倍率修改。'
                  '\n直接改等级不会执行升级奖励或刷新回调，游戏中的属性生效、保存及读档结果仍需核对。'
                  '\n伤害与破防的独立数值字段尚未确认。修改后可切换装备、重新打开武器界面检查。',
                  foreground='#64748b', wraplength=940).pack(anchor='w', pady=12)
        self.status = ttk.Label(body, text='准备就绪', wraplength=940)
        self.status.pack(anchor='w')
        self.search.trace_add('write', lambda *_: self.render())
        self.poll_id = self.after(80, self.poll)
        self.controls()
        if auto_refresh:
            self.after(100, self.refresh)

    def destroy(self):
        self.after_cancel(self.poll_id)
        super().destroy()

    def controls(self):
        enabled = not self.busy and self.selected is not None
        for w, ok in [(self.refresh_button, not self.busy), (self.level_button, enabled),
                      (self.level_entry, enabled), (self.undo_button, not self.busy and bool(self.undo_state)),
                      (self.multiplier_button, enabled and self.selected.get('multiplier') is not None),
                      (self.multiplier_entry, enabled and self.selected.get('multiplier') is not None)]:
            w.configure(state='normal' if ok else 'disabled')

    def job(self, task, done, message):
        if self.busy:
            return
        self.busy = True
        self.status.configure(text=message, foreground='#334155')
        self.controls()
        def work():
            try:
                self.events.put((done, task(), None))
            except Exception as exc:
                self.events.put((done, None, str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            while True:
                done, value, error = self.events.get_nowait()
                self.busy = False
                if error:
                    self.snapshot = self.selected = self.undo_state = None
                    self.summary.configure(text='武器读取或校验失败，请重新刷新')
                    self.tree.delete(*self.tree.get_children())
                    self.detail.configure(text='刷新后重新选择武器')
                    self.status.configure(text=error, foreground='#b91c1c')
                else:
                    done(value)
                self.controls()
        except queue.Empty:
            pass
        self.poll_id = self.after(80, self.poll)

    def refresh(self):
        def task():
            self.service.attach()
            return self.service.read_weapons()
        def done(state):
            self.undo_state = None
            self.loaded(state)
        self.job(task, done, '正在核对游戏并读取武器…')

    def loaded(self, state):
        self.snapshot = state
        self.summary.configure(text=f"PID {state['pid']} · {len(state['entries'])} 把已拥有武器")
        self.render()
        self.status.configure(text='武器已刷新。' + (' 部分名称未解析。' if state.get('name_error') else ''))

    def render(self):
        old = (self.selected['kind'], self.selected['key']) if self.selected else None
        self.tree.delete(*self.tree.get_children())
        self.selected = None
        query = self.search.get().strip().casefold()
        if self.snapshot:
            for i, entry in enumerate(self.snapshot['entries']):
                name = entry.get('name_zh') or entry['name_key']
                if query and query not in f"{name} {entry['name_key']} {entry['key']}".casefold():
                    continue
                scalar = '—' if entry['multiplier'] is None else f"{entry['multiplier']:.6g}"
                self.tree.insert('', 'end', iid=str(i), values=(name, entry['kind_label'], entry['level'], scalar, entry['name_key']))
                if old == (entry['kind'], entry['key']):
                    self.tree.selection_set(str(i))
        if self.tree.selection():
            self.select()
        else:
            self.detail.configure(text='选择一把武器')
            self.level.set('')
            self.multiplier.set('')
        self.controls()

    def select(self, _event=None):
        ids = self.tree.selection()
        if ids and self.snapshot:
            self.selected = self.snapshot['entries'][int(ids[0])]
            e = self.selected
            self.detail.configure(text=f"{e.get('name_zh') or e['name_key']} · 等级 {e['level']} · "
                                  f"配置范围 1–{e['max_config_level']}（不代表当前周目升级上限）")
            self.level.set(str(e['level']))
            self.multiplier.set('' if e['multiplier'] is None else f"{e['multiplier']:.6g}")
            self.controls()

    def apply(self, field):
        if self.busy or not self.selected:
            return
        try:
            value = int(self.level.get()) if field == 'level' else float(self.multiplier.get())
        except ValueError:
            self.status.configure(text='请输入有效数值。', foreground='#b91c1c')
            return
        if value == self.selected[field]:
            self.status.configure(text='数值未变化。')
            return
        self.write(self.snapshot, dict(self.selected), field, value)

    def write(self, state, entry, field, value, undoing=False):
        def task():
            result = self.service.change_weapon(state, entry, field, value)
            try:
                return result, self.service.read_weapons(), None
            except Exception as exc:
                return result, None, str(exc)
        def done(output):
            result, fresh, error = output
            self.undo_state = None
            if fresh:
                self.loaded(fresh)
                matches = [e for e in fresh['entries'] if e['key'] == entry['key'] and e['kind'] == entry['kind']]
                if not undoing and len(matches) == 1 and matches[0][field] == result['after']:
                    self.undo_state = (fresh, dict(matches[0]), field, result['before'])
            else:
                self.snapshot = self.selected = None
                self.tree.delete(*self.tree.get_children())
            label = '等级' if field == 'level' else '当前等级攻击倍率'
            message = f"{'已撤销' if undoing else '已修改'}{label}：{result['before']:.6g} → {result['after']:.6g}，读回一致。"
            if error:
                message += ' 写入已成功，后续刷新失败：' + error
            if result.get('log_error'):
                message += ' 修改日志保存失败。'
            self.status.configure(text=message, foreground='#15803d')
        self.job(task, done, '正在核对武器并写入选定字段…')

    def undo(self):
        if not self.busy and self.undo_state:
            self.write(*self.undo_state, undoing=True)


if __name__ == '__main__':
    from inventory_gui import InventoryApp
    app = InventoryApp()
    app.open_weapons()
    app.mainloop()
