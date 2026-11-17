param(
    [Parameter(Mandatory = $true)][string]$HostName,
    [string]$User = "ubuntu",
    [string]$Key = ".secrets\ssh_key"
)

$root = Split-Path -Parent $PSScriptRoot
$archive = Join-Path $env:TEMP "assistant.tar.gz"

Push-Location $root
tar -czf $archive --exclude=.git --exclude=.venv --exclude=.secrets --exclude=credits.yml --exclude=__pycache__ --exclude=.pytest_cache --exclude=*.egg-info .
Pop-Location

scp -i $Key -o StrictHostKeyChecking=accept-new $archive "${User}@${HostName}:/home/${User}/assistant.tar.gz"
scp -i $Key -o StrictHostKeyChecking=accept-new (Join-Path $root ".env") "${User}@${HostName}:/home/${User}/.env"

ssh -i $Key "${User}@${HostName}" @"
set -e
mkdir -p ~/ai_assistant
tar -xzf ~/assistant.tar.gz -C ~/ai_assistant
rm ~/assistant.tar.gz
mv ~/.env ~/ai_assistant/.env
cd ~/ai_assistant
sed -i 's/\r$//' .env infra/*.sh
sudo docker compose up -d --build
sleep 5
curl -fsS http://localhost/health
"@

Write-Output "Deployed to ${User}@${HostName}"
