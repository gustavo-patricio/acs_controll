#Requires -RunAsAdministrator
param(
    [System.Net.IPAddress]$ServerAddress = '192.168.18.7',
    [string[]]$AllowedRemoteAddresses = @('192.168.18.0/24')
)

$ErrorActionPreference = 'Stop'
$wslCreator = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'
$ruleName = 'ACS-CWMP-WSL-Mirrored'

# Check availability before changing the previous network setup.
Get-Command New-NetFirewallHyperVRule -ErrorAction Stop | Out-Null
$rule = Get-NetFirewallHyperVRule -Name $ruleName -ErrorAction SilentlyContinue
if ($rule) {
    if ([guid]$rule.VMCreatorId -ne [guid]$wslCreator) {
        throw 'Ja existe uma regra com esse nome para outro servico.'
    }
    Set-NetFirewallHyperVRule -Name $ruleName -Enabled True -Direction Inbound `
        -Action Allow -Protocol TCP -LocalPorts 7547 -RemoteAddresses $AllowedRemoteAddresses
} else {
    New-NetFirewallHyperVRule -Name $ruleName -DisplayName 'ACS CWMP WSL mirrored' `
        -VMCreatorId $wslCreator -Enabled True -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPorts 7547 -RemoteAddresses $AllowedRemoteAddresses
}

# Remove only the old ACS listener on this address/port, not other portproxy rules.
& netsh interface portproxy delete v4tov4 "listenaddress=$ServerAddress" listenport=7547
if ($LASTEXITCODE -ne 0) {
    Write-Warning 'A regra portproxy pode nao existir. Confira netsh interface portproxy show all.'
}
Get-NetFirewallHyperVRule -Name $ruleName |
    Select-Object Name, Enabled, Action, LocalPorts, RemoteAddresses
Write-Host 'Confira outras regras de porta 7547: regras amplas preexistentes nao foram removidas.'
