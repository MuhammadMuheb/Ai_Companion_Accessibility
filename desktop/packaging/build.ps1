# Builds Lyra-Setup-<version>.exe:  powershell -ExecutionPolicy Bypass -File desktop\packaging\build.ps1
# Needs: Python with requirements-FULL.txt + pyinstaller, Inno Setup 6, and the speech models in
# packaging\models (copied from the Hugging Face cache on first build). The default neural voice
# (Amy) is downloaded once into packaging\models\voices so Lyra speaks offline straight after install.

$ErrorActionPreference = "Stop"
$pkg = $PSScriptRoot
$desktop = Split-Path $pkg -Parent
Set-Location $desktop

# 1. speech models to bundle
$hub = Join-Path $env:USERPROFILE ".cache\huggingface\hub"
foreach ($size in @("tiny", "base")) {
    $dest = Join-Path $pkg "models\whisper-$size"
    if (-not (Test-Path "$dest\model.bin")) {
        $snap = Get-ChildItem "$hub\models--Systran--faster-whisper-$size\snapshots" -Directory | Select-Object -First 1
        if (-not $snap) { throw "Whisper '$size' not in the cache - run Lyra from source once so it downloads." }
        New-Item -ItemType Directory -Force $dest | Out-Null
        Copy-Item "$($snap.FullName)\*" $dest -Force
    }
}
$vp = Join-Path $pkg "models\voxceleb_resnet34_LM.onnx"
if (-not (Test-Path $vp)) {
    $f = Get-ChildItem "$hub\models--Wespeaker--wespeaker-voxceleb-resnet34-LM" -Recurse -Filter voxceleb_resnet34_LM.onnx | Select-Object -First 1
    Copy-Item $f.FullName $vp
}

# default speaking voice (Piper "Amy", ~63 MB) — every other voice downloads from Settings > Voice
$voices = Join-Path $pkg "models\voices"
$amy = "en_US-amy-medium"
if (-not (Test-Path "$voices\$amy.onnx")) {
    New-Item -ItemType Directory -Force $voices | Out-Null
    $base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/$amy"
    Invoke-WebRequest "$base.onnx.json" -OutFile "$voices\$amy.onnx.json" -UseBasicParsing
    Invoke-WebRequest "$base.onnx" -OutFile "$voices\$amy.onnx" -UseBasicParsing
}

# 2. icons, then the frozen app
python "$pkg\make_icon.py"
# load onnxruntime before WinRT inside PyInstaller's import scan (see app/__init__.py)
$env:PYTHONPATH = Join-Path $pkg "buildhooks"
python -m PyInstaller "$pkg\lyra.spec" --noconfirm --clean --distpath "$pkg\dist" --workpath "$pkg\build"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# 3. the installer
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
& $iscc "$pkg\Lyra.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

Get-ChildItem "$pkg\Output\*.exe" | ForEach-Object {
    $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash
    "{0}  {1:N0} MB  SHA256 {2}" -f $_.Name, ($_.Length / 1MB), $hash
}
