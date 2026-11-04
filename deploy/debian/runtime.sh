export PPGW_SUB_CACHE_DIR=${PPGW_SUB_CACHE_DIR:-/var/lib/paopaogw/subscriptions}
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
prepare_core_reload() {
    # Publish the target before refreshing /tmp, so the controller cannot race
    # this operation and rebuild the subscription on its next polling cycle.
    ppgw -ini-export /www/ppgw.ini >/tmp/ppgw-core-target.env || return 1
    set -- $(md5sum /tmp/ppgw-core-target.env)
    printf '%s\n' "$1" >/tmp/ppgw-core-only.hash.new || return 1
    mv /tmp/ppgw-core-only.hash.new /tmp/ppgw-core-only.hash || return 1
    rm -f /tmp/ppgw-core-target.env
}
accept_core_reload() {
    [ -f /tmp/ppgw-core-only.hash ] || return 1
    read -r core_only_hash </tmp/ppgw-core-only.hash
    [ "$core_only_hash" != pending ] || return 2
    [ "$core_only_hash" = "$hash" ] || return 1
    # A pending HUP is not evidence that the new configuration was accepted.
    ppgw -ini-export "$SNIFFBOX_SNAPSHOT" >/tmp/ppgw-core-loaded.env || return 2
    set -- $(md5sum /tmp/ppgw-core-loaded.env)
    rm -f /tmp/ppgw-core-loaded.env
    [ "$1" = "$hash" ] || return 2
    last_hash=$hash
    rm -f /tmp/ppgw-core-only.hash
    return 0
}
