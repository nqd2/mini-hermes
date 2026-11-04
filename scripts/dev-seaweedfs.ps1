param(
    [string]$ContainerName = 'mini-hermes-seaweedfs',
    [string]$Bucket = 'mini-hermes'
)

$ErrorActionPreference = 'Stop'
$image = 'chrislusf/seaweedfs:4.52'
$existing = docker ps -a --filter "name=^/$ContainerName$" --format '{{.Names}}'
if ($LASTEXITCODE -ne 0) { throw 'Docker is unavailable.' }
if ($existing -eq $ContainerName) {
    docker start $ContainerName | Out-Null
} else {
    docker run -d --name $ContainerName `
        -p '127.0.0.1:8333:8333' `
        -v 'mini-hermes-seaweedfs-data:/data' `
        -e 'AWS_ACCESS_KEY_ID=admin' `
        -e 'AWS_SECRET_ACCESS_KEY=secret' `
        -e "S3_BUCKET=$Bucket" `
        $image | Out-Null
}
if ($LASTEXITCODE -ne 0) { throw 'SeaweedFS could not start.' }
Write-Host "SeaweedFS S3 endpoint: http://127.0.0.1:8333; bucket: $Bucket"
Write-Host 'Set STORAGE_PROVIDER=s3, S3_ENDPOINT_URL=http://127.0.0.1:8333, S3_BUCKET_NAME=mini-hermes, S3_ACCESS_KEY_ID=admin, S3_SECRET_ACCESS_KEY=secret.'
