# Downloads the local AI models Lyra uses (through Ollama). Run by the installer, and from the
# Start menu entry "Lyra - Download AI models". Safe to run again: it only fetches what's missing.

$ErrorActionPreference = "Continue"
$Host.UI.RawUI.WindowTitle = "Lyra - AI models"

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }

Say "`n  Lyra - local AI models`n" Cyan

$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $ollama) {
    $default = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $default) { $ollama = $default }
}
if (-not $ollama) {
    Say "Ollama is not installed. Lyra needs it to run its AI models on this PC." Yellow
    Say "Opening https://ollama.com/download - install it, then run 'Lyra - Download AI models' from the Start menu."
    Start-Process "https://ollama.com/download"
    Read-Host "`nPress Enter to close"
    exit 1
}

# make sure the Ollama server is up (the desktop app normally starts it with Windows)
try { Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 3 | Out-Null }
catch {
    Say "Starting Ollama..."
    Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 4
}

$models = @(
    @{ name = "qwen2.5:1.5b";      why = "chat (about 1 GB)" },
    @{ name = "nomic-embed-text";  why = "memory search (about 270 MB)" },
    @{ name = "gemma3:4b";         why = "Roman Urdu translation (about 3.3 GB)" }
)
$installed = (& $ollama list) -join "`n"
foreach ($m in $models) {
    if ($installed -match [regex]::Escape($m.name)) {
        Say ("  [ok] {0} - already installed" -f $m.name) Green
        continue
    }
    Say ("`n  Downloading {0} - {1}" -f $m.name, $m.why) Cyan
    & $ollama pull $m.name
    if ($LASTEXITCODE -ne 0) { Say ("  Could not download {0}. Check your internet connection and run this again." -f $m.name) Red }
}

Say "`nAll set. Say 'Hey Lyra' to start talking." Green
Start-Sleep -Seconds 3
