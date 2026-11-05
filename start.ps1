# 闲鱼智能监控系统 - Windows 本地启动脚本
# 用法：在 PowerShell 中执行  .\start.ps1
# 作用：使用项目自带的 .venv 启动 FastAPI 服务（前端产物需已构建到根目录 dist\）

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Error "未找到虚拟环境 $python。请先执行：python -m venv .venv; .venv\Scripts\python.exe -m pip install -r requirements.txt"
}

if (-not (Test-Path (Join-Path $PSScriptRoot 'dist\index.html'))) {
    Write-Warning "未找到 dist\index.html，Web 界面会提示“前端构建产物不存在”。请先执行：cd web-ui; npm install; npm run build"
}

if (-not (Test-Path (Join-Path $PSScriptRoot '.env'))) {
    Write-Warning "未找到 .env，将使用默认配置（admin/admin123，端口 8000）。"
}

$port = 8000
if (Test-Path (Join-Path $PSScriptRoot '.env')) {
    $line = Select-String -Path (Join-Path $PSScriptRoot '.env') -Pattern '^SERVER_PORT=(\d+)' | Select-Object -First 1
    if ($line) { $port = [int]$line.Matches[0].Groups[1].Value }
}

Write-Host "启动闲鱼智能监控系统..." -ForegroundColor Green
Write-Host "Web UI:   http://127.0.0.1:$port" -ForegroundColor Green
Write-Host "API 文档: http://127.0.0.1:$port/docs" -ForegroundColor Green

& $python -m src.app