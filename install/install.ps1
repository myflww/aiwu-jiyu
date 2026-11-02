param([switch]$StartApp)
$ErrorActionPreference = "Stop"
$source = Join-Path $PSScriptRoot "爱屋及鱼.exe"
if (-not (Test-Path -LiteralPath $source)) { throw "请先解压完整安装包，再运行桌面安装。" }
$target = Join-Path $env:LOCALAPPDATA "Programs\AiwuJiyu"
New-Item -ItemType Directory -Path $target -Force | Out-Null
foreach ($name in @("爱屋及鱼.exe", "app-icon.ico", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md")) {
    $file = Join-Path $PSScriptRoot $name
    if (Test-Path -LiteralPath $file) {
        if ([IO.Path]::GetFullPath($file) -ne [IO.Path]::GetFullPath((Join-Path $target $name))) {
            Copy-Item -LiteralPath $file -Destination $target -Force
        }
    }
}
$notices = Join-Path $PSScriptRoot "THIRD-PARTY-LICENSES"
if ((Test-Path -LiteralPath $notices) -and $PSScriptRoot -ne $target) {
    Copy-Item -LiteralPath $notices -Destination $target -Recurse -Force
}
$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath("DesktopDirectory")
$shortcut = $shell.CreateShortcut((Join-Path $desktop "爱屋及鱼.lnk"))
$shortcut.TargetPath = Join-Path $target "爱屋及鱼.exe"
$shortcut.WorkingDirectory = $target
$shortcut.IconLocation = (Join-Path $target "app-icon.ico") + ",0"
$shortcut.Description = "爱屋及鱼 · 本地陪伴控制台"
$shortcut.Save()
Write-Host "已经安装好啦，桌面上的小鱼在等你。"
if ($StartApp) { Start-Process -FilePath (Join-Path $target "爱屋及鱼.exe") -WorkingDirectory $target }
