#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PG_ROOT="/home/botseller/.postgresql"
PG_BIN="$PG_ROOT/usr/lib/postgresql/16/bin"
PG_LIB="$PG_ROOT/usr/lib/postgresql/16/lib"
PG_DATA="$PG_ROOT/data"
LOG_FILE="$PG_ROOT/postgres.log"
SOCKET_DIR="$PG_ROOT/run"

mkdir -p "$SOCKET_DIR"

export PATH="$PG_BIN:$PATH"
export LD_LIBRARY_PATH="$PG_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

pg_ctl -D "$PG_DATA" -l "$LOG_FILE" start
