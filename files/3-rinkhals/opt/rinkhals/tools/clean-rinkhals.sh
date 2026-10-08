#!/bin/sh

. "$(dirname "$(realpath "$0")")/update-lock.sh"
acquire_update_lock || exit 1
trap release_update_lock EXIT
trap 'exit 130' INT
trap 'exit 143' TERM



RINKHALS_PATH="/useremain/rinkhals"
RINKHALS_CURRENT=$(realpath /useremain/rinkhals/.current)

# Keep the active install and two other versions. Ignore unrelated folders.
installs=$(find "$RINKHALS_PATH" -mindepth 1 -maxdepth 1 -type d ! -type l | sort -rV)
kept=0
for i in $installs; do
    basename=$(basename "$i")
    [ "$i" = "$RINKHALS_CURRENT" ] && continue
    echo "$basename" | grep -qE '^20[0-9]{6}_[0-9]+(_test)?(-[0-9]+)?$' || continue
    if [ "$kept" -lt 2 ]; then
        kept=$((kept + 1))
        continue
    fi
    rm -rf "$i"
done

# Flush changes

cd
sync

# Play ok jingle to notify completion
if [ ! -f /useremain/rinkhals/.mute-sounds ]; then
    B=/sys/class/pwm/pwmchip0/pwm0
    SAVED_P=$(cat $B/period 2>/dev/null); SAVED_D=$(cat $B/duty_cycle 2>/dev/null)
    echo 0 > $B/enable; echo 0 > $B/duty_cycle
    echo 2551000 > $B/period; echo 1020400 > $B/duty_cycle; echo 1 > $B/enable
    usleep 120000; echo 0 > $B/enable; usleep 40000
    echo 0 > $B/duty_cycle
    echo 1912000 > $B/period; echo 764800 > $B/duty_cycle; echo 1 > $B/enable
    usleep 120000; echo 0 > $B/enable; usleep 40000
    echo 0 > $B/duty_cycle
    echo 1517000 > $B/period; echo 606800 > $B/duty_cycle; echo 1 > $B/enable
    usleep 180000; echo 0 > $B/enable
    # Restore pwm0 to the state K3SysUi set so the touchscreen key sound keeps working
    echo 0 > $B/duty_cycle; [ -n "$SAVED_P" ] && echo $SAVED_P > $B/period; [ -n "$SAVED_D" ] && echo $SAVED_D > $B/duty_cycle
fi
