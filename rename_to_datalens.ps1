# =============================================================================
# rename_to_datalens.ps1
# Run this ONCE after closing the DataLens app and VSCode.
# Renames the project folder from snowflake_profiler -> datalens
# and updates the virtual-environment activation path inside the venv.
# =============================================================================

$src  = "c:\Users\TiagoOrtega\snowflake_profiler"
$dst  = "c:\Users\TiagoOrtega\datalens"

Write-Host ""
Write-Host "DataLens — Folder Rename" -ForegroundColor Cyan
Write-Host "========================" -ForegroundColor Cyan
Write-Host ""

# ── Safety checks ─────────────────────────────────────────────────────────────

if (-not (Test-Path $src)) {
    Write-Host "Source folder not found: $src" -ForegroundColor Red
    Write-Host "Nothing to do — folder may already be renamed." -ForegroundColor Yellow
    exit 0
}

if (Test-Path $dst) {
    Write-Host "Destination already exists: $dst" -ForegroundColor Red
    Write-Host "Remove or rename $dst first." -ForegroundColor Yellow
    exit 1
}

# Check that the Flask app is not running (port 5000)
$port5000 = netstat -ano 2>$null | Select-String ":5000"
if ($port5000) {
    Write-Host "WARNING: Something is listening on port 5000." -ForegroundColor Yellow
    Write-Host "Stop the DataLens app before renaming (Ctrl+C in its terminal)." -ForegroundColor Yellow
    $ans = Read-Host "Continue anyway? (y/N)"
    if ($ans -ne 'y' -and $ans -ne 'Y') { exit 1 }
}

# ── Rename ────────────────────────────────────────────────────────────────────

try {
    Rename-Item -Path $src -NewName "datalens" -ErrorAction Stop
    Write-Host "Renamed: $src" -ForegroundColor Green
    Write-Host "     To: $dst" -ForegroundColor Green
} catch {
    Write-Host "Rename failed: $_" -ForegroundColor Red
    Write-Host "Make sure VSCode and the app are fully closed, then re-run." -ForegroundColor Yellow
    exit 1
}

# ── Fix venv activation scripts (they contain the old absolute path) ──────────

$activateFiles = @(
    "$dst\.venv\Scripts\activate",
    "$dst\.venv\Scripts\activate.bat",
    "$dst\.venv\Scripts\Activate.ps1"
)

foreach ($f in $activateFiles) {
    if (Test-Path $f) {
        $content = Get-Content $f -Raw
        if ($content -match [regex]::Escape($src)) {
            $updated = $content -replace [regex]::Escape($src), $dst
            Set-Content $f $updated -Encoding UTF8
            Write-Host "Updated: $f" -ForegroundColor DarkGray
        }
    }
}

Write-Host ""
Write-Host "All done!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Open VSCode from the new folder: code $dst" -ForegroundColor White
Write-Host "  2. Re-activate the venv:  .\.venv\Scripts\Activate.ps1" -ForegroundColor White
Write-Host "  3. Start the app:  python app.py" -ForegroundColor White
Write-Host "  4. Open http://localhost:5000" -ForegroundColor White
Write-Host ""
