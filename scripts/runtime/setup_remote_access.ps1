[CmdletBinding()]
param(
  [bool]$EnableOpenSsh = $true,
  [bool]$EnableWinRm = $true,
  [int]$SshPort = 22,
  [ValidateSet("none", "powershell", "pwsh")]
  [string]$DefaultShell = "powershell",
  [string]$ShellPath = "",
  [switch]$SkipFirewallRules,
  [switch]$SkipSshConfigTuning,
  [switch]$NoSelfElevate
)

$ErrorActionPreference = "Stop"

function Test-IsAdministrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-CurrentPowerShellPath {
  $currentProcess = Get-Process -Id $PID -ErrorAction SilentlyContinue
  if ($null -ne $currentProcess -and -not [string]::IsNullOrWhiteSpace($currentProcess.Path)) {
    return $currentProcess.Path
  }

  $fallback = Join-Path $PSHOME "powershell.exe"
  if (Test-Path -Path $fallback) {
    return $fallback
  }

  $cmd = Get-Command powershell.exe -ErrorAction SilentlyContinue
  if ($null -ne $cmd) {
    return $cmd.Source
  }

  throw "Unable to locate powershell.exe for self-elevation."
}

function Format-PowerShellArgument {
  param([Parameter(Mandatory = $true)][string]$Value)

  if ($Value -match '[\s"`]') {
    return '"' + ($Value -replace '`', '``' -replace '"', '`"') + '"'
  }
  return $Value
}

function Get-SelfElevationCommand {
  param(
    [Parameter(Mandatory = $true)]
    [string]$ScriptPath,
    [Parameter(Mandatory = $true)]
    [hashtable]$BoundParameters
  )

  $arguments = New-Object System.Collections.Generic.List[string]
  [void]$arguments.Add("-NoLogo")
  [void]$arguments.Add("-NoProfile")
  [void]$arguments.Add("-ExecutionPolicy")
  [void]$arguments.Add("Bypass")
  [void]$arguments.Add("-File")
  [void]$arguments.Add((Format-PowerShellArgument -Value $ScriptPath))

  foreach ($entry in $BoundParameters.GetEnumerator() | Sort-Object Key) {
    if ($entry.Key -eq "NoSelfElevate") {
      continue
    }

    if ($entry.Value -is [switch]) {
      if ($entry.Value.IsPresent) {
        [void]$arguments.Add("-$($entry.Key)")
      }
      continue
    }

    if ($entry.Value -is [bool]) {
      [void]$arguments.Add("-$($entry.Key):$($entry.Value.ToString().ToLowerInvariant())")
      continue
    }

    [void]$arguments.Add("-$($entry.Key)")
    [void]$arguments.Add((Format-PowerShellArgument -Value ([string]$entry.Value)))
  }

  [void]$arguments.Add("-NoSelfElevate")

  return [ordered]@{
    file_path = Get-CurrentPowerShellPath
    argument_list = @($arguments)
    printable_command = ("{0} {1}" -f (Format-PowerShellArgument -Value (Get-CurrentPowerShellPath)), ($arguments -join " "))
  }
}

function Ensure-Administrator {
  param(
    [Parameter(Mandatory = $true)]
    [string]$ScriptPath,
    [Parameter(Mandatory = $true)]
    [hashtable]$BoundParameters
  )

  if (Test-IsAdministrator) {
    return
  }

  $elevationCommand = Get-SelfElevationCommand -ScriptPath $ScriptPath -BoundParameters $BoundParameters
  if (-not $NoSelfElevate.IsPresent -and [Environment]::UserInteractive) {
    try {
      $proc = Start-Process -FilePath $elevationCommand.file_path -ArgumentList $elevationCommand.argument_list -Verb RunAs -Wait -PassThru -ErrorAction Stop
      exit $proc.ExitCode
    } catch {
      $detail = $_.Exception.Message
      throw "setup_remote_access.ps1 requires administrator privileges. Auto-elevation failed: $detail`nRun this command in an elevated PowerShell session:`n$($elevationCommand.printable_command)"
    }
  }

  throw "setup_remote_access.ps1 requires administrator privileges. Run this command in an elevated PowerShell session:`n$($elevationCommand.printable_command)"
}

