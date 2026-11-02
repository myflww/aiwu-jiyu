$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "[1/4] 检查 uv.exe"
$uv = Join-Path $root "build\vendor\uv.exe"
if (-not (Test-Path $uv)) {
    Write-Host "缺少 build\vendor\uv.exe；请先下载 uv-x86_64-pc-windows-msvc.zip 并解压到这里"
    exit 1
}

Write-Host "[2/4] 准备构建环境"
$venv = Join-Path $root "build\.venv"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    $python = if ($env:DAFEIYU_BUILD_PYTHON) { $env:DAFEIYU_BUILD_PYTHON } else { "python" }
    & $python -m venv $venv
}
$py = Join-Path $venv "Scripts\python.exe"
& $py -m pip install --upgrade pip -q -i https://mirrors.cernet.edu.cn/pypi/web/simple
& $py -m pip install pywebview pyinstaller qrcode pillow -i https://mirrors.cernet.edu.cn/pypi/web/simple

Write-Host "[3/4] PyInstaller 打包"
& $py -m PyInstaller --noconfirm --clean "build\dafeiyu.spec"

Write-Host "[4/4] 输出"
$source = Join-Path $root "dist\AiwuJiyu.exe"
$target = Join-Path $root "dist\爱屋及鱼.exe"
if (Test-Path $source) {
    Copy-Item $source $target -Force
    $hash = (Get-FileHash $target -Algorithm SHA256).Hash
    Write-Host "输出: $target"
    Write-Host "SHA256: $hash"
} else {
    Write-Host "打包失败：没有找到 dist\AiwuJiyu.exe"
    exit 1
}
