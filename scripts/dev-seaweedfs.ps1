
param(
    [string]$ContainerName = 'mini-hermes-seaweedfs',
    [string]$Bucket = 'mini-hermes'
)

$ErrorActionPreference = 'Stop'

# ============================================================
# Configuration
# ============================================================

$image = 'chrislusf/seaweedfs:4.52'
$volume = 'mini-hermes-seaweedfs-data'

# Local development credentials
$adminUser = 'admin'
$adminPassword = 'admin123'
$s3AccessKey = 'admin'
$s3SecretKey = 'secret'

# Required ports
$ports = @(8333, 8888, 9333, 9340, 12535)

# ============================================================
# Docker availability
# ============================================================

docker info --format '{{.ServerVersion}}' | Out-Null

if ($LASTEXITCODE -ne 0) {
    throw 'Docker is unavailable.'
}

# ============================================================
# Inspect existing container
# ============================================================

$existing = docker ps -a `
    --filter "name=^/$ContainerName$" `
    --format '{{.Names}}'

if ($LASTEXITCODE -ne 0) {
    throw 'Failed to list Docker containers.'
}

$recreate = $false
$containerExists = ($existing -eq $ContainerName)

if ($containerExists) {

    # Use JSON instead of Docker Go template.
    # This avoids PowerShell quoting issues on Windows.
    $inspectRaw = docker inspect $ContainerName | Out-String

    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to inspect SeaweedFS container.'
    }

    $inspectObjects = @(
        ConvertFrom-Json -InputObject $inspectRaw
    )

    $info = $inspectObjects[0]

    # --------------------------------------------------------
    # Check required port mappings
    # --------------------------------------------------------

    $bindings = $info.HostConfig.PortBindings

    foreach ($port in $ports) {
        $key = "$port/tcp"
        $property = $bindings.PSObject.Properties[$key]
        $mapped = $false

        if ($null -ne $property) {
            foreach ($binding in @($property.Value)) {
                if (
                    $binding.HostIp -eq '127.0.0.1' -and
                    $binding.HostPort -eq "$port"
                ) {
                    $mapped = $true
                    break
                }
            }
        }

        if (-not $mapped) {
            Write-Host "Missing port mapping: $port"
            $recreate = $true
        }
    }

    # --------------------------------------------------------
    # Check container environment
    # --------------------------------------------------------

    $containerEnv = @($info.Config.Env)

    $requiredEnv = @(
        "WEED_ADMIN_USER=$adminUser"
        "WEED_ADMIN_PASSWORD=$adminPassword"
        "AWS_ACCESS_KEY_ID=$s3AccessKey"
        "AWS_SECRET_ACCESS_KEY=$s3SecretKey"
        "S3_BUCKET=$Bucket"
    )

    foreach ($entry in $requiredEnv) {
        if ($containerEnv -cnotcontains $entry) {
            Write-Host 'Container environment needs updating.'
            $recreate = $true
            break
        }
    }

    # --------------------------------------------------------
    # Check image
    # --------------------------------------------------------

    if ($info.Config.Image -ne $image) {
        Write-Host 'Container image needs updating.'
        $recreate = $true
    }

    # --------------------------------------------------------
    # Safely recreate if configuration changed
    # --------------------------------------------------------

    if ($recreate) {
        # Identify volume mounted at /data.
        $dataMounts = @(
            $info.Mounts | Where-Object {
                $_.Destination -eq '/data'
            }
        )

        if ($dataMounts.Count -ne 1) {
            throw (
                'Expected exactly one /data mount. ' +
                'Refusing to recreate to protect data.'
            )
        }

        $dataMount = $dataMounts[0]

        if (
            $dataMount.Type -ne 'volume' -or
            $dataMount.Name -ne $volume
        ) {
            Write-Host "Found mount type: $($dataMount.Type)"
            Write-Host "Found mount name: $($dataMount.Name)"

            throw (
                'Unexpected /data volume. ' +
                'Refusing to recreate to protect data.'
            )
        }

        Write-Host ''
        Write-Host "Preserving Docker volume: $volume"
        Write-Host 'Recreating container with GUI ports...'

        # Removing a container without -v preserves named volumes.
        docker rm -f $ContainerName | Out-Null

        if ($LASTEXITCODE -ne 0) {
            throw 'Failed to remove old container.'
        }

        $containerExists = $false
    }
}

# ============================================================
# Create or start SeaweedFS
# ============================================================

if (-not $containerExists) {

    Write-Host 'Creating SeaweedFS 4.52 container...'

    $dockerArgs = @(
        'run'
        '-d'
        '--name', $ContainerName
        '--restart', 'unless-stopped'

        # S3 API
        '-p', '127.0.0.1:8333:8333'

        # Filer UI
        '-p', '127.0.0.1:8888:8888'

        # Master UI
        '-p', '127.0.0.1:9333:9333'

        # Volume Server
        '-p', '127.0.0.1:9340:9340'

        # Admin Dashboard
        '-p', '127.0.0.1:12535:12535'

        # Persistent data
        '-v', "${volume}:/data"

        # S3 credentials
        '-e', "AWS_ACCESS_KEY_ID=$s3AccessKey"
        '-e', "AWS_SECRET_ACCESS_KEY=$s3SecretKey"
        '-e', "S3_BUCKET=$Bucket"

        # Admin credentials
        '-e', "WEED_ADMIN_USER=$adminUser"
        '-e', "WEED_ADMIN_PASSWORD=$adminPassword"

        $image
    )

    & docker @dockerArgs | Out-Null

    if ($LASTEXITCODE -ne 0) {
        throw 'SeaweedFS container creation failed.'
    }

}
else {

    if (-not $info.State.Running) {
        Write-Host 'Starting existing SeaweedFS container...'

        docker start $ContainerName | Out-Null

        if ($LASTEXITCODE -ne 0) {
            throw 'SeaweedFS could not start.'
        }
    }
    else {
        Write-Host 'SeaweedFS is already running.'
    }
}

# ============================================================
# Verify container state
# ============================================================

$running = docker inspect `
    --format '{{.State.Running}}' `
    $ContainerName

if ($LASTEXITCODE -ne 0 -or $running -ne 'true') {
    docker logs --tail 30 $ContainerName
    throw 'SeaweedFS container is not running.'
}

# ============================================================
# Output
# ============================================================

Write-Host ''
Write-Host '=========================================='
Write-Host ' SeaweedFS 4.52 - Local Development'
Write-Host '=========================================='
Write-Host ''
Write-Host 'Endpoints:'
Write-Host ' S3 API      http://127.0.0.1:8333'
Write-Host ' Admin GUI   http://127.0.0.1:12535'
Write-Host ' Filer GUI   http://127.0.0.1:8888'
Write-Host ' Master GUI  http://127.0.0.1:9333'
Write-Host ' Volume UI   http://127.0.0.1:9340'
Write-Host ''
Write-Host 'Admin credentials:'
Write-Host " Username    $adminUser"
Write-Host " Password    $adminPassword"
Write-Host ''
Write-Host 'S3 configuration:'
Write-Host " Bucket      $Bucket"
Write-Host " Access Key  $s3AccessKey"
Write-Host " Secret Key  $s3SecretKey"
Write-Host ''
Write-Host "Volume: $volume"
Write-Host '=========================================='