function Ensure-ServiceState {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Name,
    [string]$StartupType = "Automatic"
  )

  $service = Get-Service -Name $Name -ErrorAction Stop
  $startupChanged = $false
  $serviceStarted = $false

  $cim = Get-CimInstance -ClassName Win32_Service -Filter "Name='$Name'"
  if ($null -ne $cim -and $cim.StartMode -ne $StartupType) {
    Set-Service -Name $Name -StartupType $StartupType
    $startupChanged = $true
  }

  if ($service.Status -ne "Running") {
    Start-Service -Name $Name
    $serviceStarted = $true
    $service = Get-Service -Name $Name -ErrorAction Stop
  }

  return [ordered]@{
    name = $Name
    status = [string]$service.Status
    startup_type = $StartupType
    startup_changed = $startupChanged
    started_now = $serviceStarted
  }
}

function Ensure-FirewallRule {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Name,
    [Parameter(Mandatory = $true)]
    [string]$DisplayName,
    [Parameter(Mandatory = $true)]
    [int]$Port
  )

  $existing = Get-NetFirewallRule -Name $Name -ErrorAction SilentlyContinue
  if ($null -eq $existing) {
    New-NetFirewallRule -Name $Name -DisplayName $DisplayName -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort $Port | Out-Null
    return [ordered]@{
      name = $Name
      port = $Port
      status = "created"
    }
  }

  Set-NetFirewallRule -Name $Name -Enabled True -Direction Inbound -Action Allow | Out-Null
  $portFilter = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $existing | Select-Object -First 1
  $portChanged = $false
  if ($null -eq $portFilter -or [string]$portFilter.LocalPort -ne [string]$Port) {
    Set-NetFirewallRule -Name $Name -LocalPort $Port -Protocol TCP | Out-Null
    $portChanged = $true
  }

  return [ordered]@{
    name = $Name
    port = $Port
    status = if ($portChanged) { "updated" } else { "present" }
  }
}

function Ensure-RegistryStringValue {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [Parameter(Mandatory = $true)]
    [string]$Name,
    [Parameter(Mandatory = $true)]
    [string]$Value
  )

  if (-not (Test-Path -Path $Path)) {
    New-Item -Path $Path -Force | Out-Null
  }

  $current = $null
  try {
    $current = (Get-ItemProperty -Path $Path -Name $Name -ErrorAction Stop).$Name
  } catch {
  }

  if ([string]$current -ne $Value) {
    New-ItemProperty -Path $Path -Name $Name -Value $Value -PropertyType String -Force | Out-Null
    return [ordered]@{
      name = $Name
      value = $Value
      status = "updated"
    }
  }

  return [ordered]@{
    name = $Name
    value = $Value
    status = "present"
  }
}

function Resolve-DefaultShellPath {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Mode,
    [string]$OverridePath = ""
  )

  if (-not [string]::IsNullOrWhiteSpace($OverridePath)) {
    if (-not (Test-Path -Path $OverridePath)) {
      throw "ShellPath does not exist: $OverridePath"
    }
    return (Resolve-Path -Path $OverridePath).Path
  }

  if ($Mode -eq "none") {
    return $null
  }

  if ($Mode -eq "pwsh") {
    $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($null -ne $pwsh) {
      return $pwsh.Source
    }
  }

  $windowsPowerShell = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
  if (Test-Path -Path $windowsPowerShell) {
    return $windowsPowerShell
  }

  throw "Unable to resolve a PowerShell executable for DefaultShell=$Mode."
}

