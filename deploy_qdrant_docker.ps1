$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$configPath = Join-Path $projectRoot "config.json"
$config = Get-Content -Path $configPath -Raw | ConvertFrom-Json
$setting = $config.Qdrant_Setting
$port = if ($setting.port) { [int]$setting.port } else { 6333 }
$grpcPort = if ($setting.grpc_port) { [int]$setting.grpc_port } else { 6334 }
$dataPath = if ($setting.docker_data_path) { $setting.docker_data_path } else { "./qdrant_data_docker" }
$containerName = "selena-qdrant"
$image = "qdrant/qdrant:latest"
$hostDataPath = Join-Path $projectRoot $dataPath
if (-not (Test-Path $hostDataPath)) {
    New-Item -ItemType Directory -Path $hostDataPath | Out-Null
}
$existing = docker ps -a --filter "name=^${containerName}$" --format "{{.Names}}"
if ($existing -contains $containerName) {
    docker start $containerName | Out-Null
} else {
    docker run -d --name $containerName -p "${port}:6333" -p "${grpcPort}:6334" -v "${hostDataPath}:/qdrant/storage" $image | Out-Null
}
docker ps --filter "name=^${containerName}$"
