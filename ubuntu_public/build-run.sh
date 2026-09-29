#!/usr/bin/env bash
# K9X Arena — PUBLIC, read-only instance (arena.k9x.ai).
# Same code as ubuntu/ (the lab), started with ARENA_MODE=public:
#   - shows only matches you publish to it; nothing runs here
#   - refuses every write at the server (matches, uploads, reruns, reviews, deletes)
#   - has no Ollama settings, no admin login, and its own data folder
# Its own image tag, so rebuilding the lab never changes the public site.
#
# Commands:
#   build            — build the public image from the current checkout
#   start            — start the public container (port 8112)
#   stop | logs      — stop it / tail its logs
#   all              — build + start
#   publish ID...    — copy these completed matches from the lab to the public site
#   unpublish ID...  — remove them from the public site
#   list             — show what's published

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

IMAGE="k9x-arena-public:latest"
CONTAINER="k9x-arena-public"
PORT=8112
PUBLIC_DIR="${HOME}/containers/volumes/k9x-arena-public/runtime"
LAB_DIR="${HOME}/containers/volumes/k9x-arena/runtime"     # the lab's data (read-only here)

# One-off container that reads the lab DB read-only and writes the public DB.
publish_tool() {
  sudo mkdir -p "$PUBLIC_DIR"
  sudo chown -R 1001:0 "$PUBLIC_DIR"
  sudo podman run --rm \
    -v "$LAB_DIR":/lab:ro \
    -v "$PUBLIC_DIR":/app/runtime \
    -e ARENA_MODE=public \
    "$IMAGE" python -m arena.publish --from /lab/arena.db --to /app/runtime/arena.db "$@"
}

cmd="${1:-help}"; shift || true

case "$cmd" in
  build)
    echo "Building $IMAGE (context: $PROJECT_DIR) ..."
    cd "$PROJECT_DIR"
    sudo podman build -t "$IMAGE" -f ubuntu/Containerfile .
    echo "Build complete: $IMAGE"
    ;;

  start)
    sudo mkdir -p "$PUBLIC_DIR"
    sudo chown -R 1001:0 "$PUBLIC_DIR"
    echo "Starting $CONTAINER on port $PORT (read-only) ..."
    sudo podman rm -f "$CONTAINER" 2>/dev/null || true
    # Deliberately no --env-file: the public site gets no Ollama host,
    # no model names and no passwords.
    sudo podman run -d \
      --name "$CONTAINER" \
      --restart=always \
      -p "$PORT:8111" \
      -v "$PUBLIC_DIR":/app/runtime \
      -e ARENA_MODE=public \
      -e ARENA_DB_PATH=/app/runtime/arena.db \
      -e K9_ENV=production \
      "$IMAGE"
    HOST_IP=$(hostname -I | awk '{print $1}')
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  K9X Arena (public, read-only)"
    echo "  Local:   http://${HOST_IP}:${PORT}/"
    echo "  Point the Cloudflare tunnel for arena.k9x.ai at this port."
    echo "  Publish matches: $0 publish 10 11"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    ;;

  stop)  sudo podman stop "$CONTAINER" 2>/dev/null || true; echo "Stopped." ;;
  logs)  sudo podman logs -f "$CONTAINER" ;;
  all)   "$0" build; "$0" start ;;

  publish)
    [[ $# -gt 0 ]] || { echo "Usage: $0 publish MATCH_ID [MATCH_ID ...]"; exit 1; }
    publish_tool "$@"
    ;;
  unpublish)
    [[ $# -gt 0 ]] || { echo "Usage: $0 unpublish MATCH_ID [MATCH_ID ...]"; exit 1; }
    publish_tool --remove "$@"
    ;;
  list)
    publish_tool --list
    ;;

  help|*)
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    ;;
esac