function Ensure-WindowsCapabilityInstalled {
  param(
    [Parameter(Mandatory = $true)]
    [string]$CapabilityPattern
  )

  $capability = Get-WindowsCapability -Online | Where-Object { $_.Name -like $CapabilityPattern } | Select-Object -First 1
  if ($null -eq $capability) {
    throw "Windows capability not found: $CapabilityPattern"
  }

  if ($capability.State -ne "Installed") {
    Add-WindowsCapability -Online -Name $capability.Name | Out-Null
    $capability = Get-WindowsCapability -Online | Where-Object { $_.Name -eq $capability.Name } | Select-Object -First 1
    return [ordered]@{
      name = $capability.Name
      state = [string]$capability.State
      installed_now = $true
    }
  }

  return [ordered]@{
    name = $capability.Name
    state = [string]$capability.State
    installed_now = $false
  }
}

function Test-PortListening {
  param([int]$Port)
  $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  return ($null -ne $listener)
}

function Set-SshdConfigDirective {
  param(
    [Parameter(Mandatory = $true)]
    [System.Collections.Generic.List[string]]$Lines,
    [Parameter(Mandatory = $true)]
    [string]$Key,
    [Parameter(Mandatory = $true)]
    [string]$Value
  )

  $pattern = "^\s*#?\s*{0}\b.*$" -f [regex]::Escape($Key)
  $replacement = "$Key $Value"
  $indexes = New-Object System.Collections.Generic.List[int]

  for ($i = 0; $i -lt $Lines.Count; $i++) {
    if ($Lines[$i] -match $pattern) {
      [void]$indexes.Add($i)
    }
  }

  if ($indexes.Count -eq 0) {
    [void]$Lines.Add($replacement)
    return $true
  }

  $changed = $false
  $firstIndex = $indexes[0]
  if ($Lines[$firstIndex] -ne $replacement) {
    $Lines[$firstIndex] = $replacement
    $changed = $true
  }

  for ($j = $indexes.Count - 1; $j -ge 1; $j--) {
    $Lines.RemoveAt($indexes[$j])
    $changed = $true
  }

  return $changed
}

function Update-SshdConfig {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [Parameter(Mandatory = $true)]
    [int]$Port
  )

  if (-not (Test-Path -Path (Split-Path -Path $Path -Parent))) {
    New-Item -ItemType Directory -Path (Split-Path -Path $Path -Parent) -Force | Out-Null
  }

  $lines = New-Object System.Collections.Generic.List[string]
  if (Test-Path -Path $Path) {
    foreach ($line in (Get-Content -Path $Path -ErrorAction Stop)) {
      [void]$lines.Add([string]$line)
    }
  }

  $desired = [ordered]@{
    Port = [string]$Port
    PubkeyAuthentication = "yes"
    PasswordAuthentication = "yes"
    TCPKeepAlive = "yes"
    ClientAliveInterval = "30"
    ClientAliveCountMax = "4"
    UseDNS = "no"
  }

  $changed = $false
  foreach ($key in $desired.Keys) {
    if (Set-SshdConfigDirective -Lines $lines -Key $key -Value $desired[$key]) {
      $changed = $true
    }
  }

  $backupPath = $null
  if ($changed) {
    if (Test-Path -Path $Path) {
      $backupPath = "$Path.bak.$((Get-Date).ToString('yyyyMMddHHmmss'))"
      Copy-Item -Path $Path -Destination $backupPath -Force
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllLines($Path, $lines, $utf8NoBom)
  }

  return [ordered]@{
    path = $Path
    backup_path = $backupPath
    changed = $changed
  }
}

function Get-WinRmListenerSummary {
  $items = @()
  try {
    $listeners = Get-ChildItem WSMan:\localhost\Listener -ErrorAction Stop
    foreach ($listener in $listeners) {
      $items += [ordered]@{
        name = $listener.Name
        keys = @($listener.Keys)
      }
    }
  } catch {
  }
  return $items
}

Ensure-Administrator -ScriptPath $PSCommandPath -BoundParameters $PSBoundParameters

