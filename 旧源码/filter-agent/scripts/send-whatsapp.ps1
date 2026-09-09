[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\+[1-9]\d{5,14}$')]
    [string]$Phone,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Text,

    [ValidateNotNullOrEmpty()]
    [string]$BridgeUrl = 'http://127.0.0.1:3010'
)

$requestBody = @{
    phone = $Phone
    text = $Text
} | ConvertTo-Json -Compress

Invoke-RestMethod `
    -Method Post `
    -Uri "$($BridgeUrl.TrimEnd('/'))/messages/send" `
    -ContentType 'application/json; charset=utf-8' `
    -Body $requestBody `
    -TimeoutSec 30
