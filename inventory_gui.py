"""Local PBZ inventory GUI; launch with pythonw inventory_gui.py."""
from __future__ import annotations

import csv
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

import inventory_gui_backend as backend


class InventoryApp(tk.Tk):
    def __init__(self, service=backend, auto_connect=True, weapon_service=None, character_service=None):
        super().__init__()
        self.service = service
        self.title('影之刃 · 背包、武器与人物属性')
        self.geometry('1100x900')
        self.minsize(950, 850)
        self.configure(bg='#eef2f6')
        self.snapshot = None
        self.selected = None
        self.undo_state = None
        self.busy = False
        self.events = queue.Queue()
        self.search = tk.StringVar()
        self.amount = tk.StringVar()
        self.hide_zero = tk.BooleanVar(value=False)
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), background='#eef2f6')
        style.configure('Treeview', rowheight=32, background='white', fieldbackground='white', borderwidth=0)
        style.configure('Treeview.Heading', font=('Microsoft YaHei UI', 10, 'bold'), padding=8)
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 22, 'bold'), foreground='#17283e')
        style.configure('Accent.TButton', background='#2563eb', foreground='white', padding=(16, 9))
        style.map('Accent.TButton', background=[('disabled', '#a1acc0'), ('active', '#1d4ed8')])
        style.configure('TNotebook.Tab', padding=(20, 10), font=('Microsoft YaHei UI', 11, 'bold'))
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill='both', expand=True, padx=12, pady=12)
        body = ttk.Frame(self.notebook, padding=24)
        self.inventory_tab = body
        self.notebook.add(body, text='玩家背包')
        header = ttk.Frame(body)
        header.pack(fill='x')
        ttk.Label(header, text='玩家背包', style='Title.TLabel').pack(side='left')
        self.connection = ttk.Label(header, text='尚未连接', foreground='#64748b')
        self.connection.pack(side='left', padx=20)
        self.connect_button = ttk.Button(header, text='连接游戏', command=self.connect)
        self.connect_button.pack(side='right')
        self.refresh_button = ttk.Button(header, text='刷新背包', command=self.refresh)
        self.refresh_button.pack(side='right', padx=8)
        toolbar = ttk.Frame(body, padding=(0, 20, 0, 12))
        toolbar.pack(fill='x')
        ttk.Label(toolbar, text='搜索').pack(side='left', padx=(0, 8))
        self.search_entry = ttk.Entry(toolbar, textvariable=self.search, width=40)
        self.search_entry.pack(side='left')
        ttk.Checkbutton(toolbar, text='隐藏数量为 0 的物品', variable=self.hide_zero, command=self.render).pack(side='left', padx=16)
        self.export_button = ttk.Button(toolbar, text='导出 CSV', command=self.export)
        self.export_button.pack(side='right')
        self.summary = ttk.Label(body, text='进入游戏存档后连接，直接通过 Windows 读取背包。')
        self.summary.pack(anchor='w', pady=(0, 10))
        table = ttk.Frame(body)
        table.pack(fill='both', expand=True)
        columns = ('name', 'quantity', 'name_key', 'key')
        self.tree = ttk.Treeview(table, columns=columns, show='headings', selectmode='browse')
        for col, title, width in [('name', '物品名称', 240), ('quantity', '数量', 90), ('name_key', '名称键', 390), ('key', '物品标识', 150)]:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, minwidth=65, anchor='e' if col == 'quantity' else 'w')
        scrollbar = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        self.tree.bind('<<TreeviewSelect>>', self.select)
        editor = ttk.Frame(body, padding=(0, 18, 0, 12))
        editor.pack(fill='x')
        self.selection_label = ttk.Label(editor, text='选择物品以调整数量')
        self.selection_label.pack(anchor='w', pady=(0, 10))
        row = ttk.Frame(editor)
        row.pack(fill='x')
        ttk.Label(row, text='目标数量').pack(side='left', padx=(0, 10))
        self.amount_entry = ttk.Entry(row, textvariable=self.amount, width=15)
        self.amount_entry.pack(side='left')
        self.apply_button = ttk.Button(row, text='应用数量', style='Accent.TButton', command=self.apply)
        self.apply_button.pack(side='left', padx=10)
        self.undo_button = ttk.Button(row, text='撤销上次修改', command=self.undo)
        self.undo_button.pack(side='left')
        ttk.Label(body, text='名称尚未解析的物品显示其标识。修改后可重新打开游戏背包查看；存档持久化尚未验证。', foreground='#64748b', wraplength=970).pack(anchor='w', pady=6)
        self.status = ttk.Label(body, text='准备就绪', foreground='#334155', wraplength=970)
        self.status.pack(anchor='w', pady=(6, 0))
        from weapon_gui import WeaponPanel
        import weapon_backend
        self.weapon_panel = WeaponPanel(self.notebook, service=weapon_service or weapon_backend,
                                        auto_refresh=False)
        self.notebook.add(self.weapon_panel, text='武器属性')
        from character_gui import CharacterPanel
        import character_probe
        self.character_panel = CharacterPanel(self.notebook, service=character_service or character_probe)
        self.notebook.add(self.character_panel, text='人物属性')
        self.notebook.bind('<<NotebookTabChanged>>', self.tab_changed)
        self.search.trace_add('write', lambda *_: self.render())
        self.poll_id = self.after(80, self.poll)
        self.update_controls()
        if auto_connect:
            self.after(300, self.connect)

    def open_weapons(self):
        self.notebook.select(self.weapon_panel)

    def tab_changed(self, _event=None):
        if (self.notebook.select() == str(self.weapon_panel)
                and self.weapon_panel.snapshot is None and not self.weapon_panel.busy):
            self.weapon_panel.refresh()
        if (self.notebook.select() == str(self.character_panel)
                and self.character_panel.snapshot is None and not self.character_panel.busy):
            self.character_panel.refresh()

    def destroy(self):
        if getattr(self, 'poll_id', None):
            self.after_cancel(self.poll_id)
        super().destroy()

    def update_controls(self):
        idle = not self.busy
        for widget, enabled in [(self.connect_button, idle), (self.refresh_button, idle and self.snapshot is not None),
                                (self.export_button, idle and self.snapshot is not None),
                                (self.apply_button, idle and self.selected is not None),
                                (self.undo_button, idle and self.undo_state is not None),
                                (self.amount_entry, idle and self.selected is not None)]:
            widget.configure(state='normal' if enabled else 'disabled')

    def job(self, task, done, message):
        if self.busy:
            return
        self.busy = True
        self.status.configure(text=message, foreground='#334155')
        self.update_controls()
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
                    # Never allow another write from a stale snapshot after any failure.
                    self.snapshot = None
                    self.selected = None
                    self.undo_state = None
                    self.tree.delete(*self.tree.get_children())
                    self.connection.configure(text='需要重新连接')
                    self.selection_label.configure(text='连接并刷新后再选择物品')
                    self.status.configure(text=error, foreground='#b91c1c')
                else:
                    done(value)
                self.update_controls()
        except queue.Empty:
            pass
        self.poll_id = self.after(80, self.poll)

    def connect(self):
        def task():
            self.service.attach()
            return self.service.read_inventory()
        def done(value):
            self.undo_state = None
            self.loaded(value)
        self.job(task, done, '正在连接游戏并读取背包…')

    def refresh(self):
        self.job(self.service.read_inventory, self.loaded, '正在读取背包…')

    def loaded(self, snapshot):
        self.snapshot = snapshot
        self.connection.configure(text=f"已连接 · PID {snapshot['pid']}")
        self.render()
        count = len(snapshot['entries'])
        named = sum(bool(e.get('name_zh')) for e in snapshot['entries'])
        self.summary.configure(text=f'{count} 条物品记录  ·  {named} 条已解析名称')
        note = '背包已刷新。'
        if snapshot.get('name_error'):
            note += ' 中文名称暂不可用，仍可按物品标识查看。'
        self.status.configure(text=note, foreground='#334155')

    def render(self):
        key = self.selected.get('key') if self.selected else None
        self.tree.delete(*self.tree.get_children())
        self.selected = None
        query = self.search.get().strip().casefold()
        if self.snapshot:
            for i, e in enumerate(self.snapshot['entries']):
                name = e.get('name_zh') or '名称待解析'
                haystack = ' '.join([name, e.get('name_key', ''), e['key']]).casefold()
                if query and query not in haystack or self.hide_zero.get() and e['quantity'] == 0:
                    continue
                iid = str(i)
                self.tree.insert('', 'end', iid=iid, values=(name, e['quantity'], e.get('name_key', ''), e['key']))
                if e['key'] == key:
                    self.tree.selection_set(iid)
        if self.tree.selection():
            self.select()
        else:
            self.selection_label.configure(text='选择物品以调整数量')
            self.amount.set('')
        self.update_controls()

    def select(self, _event=None):
        ids = self.tree.selection()
        if not ids or not self.snapshot:
            return
        self.selected = self.snapshot['entries'][int(ids[0])]
        e = self.selected
        self.selection_label.configure(text=f"{e.get('name_zh') or '名称待解析'}  ·  当前数量 {e['quantity']}  ·  {e['key']}")
        self.amount.set(str(e['quantity']))
        self.update_controls()

    def apply(self):
        if self.busy or not self.selected:
            return
        try:
            value = int(self.amount.get().strip())
            if not 0 <= value <= 2147483647:
                raise ValueError()
        except ValueError:
            self.status.configure(text='请输入 0 到 2147483647 的整数。', foreground='#b91c1c')
            return
        snapshot, entry = self.snapshot, dict(self.selected)
        if value == entry['quantity']:
            self.status.configure(text='数量未变化。')
            return
        self.perform_write(snapshot, entry, value, undoing=False)

    def perform_write(self, snapshot, entry, value, undoing):
        def done(result):
            # Reflect verified readback; avoid hiding a successful write behind a later refresh failure.
            if self.snapshot is snapshot:
                for e in snapshot['entries']:
                    if e['key'] == entry['key']:
                        e['quantity'] = result['after']
            self.undo_state = None if undoing else (snapshot, {**entry, 'quantity': result['after']}, result['before'])
            self.render()
            message = f"{'已撤销' if undoing else '已修改'}：{entry.get('name_zh') or entry['key']}，{result['before']} → {result['after']}。已读回验证。"
            if result.get('log_error'):
                message += ' 修改记录保存失败。'
            self.status.configure(text=message, foreground='#15803d')
        self.job(lambda: self.service.change_quantity(snapshot, entry, value), done, '正在核对物品并写入数量…')

    def undo(self):
        if not self.busy and self.undo_state:
            self.perform_write(*self.undo_state, undoing=True)

    def export(self):
        if not self.snapshot or self.busy:
            return
        path = filedialog.asksaveasfilename(parent=self, title='导出背包', defaultextension='.csv', initialfile='玩家背包.csv', filetypes=[('CSV 表格', '*.csv')])
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['物品名称', '数量', '名称键', '物品标识'])
                for e in self.snapshot['entries']:
                    writer.writerow([e.get('name_zh', ''), e['quantity'], e.get('name_key', ''), e['key']])
            self.status.configure(text='背包已导出。')
        except OSError as exc:
            self.status.configure(text=str(exc), foreground='#b91c1c')


if __name__ == '__main__':
    import sys
    app = InventoryApp()
    if '--weapons' in sys.argv:
        app.after(700, app.open_weapons)
    if '--character' in sys.argv:
        app.after(700, lambda: app.notebook.select(app.character_panel))
    app.mainloop()
