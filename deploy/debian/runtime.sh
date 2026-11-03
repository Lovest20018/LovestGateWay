# Sourced by ppg.sh only under the Debian service mount namespace.
ppgw_owned_pid() {
    # The host can also run containers or other services with identical binaries.
    [ -n "$(cat "/proc/$$/cgroup" 2>/dev/null)" ] && [ -r "/proc/$1/cgroup" ] || return 1
    cmp -s "/proc/$$/cgroup" "/proc/$1/cgroup" || return 1
    if [ "$2" = sniffbox ]; then
        # ppgw is a multicall symlink to sniffbox; helpers are not the server.
        [ -z "$(tr '\0' '\n' < "/proc/$1/cmdline" 2>/dev/null | sed -n '2p')" ] || return 1
    fi
}
pidof() {
    owned_pids=""
    for candidate in $(command pidof "$@"); do
        comm=$(cat "/proc/$candidate/comm" 2>/dev/null)
        if ppgw_owned_pid "$candidate" "$comm"; then owned_pids="${owned_pids:+$owned_pids }$candidate"; fi
    done
    [ -n "$owned_pids" ] || return 1
    printf '%s\n' "$owned_pids"
}
net_ready() {
    ip link show dev "$PPGW_INTERFACE" >/dev/null 2>&1 || exit 1
    eth0ip=$(ip -4 -o addr show dev "$PPGW_INTERFACE" scope global | awk '{print $4}' | cut -d/ -f1 | head -1)
    [ -n "$eth0ip" ] || { log "No IPv4 on configured interface" warn; exit 1; }
    export eth0ip
    gw=$(ip -4 route show default dev "$PPGW_INTERFACE" | awk '{print $3}' | head -1)
    export gw
}
sync_ntp() { :; } # Use Debian's configured clock service.
hot_reload_box() {
    pid=$(pidof sniffbox)
    if [ -n "$pid" ]; then kill -HUP "$pid"; else load_box; fi
}
