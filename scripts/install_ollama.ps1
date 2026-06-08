# =============================================================================
# install_ollama.ps1  --  Install Ollama and pull the default LLM model
# Usage: powershell -ExecutionPolicy Bypass -File scripts\install_ollama.ps1
# Optionally pass a model name: -Model llama3.2:1b
# =============================================================================

param(
    [string]$Model = "llama3.2"
)

Write-Host ""
Write-Host "  Snowflake Profiler - Ollama LLM Setup" -ForegroundColor Cyan
Write-Host "  ======================================" -ForegroundColor Cyan
Write-Host ""

# --- 1. Check / install Ollama ---
$ollamaExe = (Get-Command ollama -ErrorAction SilentlyContinue)

if (-not $ollamaExe) {
    Write-Host "  Ollama not found -- installing via winget..." -ForegroundColor Yellow
    winget install Ollama.Ollama --accept-package-agreements --accept-source-agreements --silent

    # Refresh PATH so ollama is visible in this session
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")

    $ollamaExe = (Get-Command ollama -ErrorAction SilentlyContinue)
    if (-not $ollamaExe) {
        Write-Host ""
        Write-Host "  ERROR: Ollama installation failed." -ForegroundColor Red
        Write-Host "  Download manually from https://ollama.ai and re-run this script." -ForegroundColor Red
        exit 1
    }
    Write-Host "  Ollama installed successfully." -ForegroundColor Green
} else {
    Write-Host "  Ollama is already installed at: $($ollamaExe.Source)" -ForegroundColor Green
}

# --- 2. Start Ollama server in the background ---
Write-Host ""
Write-Host "  Starting Ollama server..." -ForegroundColor Cyan

$running = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "  Ollama server already running (PID $($running.Id))." -ForegroundColor Green
} else {
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 3
    Write-Host "  Ollama server started." -ForegroundColor Green
}

# --- 3. Pull the model ---
Write-Host ""
Write-Host "  Pulling model: $Model" -ForegroundColor Cyan
Write-Host "  NOTE: first pull downloads the model and may take several minutes." -ForegroundColor DarkGray
Write-Host ""

ollama pull $Model

# --- 4. Verify ---
Write-Host ""
Write-Host "  Available models:" -ForegroundColor Cyan
ollama list

Write-Host ""
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host "  Ollama API : http://localhost:11434" -ForegroundColor Cyan
Write-Host "  Model      : $Model" -ForegroundColor Cyan
Write-Host ""
Write-Host "  In the Profiler web UI:" -ForegroundColor Yellow
Write-Host "    Sources page -> AI Insights section -> select Ollama" -ForegroundColor Yellow
Write-Host "    Set Model name to: $Model" -ForegroundColor Yellow
Write-Host ""

# --- Recommended models ---
Write-Host "  Recommended models by speed / quality:" -ForegroundColor DarkGray
Write-Host "    llama3.2:1b   -- fastest  (~700 MB)   good for quick insights" -ForegroundColor DarkGray
Write-Host "    llama3.2      -- balanced (~2 GB)     best default choice" -ForegroundColor DarkGray
Write-Host "    phi3.5        -- compact  (~2 GB)     strong reasoning" -ForegroundColor DarkGray
Write-Host "    mistral       -- quality  (~4 GB)     great for analytics" -ForegroundColor DarkGray
Write-Host ""
