#!/usr/bin/env bash
# K9X Arena — build and run helper (single container, no pod needed)
# Run from any directory on the Podman host (the script uses sudo itself).
#
# Commands:
#   build   — build the k9x-arena container image
#   start   — start the container (port 8111)
#   stop    — stop the container
#   logs    — tail logs (pre-flight results appear here first)
#   all     — build + start

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

IMAGE="k9x-arena:latest"
CONTAINER="k9x-arena"
PORT=8111
# Match history, the router's database and uploaded suites live here.
RUNTIME_HOST_DIR="${HOME}/containers/volumes/k9x-arena/runtime"

cmd="${1:-help}"

case "$cmd" in

  build)
    echo "Building $IMAGE (context: $PROJECT_DIR) ..."
    cd "$PROJECT_DIR"
    sudo podman build -t "$IMAGE" -f ubuntu/Containerfile .
    echo "Build complete: $IMAGE"
    ;;

  start)
    ENV_FILE="$PROJECT_DIR/.env"
    [[ -f "$ENV_FILE" ]] || { echo "Error: $ENV_FILE not found. Copy .env.example to .env and edit it."; exit 1; }
    sudo mkdir -p "$RUNTIME_HOST_DIR"
    # The container runs as UID 1001 (not root), so it must be able to write here.
    sudo chown -R 1001:0 "$RUNTIME_HOST_DIR"
    echo "Starting $CONTAINER on port $PORT ..."
    sudo podman rm -f "$CONTAINER" 2>/dev/null || true
    sudo podman run -d \
      --name "$CONTAINER" \
      --restart=always \
      -p "$PORT:8111" \
      -v "$RUNTIME_HOST_DIR":/app/runtime:Z \
      --env-file "$ENV_FILE" \
      -e ARENA_DB_PATH=/app/runtime/arena.db \
      "$IMAGE"
    echo ""
    HOST_IP=$(hostname -I | awk '{print $1}')
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  K9X Arena"
    echo "  Web UI:  http://${HOST_IP}:${PORT}/"
    echo "  Health:  http://${HOST_IP}:${PORT}/api/health"
    echo "  Check the pre-flight: $0 logs"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    ;;

  stop)
    echo "Stopping $CONTAINER ..."
    sudo podman stop "$CONTAINER" 2>/dev/null || true
    echo "Stopped."
    ;;

  logs)
    sudo podman logs -f "$CONTAINER"
    ;;

  all)
    "$0" build
    "$0" start
    ;;

  help|*)
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  build   — build the Podman image ($IMAGE)"
    echo "  start   — start the container (port $PORT)"
    echo "  stop    — stop the container"
    echo "  logs    — tail logs (pre-flight results appear first)"
    echo "  all     — build + start"
    ;;

esac
