# Shared by the web backend, touch UI and shell installers. Never steal a lock
# from a running update. /tmp clears abandoned locks when the printer reboots.
RINKHALS_UPDATE_LOCK_DIR=/tmp/rinkhals-update.lock
RINKHALS_UPDATE_LOCK_OWNED=0

acquire_update_lock() {
    if [ -n "${RINKHALS_UPDATE_LOCK_TOKEN:-}" ] &&
       [ "$(cat "$RINKHALS_UPDATE_LOCK_DIR/owner" 2>/dev/null)" = "$RINKHALS_UPDATE_LOCK_TOKEN" ]; then
        return 0
    fi
    if ! mkdir -m 700 "$RINKHALS_UPDATE_LOCK_DIR" 2>/dev/null; then
        echo "Another update or maintenance operation is in progress" >&2
        return 1
    fi
    RINKHALS_UPDATE_LOCK_TOKEN=$(cat /proc/sys/kernel/random/uuid 2>/dev/null)
    RINKHALS_UPDATE_LOCK_TOKEN=${RINKHALS_UPDATE_LOCK_TOKEN:-"$$-$(date +%s)-${RANDOM:-0}"}
    if ! printf '%s\n' "$RINKHALS_UPDATE_LOCK_TOKEN" > "$RINKHALS_UPDATE_LOCK_DIR/owner"; then
        rmdir "$RINKHALS_UPDATE_LOCK_DIR" 2>/dev/null
        return 1
    fi
    RINKHALS_UPDATE_LOCK_OWNED=1
    export RINKHALS_UPDATE_LOCK_TOKEN
}

release_update_lock() {
    if [ "$RINKHALS_UPDATE_LOCK_OWNED" = 1 ] &&
       [ "$(cat "$RINKHALS_UPDATE_LOCK_DIR/owner" 2>/dev/null)" = "$RINKHALS_UPDATE_LOCK_TOKEN" ]; then
        rm -f "$RINKHALS_UPDATE_LOCK_DIR/owner"
        rmdir "$RINKHALS_UPDATE_LOCK_DIR"
        RINKHALS_UPDATE_LOCK_OWNED=0
    fi
}
