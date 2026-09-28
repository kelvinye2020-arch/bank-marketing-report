# XHS Bank Marketing Dashboard - One Click Update
Write-Host "========================================"
Write-Host "  XHS Bank Marketing Dashboard Update"
Write-Host "========================================"
Write-Host ""

# 1. Check port 18060
$portInUse = netstat -ano | Select-String ":18060" | Select-String "LISTENING"
if ($portInUse) {
    Write-Host "[INFO] Port 18060 is occupied, MCP is already running"
} else {
    Write-Host "[1/3] Starting MCP (headed mode, Chromium will pop up)..."
    $mcpDir = "D:\AI agent\xiaohongshu-mcp-windows-amd64"
    $mcpExe = "$mcpDir\xiaohongshu-mcp-windows-amd64.exe"
    $chromiumBin = "D:\AI agent\chromium\chrome-win\chrome.exe"
    
    Start-Process -FilePath $mcpExe -ArgumentList "-headless=false","-bin","$chromiumBin" -WorkingDirectory $mcpDir
    
    Write-Host "       Waiting 15s for MCP to start..."
    Start-Sleep -Seconds 15
}

# 2. Verify health
Write-Host ""
Write-Host "[2/3] Verifying MCP health..."
try {
    $health = Invoke-WebRequest -Uri "http://localhost:18060/health" -UseBasicParsing -TimeoutSec 5
    Write-Host "       $($health.Content)"
} catch {
    Write-Host "       WARNING: health check failed: $_"
}

# 3. Run update_report.py
Write-Host ""
Write-Host "[3/3] Running update pipeline (search + report + push)..."
Write-Host ""
$projectDir = "C:\Users\kelvinyye\WorkBuddy\20260313150001"
Set-Location $projectDir
python update_report.py

Write-Host ""
Write-Host "========================================"
Write-Host "  All done! Press any key to close"
Write-Host "========================================"
$null = Read-Host
