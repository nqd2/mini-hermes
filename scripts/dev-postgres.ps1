param(
    [string]$ContainerName = 'mini-hermes-postgres-dev',
    [string]$EnvPath = (Join-Path $PSScriptRoot '..\packages\backend\.env')
)

$ErrorActionPreference = 'Stop'
$image = 'postgres:17.6'
$databaseLine = Get-Content -LiteralPath $EnvPath | Where-Object { $_ -match '^DATABASE_URL=' } | Select-Object -Last 1
if (-not $databaseLine) { throw 'Set DATABASE_URL in packages/backend/.env before starting PostgreSQL.' }
$databaseUrl = $databaseLine.Substring('DATABASE_URL='.Length).Trim()
try { $uri = [Uri]$databaseUrl } catch { throw 'DATABASE_URL is not a valid PostgreSQL URL.' }
if ($uri.Scheme -ne 'postgresql' -or $uri.Host -ne '127.0.0.1' -or $uri.Port -ne 5433) {
    throw 'Dev DATABASE_URL must use postgresql://...@127.0.0.1:5433/...'
}
$credentials = $uri.UserInfo -split ':', 2
if ($credentials.Count -ne 2) { throw 'DATABASE_URL must contain a username and password.' }
$dbUser = [Uri]::UnescapeDataString($credentials[0])
$dbPassword = [Uri]::UnescapeDataString($credentials[1])
$dbName = $uri.AbsolutePath.TrimStart('/')
if (-not $dbUser -or -not $dbPassword -or -not $dbName -or $dbPassword -match '^replace-') {
    throw 'Fill the PostgreSQL username, password and database name in DATABASE_URL.'
}

$existing = docker ps -a --filter "name=^/$ContainerName$" --format '{{.Names}}'
if ($LASTEXITCODE -ne 0) { throw 'Docker is unavailable.' }
if ($existing -eq $ContainerName) {
    # docker port only reports active network bindings; inspect the saved mapping
    # before starting a stopped container.
    $bindingsJson = docker inspect $ContainerName --format '{{json .HostConfig.PortBindings}}'
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect $ContainerName." }
    $bindings = $bindingsJson | ConvertFrom-Json
    $mapping = $bindings.'5432/tcp' | Select-Object -First 1
    if (-not $mapping -or $mapping.HostIp -ne '127.0.0.1' -or $mapping.HostPort -ne '5433') {
        throw "Existing $ContainerName does not publish 127.0.0.1:5433."
    }
    docker start $ContainerName | Out-Null
} else {
    docker run -d --name $ContainerName `
        -p '127.0.0.1:5433:5432' `
        -v 'mini-hermes-postgres-dev-data:/var/lib/postgresql/data' `
        -e "POSTGRES_DB=$dbName" `
        -e "POSTGRES_USER=$dbUser" `
        -e "POSTGRES_PASSWORD=$dbPassword" `
        $image | Out-Null
}
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL could not start.' }

for ($attempt = 0; $attempt -lt 60; $attempt++) {
    $result = docker exec -e "PGPASSWORD=$dbPassword" $ContainerName `
        psql -h 127.0.0.1 -U $dbUser -d $dbName -tAc 'SELECT 1' 2>$null
    if ($LASTEXITCODE -eq 0 -and $result.Trim() -eq '1') {
        Write-Host 'PostgreSQL ready at 127.0.0.1:5433.'
        return
    }
    Start-Sleep -Seconds 1
}
throw 'PostgreSQL did not accept the DATABASE_URL credentials within 60 seconds. Check docker logs mini-hermes-postgres-dev.'
