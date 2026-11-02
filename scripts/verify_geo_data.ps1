# scripts/verify_geo_data.ps1
# Verification script for geo-data service.
# Tests all core endpoints and contracts against a running service instance.

param(
    [string]$BaseUrl = "http://127.0.0.1:8000"
)

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  Geo-Data Service End-to-End Verification                " -ForegroundColor Cyan
Write-Host "  Target: $BaseUrl                                        " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$script:ErrorCount = 0

function Run-Check {
    param(
        [string]$Name,
        [string]$Url,
        [string]$Method = "GET",
        [string]$Body = $null,
        [int]$ExpectedStatus = 200,
        [scriptblock]$Validator
    )
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $headers = @{}
        $resp = $null
        $statusCode = 200
        $rawJson = $null

        if ($Method -eq "POST") {
            $httpReq = [System.Net.HttpWebRequest]::Create($Url)
            $httpReq.Method = "POST"
            $httpReq.ContentType = "application/json"
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($Body)
            $httpReq.ContentLength = $bytes.Length
            $reqStream = $httpReq.GetRequestStream()
            $reqStream.Write($bytes, 0, $bytes.Length)
            $reqStream.Close()
            try {
                $httpResp = $httpReq.GetResponse()
                $statusCode = [int]$httpResp.StatusCode
                $reader = New-Object System.IO.StreamReader($httpResp.GetResponseStream())
                $rawJson = $reader.ReadToEnd()
                $reader.Close()
            } catch [System.Net.WebException] {
                if ($_.Exception.Response) {
                    $statusCode = [int]$_.Exception.Response.StatusCode
                    $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                    $rawJson = $reader.ReadToEnd()
                    $reader.Close()
                } else {
                    throw $_
                }
            }
        } else {
            $httpReq = [System.Net.HttpWebRequest]::Create($Url)
            $httpReq.Method = "GET"
            try {
                $httpResp = $httpReq.GetResponse()
                $statusCode = [int]$httpResp.StatusCode
                $reader = New-Object System.IO.StreamReader($httpResp.GetResponseStream())
                $rawJson = $reader.ReadToEnd()
                $reader.Close()
            } catch [System.Net.WebException] {
                if ($_.Exception.Response) {
                    $statusCode = [int]$_.Exception.Response.StatusCode
                    $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                    $rawJson = $reader.ReadToEnd()
                    $reader.Close()
                } else {
                    throw $_
                }
            }
        }

        $stopwatch.Stop()
        $elapsedMs = $stopwatch.ElapsedMilliseconds

        if ($statusCode -ne $ExpectedStatus) {
            Write-Host "[FAIL] $Name (status: $statusCode, expected $ExpectedStatus, ${elapsedMs} ms)" -ForegroundColor Red
            $script:ErrorCount++
            return
        }

        $parsedObj = $null
        if ($rawJson) {
            try {
                $parsedObj = $rawJson | ConvertFrom-Json
            } catch {}
        }

        if ($Validator) {
            $isValid = & $Validator $parsedObj
            if ($isValid) {
                Write-Host "[PASS] $Name (${elapsedMs} ms)" -ForegroundColor Green
            } else {
                Write-Host "[FAIL] ${Name}: validation check failed (${elapsedMs} ms)" -ForegroundColor Red
                $script:ErrorCount++
            }
        } else {
            Write-Host "[PASS] $Name (${elapsedMs} ms)" -ForegroundColor Green
        }

    } catch {
        $stopwatch.Stop()
        $elapsedMs = $stopwatch.ElapsedMilliseconds
        Write-Host "[FAIL] ${Name}: $($_.Exception.Message) (${elapsedMs} ms)" -ForegroundColor Red
        $script:ErrorCount++
    }
}

# 1. Health check & data_as_of match
Run-Check -Name "GET /health (status=ok, data_as_of=2026-10-29)" `
    -Url "$BaseUrl/health" `
    -ExpectedStatus 200 `
    -Validator {
        param($res)
        $res.status -eq "ok" -and $res.data_as_of -eq "2026-10-29"
    }

# 2. Ambiguous query: Durbar Square
Run-Check -Name "GET /resolve?q=Durbar+Square (ambiguous=true, 3 options, best_match=null)" `
    -Url "$BaseUrl/resolve?q=Durbar+Square" `
    -ExpectedStatus 200 `
    -Validator {
        param($res)
        $res.ambiguous -eq $true -and $res.clarification_needed -eq $true -and $null -eq $res.best_match -and $res.options.Count -eq 3
    }

# 3. Non-ambiguous query: EBC
Run-Check -Name "GET /resolve?q=EBC (best_match present, ambiguous=false)" `
    -Url "$BaseUrl/resolve?q=EBC" `
    -ExpectedStatus 200 `
    -Validator {
        param($res)
        $res.ambiguous -eq $false -and $null -ne $res.best_match -and $res.best_match.short_name -eq "Everest Base Camp"
    }

# 4. Unknown query parameter returns 422
Run-Check -Name "GET /resolve?q=Kathmandu&typo=1 (rejects typo with HTTP 422)" `
    -Url "$BaseUrl/resolve?q=Kathmandu&typo=1" `
    -ExpectedStatus 422 `
    -Validator {
        param($res)
        $res.detail -match "typo"
    }

# 5. Profile endpoint with demographics
Run-Check -Name "GET /profile (Kathmandu centroid returns 200 with demographics)" `
    -Url "$BaseUrl/profile?lat=27.7041&lon=85.3075" `
    -ExpectedStatus 200 `
    -Validator {
        param($res)
        $null -ne $res.admin_unit -and $null -ne $res.demographics -and $null -ne $res.demographics.population
    }

# 6. Amenities point query for Chhatradev
Run-Check -Name "GET /amenities (Chhatradev 10km: health >= 6, evacuation >= 45)" `
    -Url "$BaseUrl/amenities?lat=27.9954&lon=83.2253&radius_m=10000" `
    -ExpectedStatus 200 `
    -Validator {
        param($res)
        $res.totals.health -ge 6 -and $res.totals.evacuation_sites -ge 45
    }

# 7. Amenities area query for Chhatradev
Run-Check -Name "POST /amenities/area (Chhatradev boundary code: 55102, total > 0)" `
    -Url "$BaseUrl/amenities/area" `
    -Method "POST" `
    -Body '{"local_level_code": "55102"}' `
    -ExpectedStatus 200 `
    -Validator {
        param($res)
        $res.summary.total -gt 0 -and $res.summary.by_category.health -gt 0
    }

Write-Host "`n----------------------------------------------------------"
if ($script:ErrorCount -eq 0) {
    Write-Host "ALL VERIFICATION CHECKS PASSED!" -ForegroundColor Green
    exit 0
} else {
    Write-Host "VERIFICATION FAILED: $script:ErrorCount checks failed." -ForegroundColor Red
    exit 1
}
