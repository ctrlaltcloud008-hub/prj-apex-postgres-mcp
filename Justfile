# pg-mcp task runner. All targets wrap uv.

default:
    @just --list

# Resolve/refresh the lockfile.
lock:
    uv lock

# Install dependencies from the lockfile.
sync:
    uv sync

# Run the server over stdio (local). Pass extra args after `--`, e.g. -- --config config.yaml
run-stdio *ARGS:
    uv run pg-mcp --transport stdio {{ARGS}}

# Run the server over Streamable HTTP (containerised/shared).
run-http *ARGS:
    uv run pg-mcp --transport http {{ARGS}}

# Lint + format check.
lint:
    uv run ruff check .
    uv run ruff format --check .

# Run the test suite (unit always; integration when Docker is available).
test *ARGS:
    uv run pytest {{ARGS}}

# Validate a config file's schema and secret resolution without starting the server.
validate-config PATH:
    uv run pg-mcp validate-config {{PATH}}

# Start + seed the sample Postgres (docker-compose) and wait until ready.
sample-up:
    docker compose up -d --wait
    @echo "Sample DB ready on localhost:5432 (db=appdb, ro user=mcp_readonly)."
    @echo "Run:  cp .env.sample .env  &&  just run-stdio -- --config config.sample.yaml"

# Stop and wipe the sample Postgres.
sample-down:
    docker compose down -v

# Open a psql shell into the sample DB (as admin).
sample-psql:
    docker compose exec sample-db psql -U postgres -d appdb

# Build the container image.
docker-build:
    docker build -t pg-mcp:latest .

# Run the container over HTTP, attached to the sample DB's docker network.
# Requires `just sample-up` and `just docker-build` first. Serves on localhost:8097.
docker-run PORT="8097" NETWORK="prj-apex-postgres-mcp_default":
    docker run --rm -p {{PORT}}:{{PORT}} \
        --network {{NETWORK}} \
        -v {{justfile_directory()}}/config.docker.yaml:/etc/pg-mcp/config.yaml:ro \
        -v {{justfile_directory()}}/.env:/etc/pg-mcp/.env:ro \
        -e CONFIG_PATH=/etc/pg-mcp/config.yaml \
        -e ENV_FILE=/etc/pg-mcp/.env \
        -e MCP_PORT={{PORT}} \
        pg-mcp:latest

# Full local stack in Docker: start+seed DB, build image, run MCP attached to its network.
docker-up: sample-up docker-build docker-run
