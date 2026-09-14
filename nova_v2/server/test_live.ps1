# Ad-hoc smoke test for the live nova-v2 Cloud Run deploy - covers the paths
# that were NOT exercised by the original /event + /tools/gain curl checks:
# /persona*, /event/continue (need_more round-trip), and a real (non-mock)
# Claude call. Run this yourself - it pulls NOVA_API_KEY from Secret Manager
# into a local variable and never prints it.
#
# Usage: powershell -File test_live.ps1   (or just run it from a PowerShell prompt)

$ErrorActionPreference = "Stop"

$BaseUrl = "https://nova-v2-1021689546881.australia-southeast1.run.app"
$Project = "project-nova-505909"

$ApiKey = gcloud secrets versions access latest --secret=nova-api-key --project $Project
if (-not $ApiKey) { throw "Could not fetch nova-api-key from Secret Manager" }

$Headers = @{ "X-Nova-Api-Key" = $ApiKey }

Write-Host "=== 1. GET /persona (list beliefs) ==="
$persona = Invoke-RestMethod -Uri "$BaseUrl/persona" -Headers $Headers -Method Get
$persona | ConvertTo-Json -Depth 10
Write-Host ""

Write-Host "=== 2. GET /persona/graph (knowledge map) ==="
$graph = Invoke-RestMethod -Uri "$BaseUrl/persona/graph" -Headers $Headers -Method Get
$graph | ConvertTo-Json -Depth 10
Write-Host ""

Write-Host "=== 3. POST /persona/consolidate?preview=true (safe, writes nothing) ==="
$preview = Invoke-RestMethod -Uri "$BaseUrl/persona/consolidate?preview=true" -Headers $Headers -Method Post
$preview | ConvertTo-Json -Depth 10
Write-Host ""

Write-Host "=== 4. POST /event - real Claude call (plain voice event) ==="
$body1 = @{
    event = @{
        type      = "voice"
        id        = [guid]::NewGuid().ToString()
        timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        text      = "Hey Nova, how's it going?"
    }
    user_state = @{
        confidence = 0.9
    }
} | ConvertTo-Json -Depth 10

$eventResp = Invoke-RestMethod -Uri "$BaseUrl/event" -Headers $Headers -Method Post -Body $body1 -ContentType "application/json"
$eventResp | ConvertTo-Json -Depth 10
Write-Host ""
Write-Host "--> Check the 'speech' field above. If it starts with '[mock] received event'"
Write-Host "    NOVA_MOCK_LLM is set on the deployed service (should NOT be). Any other"
Write-Host "    natural-sounding reply confirms a real Anthropic API call happened."
Write-Host ""

Write-Host "=== 5. POST /event - try to trigger a need_more (calendar range) pause ==="
$body2 = @{
    event = @{
        type      = "voice"
        id        = [guid]::NewGuid().ToString()
        timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        text      = "What have I got on next Friday afternoon?"
    }
    user_state = @{
        confidence   = 0.9
        calendar_ctx = "free"
    }
} | ConvertTo-Json -Depth 10

$needMoreResp = Invoke-RestMethod -Uri "$BaseUrl/event" -Headers $Headers -Method Post -Body $body2 -ContentType "application/json"
$needMoreResp | ConvertTo-Json -Depth 10
Write-Host ""

if ($needMoreResp.status -eq "need_more" -and $needMoreResp.session_id) {
    Write-Host "=== 6. POST /event/continue - resume session $($needMoreResp.session_id) ==="
    $body3 = @{
        session_id = $needMoreResp.session_id
        result     = @()
    } | ConvertTo-Json -Depth 10

    $continueResp = Invoke-RestMethod -Uri "$BaseUrl/event/continue" -Headers $Headers -Method Post -Body $body3 -ContentType "application/json"
    $continueResp | ConvertTo-Json -Depth 10
} else {
    Write-Host "=== 6. No need_more pause this time (Claude answered directly, e.g. said it"
    Write-Host "    has no calendar access) - status was '$($needMoreResp.status)' not 'need_more'."
    Write-Host "    Re-run step 5 a couple times, or reword the voice text, until you see"
    Write-Host "    status=need_more with a session_id to exercise /event/continue."
}

Write-Host ""
Write-Host "Done."
