$ErrorActionPreference = 'Stop'

$QQSelfId = if ($args.Count -gt 0 -and -not [string]::IsNullOrWhiteSpace([string]$args[0])) {
  [string]$args[0]
} else {
  '230749497'
}

Set-Location 'D:\Apps\NapCat.Shell.clean'
Remove-Item 'D:\Apps\NapCat.Shell.clean\cache\qrcode.png' -Force -ErrorAction SilentlyContinue
& '.\launcher-user.bat' $QQSelfId
