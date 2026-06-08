# =============================================================================
# start_llm.ps1  --  Start (or verify) the Ollama LLM service
# Usage: powershell -ExecutionPolicy Bypass -File scripts\start_llm.ps1
# Optionally pass a model name: -Model llama3.2:1b
# =============================================================================

param(
    [string]$Model = "llama3.2"
)

# --- Check if ollama is installed ---
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "Ollama is not installed." -ForegroundColor Red
    Write-Host "Run scripts\install_ollama.ps1 first." -ForegroundColor Yellow
    exit 1
}

# --- Start server if not already running ---
$running = Get-Process -Name "ollama" -ErrorAction SilentlyContinue

if ($running) {
    Write-Host "Ollama already running (PID $($running.Id))  ->  http://localhost:11434" -ForegroundColor Green
} else {
    Write-Host "Starting Ollama server..." -ForegroundColor Cyan
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 2
    Write-Host "Ollama server started  ->  http://localhost:11434" -ForegroundColor Green
}

# --- Health check and model verification ---
try {
    $resp   = Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 5
    $models = $resp.models | ForEach-Object { $_.name }

    if ($models) {
        Write-Host "Available models: $($models -join ', ')" -ForegroundColor Cyan
    } else {
        Write-Host "No models pulled yet." -ForegroundColor Yellow
    }

    # Check if the requested model is available; pull if missing
    $modelShort = $Model.Split(':')[0]
    $found = $models | Where-Object { $_ -like "$modelShort*" }

    if (-not $found) {
        Write-Host "Model '$Model' not found locally -- pulling..." -ForegroundColor Yellow
        ollama pull $Model
    } else {
        Write-Host "Model '$Model' is ready." -ForegroundColor Green
    }
} catch {
    Write-Host "WARNING: Could not reach Ollama API. Server may still be starting up." -ForegroundColor Yellow
    Write-Host "Wait a few seconds and try again, or check that port 11434 is free." -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "LLM service is ready. Reload the Profiler web UI." -ForegroundColor Green
