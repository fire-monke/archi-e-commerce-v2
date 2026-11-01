# Démonstration de bout en bout : port IN, événement publié, événement consommé,
# panne simulée + reprise, logs JSON corrélés par traceId.
# Prérequis : serveur lancé avec PAYMENT_ENABLE_SIMULATION=true et PAYMENT_INTERNAL_TOKEN (voir README).
param(
    [string]$BaseUrl = 'http://127.0.0.1:8000',
    [string]$Token = 'local-secret'
)
$ErrorActionPreference = 'Stop'

function New-TraceId([string]$prefix) {
    return "$prefix-" + [guid]::NewGuid().ToString('N').Substring(0, 8)
}

function Send-OrderCreated([string]$traceId, [string]$orderId, [double]$amount) {
    $event = @{
        id         = "evt-$traceId"
        type       = 'OrderCreated'
        occurredAt = (Get-Date).ToUniversalTime().ToString('o')
        traceId    = $traceId
        payload    = @{ orderId = $orderId; amount = $amount; currency = 'EUR' }
    } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri "$BaseUrl/events/order-created" -Method Post -ContentType 'application/json' -Body $event | Out-Null
    Start-Sleep -Milliseconds 500  # le paiement est traité en tâche de fond après le 202
}

function Set-GatewayScenario([string]$scenario) {
    $body = @{ scenario = $scenario } | ConvertTo-Json -Compress
    Invoke-RestMethod -Uri "$BaseUrl/internal/gateway-scenario" -Method Post -Headers @{ 'X-Internal-Token' = $Token } -ContentType 'application/json' -Body $body | Out-Null
}

function Get-TraceLogs([string]$traceId) {
    # Variable intermédiaire : sous PowerShell 5.1, return déroule ainsi le tableau JSON élément par élément.
    $logs = Invoke-RestMethod -Uri "$BaseUrl/logs?traceId=$traceId" -Method Get
    return $logs
}

function Show-TraceLogs([string]$traceId) {
    Write-Host "GET /logs?traceId=$traceId" -ForegroundColor Yellow
    foreach ($entry in (Get-TraceLogs $traceId)) {
        Write-Host ($entry | ConvertTo-Json -Compress -Depth 6)
    }
    Write-Host ''
}

Set-GatewayScenario 'normal'

Write-Host '=== 1. Flux nominal : OrderCreated -> PaymentConfirmed ===' -ForegroundColor Cyan
$trace1 = New-TraceId 'demo-ok'
Send-OrderCreated $trace1 'ORD-DEMO-OK' 15.00
Show-TraceLogs $trace1

Write-Host '=== 2. Panne du prestataire : timeout -> PENDING -> reprise -> PaymentConfirmed ===' -ForegroundColor Cyan
$trace2 = New-TraceId 'demo-outage'
Set-GatewayScenario 'timeout'
Send-OrderCreated $trace2 'ORD-DEMO-OUTAGE' 24.00
$paymentId = (Get-TraceLogs $trace2 | Where-Object { $_.paymentId } | Select-Object -First 1).paymentId
Write-Host "Paiement $paymentId resté PENDING ; le service répond toujours." -ForegroundColor DarkYellow
Set-GatewayScenario 'normal'
Invoke-RestMethod -Uri "$BaseUrl/internal/payments/$paymentId/retry" -Method Post -Headers @{ 'X-Internal-Token' = $Token; 'X-Trace-Id' = $trace2 } | Out-Null
Show-TraceLogs $trace2

Write-Host '=== 3. Appel REST direct (port IN), traceId généré par le service ===' -ForegroundColor Cyan
$body = @{ orderId = 'ORD-DEMO-REST'; amount = 12.00; currency = 'EUR' } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri "$BaseUrl/payments" -Method Post -ContentType 'application/json' -Body $body | Out-Null
# Sans X-Trace-Id, le traceId généré se retrouve dans les logs (GET /logs sans filtre).
$allLogs = Invoke-RestMethod -Uri "$BaseUrl/logs" -Method Get
$trace3 = ($allLogs | Where-Object { $_.msg -eq 'HTTP POST /payments completed with 200' } | Select-Object -Last 1).traceId
Write-Host "traceId généré par le service = $trace3"
Show-TraceLogs $trace3
