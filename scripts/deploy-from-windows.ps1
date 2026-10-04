# Copy this project (including your local .env) to the server and deploy it.
# Usage, in PowerShell from the project folder:   .\scripts\deploy-from-windows.ps1 <server-ip>
param([Parameter(Mandatory = $true)][string]$Server)
$ErrorActionPreference = "Stop"

$archive = Join-Path $env:TEMP "lookmate.tgz"
tar -czf $archive --exclude=.git --exclude=.venv --exclude=data/cache .
ssh "root@$Server" "mkdir -p /opt/lookmate"
scp $archive "root@${Server}:/opt/lookmate.tgz"
ssh "root@$Server" "tar -xzf /opt/lookmate.tgz -C /opt/lookmate && bash /opt/lookmate/scripts/deploy.sh"
Remove-Item $archive
