# Copy this project (including your local .env) to the server and deploy it.
# Usage, in PowerShell from the project folder:   .\scripts\deploy-from-windows.ps1 <server-ip>
param([Parameter(Mandatory = $true)][string]$Server)
$ErrorActionPreference = "Stop"

$archive = Join-Path $env:TEMP "lookmate.tgz"
tar -czf $archive --exclude=.git --exclude=.venv --exclude=data/cache .
ssh "root@$Server" "mkdir -p /opt/lookmate"
scp $archive "root@${Server}:/opt/lookmate.tgz"
# Git on Windows may check files out with CRLF line endings, which bash and .env parsing reject:
# convert scripts, .env and config files back to LF on the server before running anything.
$fixEol = "cd /opt/lookmate && find . -type f \( -name '*.sh' -o -name '.env' -o -name '*.conf' -o -name 'Caddyfile' -o -name '*.yml' \) -exec sed -i 's/\r`$//' {} +"
ssh "root@$Server" "tar -xzf /opt/lookmate.tgz -C /opt/lookmate && $fixEol && bash /opt/lookmate/scripts/deploy.sh"
Remove-Item $archive
