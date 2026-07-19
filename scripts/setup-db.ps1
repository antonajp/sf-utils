# PostgreSQL setup script for sf-utils local development (Windows PowerShell)
# Creates a Docker container with PostgreSQL 16 and sf_utils database

$ErrorActionPreference = "Stop"

# Configuration
$CONTAINER_NAME = "sf-utils-postgres"
$DB_NAME = "sf_utils"
$DB_USER = "postgres"
$DB_PASSWORD = "postgres"
$DB_PORT = "5432"
$POSTGRES_VERSION = "16"

function Write-Success {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Error {
    param([string]$Message)
    Write-Host "[X] $Message" -ForegroundColor Red
}

function Write-Warning {
    param([string]$Message)
    Write-Host "[!] $Message" -ForegroundColor Yellow
}

function Test-Docker {
    # Check if Docker is installed
    try {
        $null = Get-Command docker -ErrorAction Stop
    }
    catch {
        Write-Error "Docker is not installed"
        Write-Host "Please install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/"
        exit 1
    }

    # Check if Docker is running
    try {
        $null = docker info 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "Docker not running"
        }
    }
    catch {
        Write-Error "Docker is not running"
        Write-Host "Please start Docker Desktop and try again"
        exit 1
    }

    Write-Success "Docker is running"
}

function Remove-ExistingContainer {
    $existingContainer = docker ps -a --format "{{.Names}}" | Where-Object { $_ -eq $CONTAINER_NAME }

    if ($existingContainer) {
        Write-Warning "Removing existing $CONTAINER_NAME container"
        docker stop $CONTAINER_NAME 2>&1 | Out-Null
        docker rm $CONTAINER_NAME 2>&1 | Out-Null
        Write-Success "Removed existing $CONTAINER_NAME container"
    }
}

function Test-PortAvailable {
    $portInUse = Get-NetTCPConnection -LocalPort $DB_PORT -ErrorAction SilentlyContinue

    if ($portInUse) {
        Write-Error "Port $DB_PORT is already in use"
        Write-Host "Please free port $DB_PORT or stop the service using it:"
        Write-Host "  - Check what's using it: Get-NetTCPConnection -LocalPort $DB_PORT"
        Write-Host "  - If it's another PostgreSQL instance, stop it first"
        exit 1
    }
}

function New-PostgresContainer {
    try {
        docker run -d `
            --name $CONTAINER_NAME `
            -e "POSTGRES_DB=$DB_NAME" `
            -e "POSTGRES_USER=$DB_USER" `
            -e "POSTGRES_PASSWORD=$DB_PASSWORD" `
            -p "127.0.0.1:${DB_PORT}:5432" `
            "postgres:$POSTGRES_VERSION" | Out-Null

        if ($LASTEXITCODE -ne 0) {
            throw "Docker run failed"
        }

        Write-Success "Created new PostgreSQL container"
    }
    catch {
        # Clean up failed container if it was created
        docker rm $CONTAINER_NAME 2>&1 | Out-Null

        Write-Error "Failed to create PostgreSQL container"
        Write-Host "This usually means port $DB_PORT is already in use."
        Write-Host "Check what's using it: Get-NetTCPConnection -LocalPort $DB_PORT"
        exit 1
    }
}

function Wait-ForPostgres {
    Write-Host "Waiting for PostgreSQL to be ready" -NoNewline

    $maxAttempts = 30
    $attempt = 1

    while ($attempt -le $maxAttempts) {
        $result = docker exec $CONTAINER_NAME pg_isready -U $DB_USER -d $DB_NAME 2>&1

        if ($LASTEXITCODE -eq 0) {
            Write-Host ""
            Write-Success "PostgreSQL is ready"
            return
        }

        Write-Host "." -NoNewline
        Start-Sleep -Seconds 1
        $attempt++
    }

    Write-Host ""
    Write-Error "PostgreSQL failed to become ready after $maxAttempts seconds"
    exit 1
}

function Write-ConnectionInfo {
    Write-Host ""
    Write-Host "Connection: postgresql://${DB_USER}:${DB_PASSWORD}@localhost:${DB_PORT}/${DB_NAME}"
    Write-Host ""
    Write-Host "Add to your .env:"
    Write-Host "  PG_HOST=localhost"
    Write-Host "  PG_PORT=$DB_PORT"
    Write-Host "  PG_DATABASE=$DB_NAME"
    Write-Host "  PG_USER=$DB_USER"
    Write-Host "  PG_PASSWORD=$DB_PASSWORD"
    Write-Host ""
}

# Main execution
Test-Docker
Remove-ExistingContainer
Test-PortAvailable
New-PostgresContainer
Wait-ForPostgres
Write-ConnectionInfo
