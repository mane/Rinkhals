# Shared process identity checks for the launcher and supervisor.
MOONRAKER_PIDFILE=/tmp/rinkhals/moonraker-supervisor.pid

moonraker_process_identity() {
    local pid="$1" stat fields
    case "$pid" in ''|*[!0-9]*) return 1 ;; esac
    read -r stat 2>/dev/null < "/proc/$pid/stat" || return 1
    # Strip pid and comm (which may contain spaces/parentheses). The remaining
    # field 20 is /proc starttime, so a recycled PID cannot match an old owner.
    fields=${stat##*) }
    set -- $fields
    case "$1" in Z|X) return 1 ;; esac
    [ "$#" -ge 20 ] || return 1
    shift 19
    printf '%s\n' "$1"
}

moonraker_process_matches() {
    local pid="$1" kind="$2" expected relative cwd
    if [ "$kind" = supervisor ]; then
        expected="$APP_ROOT/moonraker.sh"
        relative=moonraker.sh
    else
        expected="$APP_ROOT/moonraker/moonraker/moonraker.py"
        relative=moonraker/moonraker/moonraker.py
    fi
    cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null)
    tr '\000' '\n' 2>/dev/null < "/proc/$pid/cmdline" | awk \
        -v kind="$kind" -v expected="$expected" -v relative="$relative" \
        -v cwd="$cwd" -v root="$APP_ROOT" '
        NR == 1 {
            if (kind == "supervisor") interpreter = ($0 ~ /(^|\/)(sh|ash|bash)$/)
            else interpreter = ($0 ~ /(^|\/)python([0-9.]+)?$/)
        }
        NR == 2 {
            script = ($0 == expected ||
                (cwd == root && ($0 == relative || $0 == "./" relative)))
        }
        END { exit !(interpreter && script) }
    '
}

moonraker_find_processes() {
    local kind="$1" entry pid identity
    # This also discovers supervisors launched before pidfile tracking existed.
    for entry in /proc/[0-9]*/cmdline; do
        pid=${entry#/proc/}
        pid=${pid%/cmdline}
        moonraker_process_matches "$pid" "$kind" || continue
        identity=$(moonraker_process_identity "$pid") || continue
        printf '%s %s\n' "$pid" "$identity"
    done
}

moonraker_same_process() {
    [ -n "$2" ] && [ "$(moonraker_process_identity "$1")" = "$2" ]
}

moonraker_terminate() {
    local pid="$1" identity="$2" timeout="${3:-10}" elapsed=0
    moonraker_same_process "$pid" "$identity" || return 0
    kill -TERM "$pid" 2>/dev/null || {
        moonraker_same_process "$pid" "$identity" && return 1
        return 0
    }
    while moonraker_same_process "$pid" "$identity"; do
        [ "$elapsed" -lt "$timeout" ] || break
        sleep 1
        elapsed=$((elapsed + 1))
    done
    if moonraker_same_process "$pid" "$identity"; then
        kill -KILL "$pid" 2>/dev/null || {
            moonraker_same_process "$pid" "$identity" && return 1
            return 0
        }
        elapsed=0
        while moonraker_same_process "$pid" "$identity"; do
            [ "$elapsed" -lt 5 ] || return 1
            sleep 1
            elapsed=$((elapsed + 1))
        done
    fi
}
