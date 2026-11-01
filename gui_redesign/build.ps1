# 打包玄锋控制台为 Windows 可执行文件（PyInstaller）。
# 用法：  powershell -ExecutionPolicy Bypass -File gui_redesign\build.ps1 [-OneDir] [-NoPoster]
#   -OneDir    输出文件夹形式（启动更快，杀软误报更少）；默认单文件 exe
#   -NoPoster  不打包 picture.png 侧栏海报（开源分发时建议使用）
param(
    [switch]$OneDir,
    [switch]$NoPoster
)
$ErrorActionPreference = 'Stop'

$Here = $PSScriptRoot
$Root = Split-Path $Here -Parent
$Name = 'XuanfengConsole'
$Sep = [IO.Path]::PathSeparator  # Windows 下为 ';'

python -c "import sys; assert sys.maxsize > 2**32, '需要 64 位 Python'"
if ($LASTEXITCODE) { throw '需要 64 位 Python。' }
python -m PyInstaller --version *> $null
if ($LASTEXITCODE) {
    Write-Host '未找到 PyInstaller，正在安装…'
    python -m pip install --upgrade pyinstaller pillow
    if ($LASTEXITCODE) { throw 'PyInstaller 安装失败。' }
}

$data = @(
    "$Here\assets${Sep}assets",
    "$Root\character-basic-attribute-metadata.json${Sep}."
)
$poster = Join-Path $Here 'picture.png'
if (-not $NoPoster -and (Test-Path $poster)) { $data += "$poster${Sep}." }

$pyiArgs = @(
    '--noconfirm', '--clean', '--windowed',
    $(if ($OneDir) { '--onedir' } else { '--onefile' }),
    '--name', $Name,
    '--icon', "$Here\assets\icon.ico",
    '--paths', $Root,
    '--distpath', "$Here\dist",
    '--workpath', "$Here\build",
    '--specpath', "$Here\build"
)
foreach ($d in $data) { $pyiArgs += @('--add-data', $d) }
$pyiArgs += "$Here\app.py"

Push-Location $Root
try {
    python -m PyInstaller @pyiArgs
    if ($LASTEXITCODE) { throw "PyInstaller 失败（退出码 $LASTEXITCODE）。" }
} finally {
    Pop-Location
}

$out = if ($OneDir) { "$Here\dist\$Name\$Name.exe" } else { "$Here\dist\$Name.exe" }
Write-Host "`n完成：$out"
