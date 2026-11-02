param(
    [string]$PythonPath = $env:DAFEIYU_BUILD_PYTHON,
    [switch]$SkipDependencyInstall,
    [string]$Version = "",
    [string]$IndexUrl = "https://pypi.org/simple",
    [string]$IsccPath = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
if (-not $Version) { $Version = (Get-Content -LiteralPath (Join-Path $root "VERSION") -Raw).Trim() }
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Version must be x.y.z" }
function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}
Write-Host "[1/5] Prepare Windows x64 build environment"
if (-not $IsccPath) {
    $isccCommand = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($isccCommand) { $IsccPath = $isccCommand.Source }
    if (-not $IsccPath) {
        $isccCandidates = @(
            (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
            (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
            (Join-Path $root "build\vendor\InnoSetup\ISCC.exe")
        )
        $IsccPath = $isccCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if (-not $IsccPath) {
            $IsccPath = Get-ChildItem -LiteralPath (Join-Path $root "build\vendor") -Filter "ISCC.exe" -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
        }
    }
}
if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath)) {
    throw "Inno Setup 6 is required for the update-compatible Setup package. Pass -IsccPath <ISCC.exe>."
}
if (-not $PythonPath) {
    $PythonPath = Join-Path $root "build\.venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $PythonPath)) {
        Invoke-Checked "py" @("-3.13", "-m", "venv", (Join-Path $root "build\.venv"))
    }
}
if (-not $SkipDependencyInstall) {
    Invoke-Checked $PythonPath @("-m", "pip", "install", "--index-url", $IndexUrl, "-r", "requirements-build.txt")
}
Invoke-Checked $PythonPath @("-c", "import platform,struct; assert platform.system()=='Windows' and struct.calcsize('P')==8, 'Windows x64 Python required'")
Invoke-Checked $PythonPath @("-c", "import importlib.metadata as m,pathlib; rows=[r.strip() for r in pathlib.Path('requirements-build.txt').read_text().splitlines() if r.strip() and not r.startswith('#')]; bad=[r for r in rows if m.version(r.split('==')[0])!=r.split('==')[1]]; assert not bad, 'Dependency versions differ: '+str(bad)")
Write-Host "[2/5] Collect dependency notices"
Invoke-Checked $PythonPath @("build\collect_licenses.py")
$icon = Join-Path $root "assets\ui\app-icon.ico"
if (-not (Test-Path -LiteralPath $icon)) { throw "Missing assets/ui/app-icon.ico" }
Write-Host "[3/5] Freeze the desktop app"
$env:AIWU_APP_VERSION = $Version
Invoke-Checked $PythonPath @("-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", "dist", "--workpath", "build\aiwu-jiyu", "build\dafeiyu.spec")
Write-Host "[4/5] Assemble portable desktop download"
$source = Join-Path $root "dist\AiwuJiyu.exe"
if (-not (Test-Path -LiteralPath $source)) { throw "Build produced no AiwuJiyu.exe" }
$bundle = Join-Path $root "dist\release\AiwuJiyu-$Version-Windows-x64"
New-Item -ItemType Directory -Path $bundle -Force | Out-Null
Copy-Item -LiteralPath $source -Destination (Join-Path $bundle "爱屋及鱼.exe") -Force
foreach ($name in @("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "README.md", "CONTRIBUTING.md")) {
    Copy-Item -LiteralPath (Join-Path $root $name) -Destination $bundle -Force
}
Invoke-Checked $PythonPath @("-c", "import shutil,sys; shutil.copytree(sys.argv[1],sys.argv[2],dirs_exist_ok=True)", (Join-Path $root "docs"), (Join-Path $bundle "docs"))
$readmeAssets = Join-Path $bundle "assets\ui"
New-Item -ItemType Directory -Path $readmeAssets -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $root "assets\ui\dafeiyu-daily.webp") -Destination $readmeAssets -Force
Invoke-Checked $PythonPath @("-c", "import shutil,sys; shutil.copytree(sys.argv[1],sys.argv[2],dirs_exist_ok=True)", (Join-Path $root "build\collected-licenses"), (Join-Path $bundle "THIRD-PARTY-LICENSES"))
Copy-Item -LiteralPath (Join-Path $root "install\install.ps1") -Destination $bundle -Force
Copy-Item -LiteralPath (Join-Path $root "install\安装到桌面.cmd") -Destination $bundle -Force
Copy-Item -LiteralPath $icon -Destination (Join-Path $bundle "app-icon.ico") -Force
$archive = Join-Path $root "dist\release\AiwuJiyu-$Version-Windows-x64.zip"
Compress-Archive -LiteralPath $bundle -DestinationPath $archive -Force
$direct = Join-Path $root "dist\release\爱屋及鱼.exe"
Copy-Item -LiteralPath $source -Destination $direct -Force
Invoke-Checked $IsccPath @("/DAppVersion=$Version", "/DBundleDir=$bundle", "/DReleaseDir=$(Join-Path $root 'dist\release')", "build\installer.iss")
Write-Host "[5/5] Write release checksums"
$artifacts = @($archive, $direct)
$setup = Join-Path $root "dist\release\AiwuJiyu-Setup-$Version-Windows-x64.exe"
if (-not (Test-Path -LiteralPath $setup)) { throw "Build produced no update-compatible Setup installer" }
$artifacts += $setup
$sums = foreach ($artifact in $artifacts) {
    $hash = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $(Split-Path -Leaf $artifact)"
}
$sums | Set-Content -LiteralPath (Join-Path $root "dist\release\SHA256SUMS.txt") -Encoding utf8
Write-Host "Portable app: $direct"
Write-Host "Desktop installation bundle: $archive"
Write-Host "Update-compatible setup: $setup"
