# Lyra installer for Windows - right-click > "Run with PowerShell" (no administrator rights needed).
# Installs the Python packages and the local AI models, then adds Lyra to the Start menu and to
# Windows sign-in. Safe to run again: it only adds what is missing.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "`n$text" -ForegroundColor Red; Read-Host "Press Enter to close"; exit 1 }

Step "Checking Python"
$python = $null
foreach ($candidate in @("py -3.13", "py -3.12", "py -3.11", "py -3.10", "python")) {
    $exe, $pyArgs = $candidate.Split(" ")
    $pyArgs = @($pyArgs | Where-Object { $_ })
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $version = & $exe @pyArgs -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and [version]$version -ge [version]"3.10" -and [version]$version -le [version]"3.13") {
        $python = $candidate; break
    }
}
if (-not $python) { Fail "Python 3.10-3.13 not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and run this again." }
$exe, $pyArgs = $python.Split(" ")
$pyArgs = @($pyArgs | Where-Object { $_ })
Write-Host "Using $python ($version)"

Step "Installing Python packages (first time takes a few minutes)"
& $exe @pyArgs -m pip install --disable-pip-version-check -q -r requirements-FULL.txt
if ($LASTEXITCODE -ne 0) { Fail "Package installation failed - see the messages above." }

Step "Checking Ollama (the local AI engine)"
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Fail "Ollama is not installed. Get it from https://ollama.com/download, then run this again."
}
$models = (& ollama list) -join "`n"
foreach ($model in @("qwen2.5:1.5b", "nomic-embed-text", "gemma3:4b")) {
    if ($models -match [regex]::Escape($model)) { Write-Host "$model is already installed" }
    else { Write-Host "Downloading $model ..."; & ollama pull $model }
}

Step "Adding Lyra to the Start menu and to Windows sign-in"
& $exe @pyArgs -c "from app.tray import set_start_menu, set_autostart; print(set_start_menu(True)); print(set_autostart(True))"

Step "Health check"
& $exe @pyArgs main.py --check

Write-Host "`nDone! Starting Lyra in the background - say 'Hello Lyra'." -ForegroundColor Green
$pythonw = (& $exe @pyArgs -c "import sys, pathlib; print(pathlib.Path(sys.executable).with_name('pythonw.exe'))").Trim()
Start-Process -FilePath $pythonw -ArgumentList "`"$PSScriptRoot\lyra.pyw`"" -WorkingDirectory $PSScriptRoot
Read-Host "Press Enter to close"
