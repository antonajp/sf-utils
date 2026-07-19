#!/usr/bin/env bash
# PostgreSQL setup script for sf-utils local development
# Creates a Docker container with PostgreSQL 16 and sf_utils database

set -e  # Exit on any error

# Configuration
CONTAINER_NAME="sf-utils-postgres"
DB_NAME="sf_utils"
DB_USER="postgres"
DB_PASSWORD="postgres"
DB_PORT="5432"
POSTGRES_VERSION="16"

# ANSI color codes for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Print success message
print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

# Print error message
print_error() {
    echo -e "${RED}✗${NC} $1" >&2
}

# Print warning message
print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# Check if Docker is installed and running
check_docker() {
    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed"
        echo "Please install Docker: https://docs.docker.com/get-docker/"
        exit 1
    fi

    if ! docker info &> /dev/null; then
        print_error "Docker is not running"
        echo "Please start Docker and try again"
        exit 1
    fi

    print_success "Docker is running"
}

# Remove existing container if present
remove_existing_container() {
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        print_warning "Removing existing ${CONTAINER_NAME} container"
        docker stop "${CONTAINER_NAME}" &> /dev/null || true
        docker rm "${CONTAINER_NAME}" &> /dev/null || true
        print_success "Removed existing ${CONTAINER_NAME} container"
    fi
}

# Check if port is available
check_port_available() {
    local port_in_use=false

    # Try lsof first (most reliable if available)
    if command -v lsof &> /dev/null; then
        if lsof -i :"${DB_PORT}" &> /dev/null; then
            port_in_use=true
        fi
    # Try ss next (modern Linux systems)
    elif command -v ss &> /dev/null; then
        if ss -tln 2>/dev/null | grep -q ":${DB_PORT} "; then
            port_in_use=true
        fi
    # Fall back to netstat (legacy systems)
    elif command -v netstat &> /dev/null; then
        if netstat -tln 2>/dev/null | grep -q ":${DB_PORT} "; then
            port_in_use=true
        fi
    fi

    if $port_in_use; then
        print_error "Port ${DB_PORT} is already in use"
        echo "Please free port ${DB_PORT} or stop the service using it:"
        echo "  - Check what's using it: lsof -i :${DB_PORT} or ss -tlnp | grep ${DB_PORT}"
        echo "  - If it's another PostgreSQL instance, stop it first"
        exit 1
    fi
}

# Create new PostgreSQL container
create_container() {
    if ! docker run -d \
        --name "${CONTAINER_NAME}" \
        -e POSTGRES_DB="${DB_NAME}" \
        -e POSTGRES_USER="${DB_USER}" \
        -e POSTGRES_PASSWORD="${DB_PASSWORD}" \
        -p "127.0.0.1:${DB_PORT}:5432" \
        "postgres:${POSTGRES_VERSION}" \
        > /dev/null 2>&1; then

        # Clean up failed container if it was created
        docker rm "${CONTAINER_NAME}" &> /dev/null || true

        print_error "Failed to create PostgreSQL container"
        echo "This usually means port ${DB_PORT} is already in use."
        echo "Check what's using it: lsof -i :${DB_PORT} or ss -tlnp | grep ${DB_PORT}"
        exit 1
    fi

    print_success "Created new PostgreSQL container"
}

# Wait for PostgreSQL to be ready
wait_for_postgres() {
    echo -n "Waiting for PostgreSQL to be ready"

    local max_attempts=30
    local attempt=1

    while [ $attempt -le $max_attempts ]; do
        if docker exec "${CONTAINER_NAME}" pg_isready -U "${DB_USER}" -d "${DB_NAME}" &> /dev/null; then
            echo ""
            print_success "PostgreSQL is ready"
            return 0
        fi

        echo -n "."
        sleep 1
        attempt=$((attempt + 1))
    done

    echo ""
    print_error "PostgreSQL failed to become ready after ${max_attempts} seconds"
    exit 1
}

# Print connection information
print_connection_info() {
    echo ""
    echo "Connection: postgresql://${DB_USER}:${DB_PASSWORD}@localhost:${DB_PORT}/${DB_NAME}"
    echo ""
    echo "Add to your .env:"
    echo "  PG_HOST=localhost"
    echo "  PG_PORT=${DB_PORT}"
    echo "  PG_DATABASE=${DB_NAME}"
    echo "  PG_USER=${DB_USER}"
    echo "  PG_PASSWORD=${DB_PASSWORD}"
    echo ""
}

# Main execution
main() {
    check_docker
    remove_existing_container
    check_port_available
    create_container
    wait_for_postgres
    print_connection_info
}

main
