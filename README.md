<p align="center"><img src="gui_redesign/assets/icon.svg" width="96" alt="玄锋控制台"></p>

# 玄锋控制台

《影之刃零》（Shadow Blade Zero，PC / Steam）单机存档辅助工具的桌面界面：查看并修改**背包物品数量**、**武器等级与攻击倍率**、**人物属性**。

- 只使用 Windows 原生内存读写 API：不注入 DLL、不 Hook、不调用游戏函数、不设置断点。
- 每次写入前重新定位并核对进程路径、模块、场景与对象身份；写入后立即读回比对。
- 任何一步失败都会作废当前数据，必须重新读取后才能再次写入，避免基于过期地址写内存。

> ⚠️ 仅供单机游戏学习与个人使用。修改内存可能导致存档异常，请先备份存档。本项目与游戏开发商、发行商无任何关联。

## 功能

| 页面 | 可读取 | 可修改 |
|------|--------|--------|
| 背包总览 | 物品名称、数量、名称键、标识 | 数量（0 – 2147483647） |
| 武器配置 | 主武器 / 杀气武器、等级、当前等级攻击倍率 | 等级、攻击倍率 |
| 人物属性 | 当前值、基础值（默认显示常用属性） | 当前值、基础值 |

界面细节：

- 所有读写在后台线程执行，界面不卡顿；侧栏显示连接状态与游戏 PID。
- 搜索、点击表头排序、“隐藏数量为 0” / “显示全部属性”筛选。
- 输入实时校验并预览 `旧值 → 新值`；整数字段提供 −10 / −1 / +1 / +10 / +100 快捷调整。
- 每个页面可撤销上一次修改（同样经过核对和读回验证）。
- 写入日志追加到 `inventory-changes.jsonl` / `weapon-changes.jsonl`。

### 快捷键

| 按键 | 作用 |
|------|------|
| `F5` | 刷新当前页面 |
| `Ctrl+1` / `Ctrl+2` / `Ctrl+3` | 切换到背包 / 武器 / 人物属性 |
| `Ctrl+F` | 聚焦搜索框，`Esc` 清空 |
| 双击行 / `Enter` | 编辑选中行的数值 |
| `Enter` | 应用修改 |
| `↑` / `↓`（`Shift` ×10） | 在输入框中增减整数值 |
| `Ctrl+Z` | 撤销上一次修改（焦点在输入框时不触发） |

## 使用

1. 启动游戏并**进入存档**（主菜单中无法读取）。
2. 运行本工具，默认自动读取背包；切换页面时首次自动读取。
3. 选择一行 → 输入新数值 → `Enter` 或点击“应用修改”。

若游戏以管理员权限运行，本工具也需要以管理员身份运行。

### 从源码运行

需要 Windows、**64 位** Python 3.10+（自带 Tkinter）。Pillow 可选，只用于显示侧栏海报。

```powershell
pip install pillow          # 可选
python gui_redesign/app.py  # 在仓库根目录执行
```

也可以直接运行 `gui_redesign\启动.ps1`。

### 打包为 exe

```powershell
powershell -ExecutionPolicy Bypass -File gui_redesign\build.ps1             # 单文件 exe
powershell -ExecutionPolicy Bypass -File gui_redesign\build.ps1 -OneDir     # 文件夹形式
powershell -ExecutionPolicy Bypass -File gui_redesign\build.ps1 -NoPoster   # 不包含侧栏海报
```

脚本会在缺少 PyInstaller 时自动安装，产物输出到 `gui_redesign\dist\XuanfengConsole.exe`。打包版本的修改日志写在 exe 同目录。

> 未签名的内存读写工具容易被杀毒软件误报；`-OneDir` 模式通常误报更少。

## 目录结构

```
.
├── inventory_gui_backend.py   # 进程发现、附加、背包读写
├── weapon_backend.py          # 武器读写
├── character_probe.py         # 人物属性读写
├── character_gui.py           # 旧版界面的属性页（本界面复用其属性中文名表）
├── inventory_gui.py / weapon_gui.py   # 旧版 Tkinter 界面
├── test_*.py                  # 后端单元测试：python -m pytest
├── windows_memory.py          # Win32 内存 API 封装
├── character-basic-attribute-metadata.json
└── gui_redesign/
    ├── app.py                 # 本界面（唯一入口）
    ├── build.ps1              # PyInstaller 打包脚本
    ├── assets/icon.svg        # 图标源文件（icon.png / icon.ico 由其导出）
    ├── picture.png            # 侧栏海报（可选，不随仓库分发；缺失时显示纯色侧栏）
    └── designmd.md            # 视觉规范
```

界面只负责交互，所有地址解析、身份核对和读回验证都在后端模块中完成。

## 兼容性

后端使用的偏移与类布局对应特定游戏版本。游戏更新后，签名或身份校验会失败并拒绝读写（界面显示错误信息），不会盲目写入；此时需要重新分析并更新后端偏移。

## 设计

视觉遵循 [designmd.md](gui_redesign/designmd.md)：Onyx `#171721` 底色、Graphite `#1e1e2a` 卡片、Ivory `#ededf3` 文字，Cobalt `#5266eb` 只用于主操作、当前页面和连接状态；胶囊按钮、无阴影、无粗体标题。

图标 `gui_redesign/assets/icon.svg`（斗笠与斜斩之刃）为本项目原创。修改后可在浏览器中以 512×512 渲染为 PNG，再用 Pillow 导出 `icon.png`（256）与 `icon.ico`（16–256 多尺寸）。

## 许可证

[MIT](LICENSE)

## 素材说明

- `gui_redesign/picture.png` 是游戏官方宣传图，版权归原权利人所有，仅作个人界面装饰；公开分发时请删除或替换为自有授权图片，并使用 `build.ps1 -NoPoster` 打包。
