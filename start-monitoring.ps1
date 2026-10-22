# Talk2Data Prometheus & Grafana Monitoring Launcher
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "       TALK2DATA PROMETHEUS & GRAFANA MONITORING          " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# Check if Docker is running
docker ps 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[!] Docker Desktop is not running." -ForegroundColor Yellow
    Write-Host "Please start Docker Desktop on your machine, then re-run this script." -ForegroundColor White
    exit 1
}

Write-Host "`nStarting Prometheus and Grafana containers..." -ForegroundColor Green
docker compose -f docker-compose.monitoring.yml up -d

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n✓ Prometheus:  http://localhost:9090" -ForegroundColor Green
    Write-Host "✓ Grafana:     http://localhost:3000 (User: admin / Pass: admin)" -ForegroundColor Green
    Write-Host "✓ Metrics URL: http://localhost:8000/metrics (from ChatToData API)" -ForegroundColor Green
    Write-Host "`nOpening Grafana in your web browser..." -ForegroundColor Cyan
    Start-Process "http://localhost:3000"
} else {
    Write-Host "`n[Error] Failed to start containers. Check docker logs." -ForegroundColor Red
}