$sshResult = [ordered]@{
  enabled = $EnableOpenSsh
  port = $SshPort
}
$winrmResult = [ordered]@{
  enabled = $EnableWinRm
  port = 5985
}

if ($EnableOpenSsh) {
  $sshCapability = Ensure-WindowsCapabilityInstalled -CapabilityPattern "OpenSSH.Server*"
  $sshService = Ensure-ServiceState -Name "sshd" -StartupType "Automatic"

  $sshConfig = [ordered]@{
    path = $null
    backup_path = $null
    changed = $false
    skipped = $SkipSshConfigTuning.IsPresent
  }
  if (-not $SkipSshConfigTuning.IsPresent) {
    $sshConfigPath = Join-Path $env:ProgramData "ssh\sshd_config"
    $sshConfig = Update-SshdConfig -Path $sshConfigPath -Port $SshPort
    $sshConfig["skipped"] = $false
  }

  $defaultShellValue = $null
  $defaultShellRegistry = $null
  if ($DefaultShell -ne "none" -or -not [string]::IsNullOrWhiteSpace($ShellPath)) {
    $defaultShellValue = Resolve-DefaultShellPath -Mode $DefaultShell -OverridePath $ShellPath
    $defaultShellRegistry = Ensure-RegistryStringValue -Path "HKLM:\SOFTWARE\OpenSSH" -Name "DefaultShell" -Value $defaultShellValue
  }

  $sshFirewall = [ordered]@{
    name = "natsume-remote-ssh"
    port = $SshPort
    status = "skipped"
  }
  if (-not $SkipFirewallRules.IsPresent) {
    $sshFirewall = Ensure-FirewallRule -Name "natsume-remote-ssh" -DisplayName "Natsume Remote SSH" -Port $SshPort
  }

  if ($sshCapability.installed_now -or $sshService.started_now -or $sshConfig.changed -or ($null -ne $defaultShellRegistry -and $defaultShellRegistry.status -eq "updated")) {
    Restart-Service -Name "sshd" -ErrorAction Stop
    $sshService = Ensure-ServiceState -Name "sshd" -StartupType "Automatic"
  }

  $sshResult["capability"] = $sshCapability
  $sshResult["service"] = $sshService
  $sshResult["config"] = $sshConfig
  $sshResult["default_shell"] = $defaultShellValue
  $sshResult["default_shell_registry"] = $defaultShellRegistry
  $sshResult["firewall"] = $sshFirewall
  $sshResult["listening"] = (Test-PortListening -Port $SshPort)
}

if ($EnableWinRm) {
  Enable-PSRemoting -Force -SkipNetworkProfileCheck
  $winrmService = Ensure-ServiceState -Name "WinRM" -StartupType "Automatic"

  $winrmFirewall = [ordered]@{
    name = "natsume-remote-winrm-http"
    port = 5985
    status = "skipped"
  }
  if (-not $SkipFirewallRules.IsPresent) {
    $winrmFirewall = Ensure-FirewallRule -Name "natsume-remote-winrm-http" -DisplayName "Natsume Remote WinRM HTTP" -Port 5985
  }

  $winrmResult["service"] = $winrmService
  $winrmResult["firewall"] = $winrmFirewall
  $winrmResult["listeners"] = Get-WinRmListenerSummary
  $winrmResult["listening"] = (Test-PortListening -Port 5985)
}

$result = [ordered]@{
  status = "configured"
  host = $env:COMPUTERNAME
  applied_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  ssh = $sshResult
  winrm = $winrmResult
  connect_examples = [ordered]@{
    ssh = "ssh username@$env:COMPUTERNAME"
    ssh_no_profile = "ssh username@$env:COMPUTERNAME `"powershell -NoLogo -NoProfile`""
    winrm = "Enter-PSSession -ComputerName $env:COMPUTERNAME -Credential (Get-Credential)"
  }
}

$result | ConvertTo-Json -Depth 8
