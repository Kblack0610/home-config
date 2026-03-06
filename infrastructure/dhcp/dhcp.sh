#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# dhcp — DHCP Static Lease Manager for OpenWRT
# =============================================================================
# Manages a YAML device inventory and syncs static DHCP leases to OpenWRT
# via SSH + UCI. Single source-of-truth for all static IP assignments.
# =============================================================================

# Resolve symlinks to find real script directory
SCRIPT_PATH="${BASH_SOURCE[0]}"
while [[ -L "$SCRIPT_PATH" ]]; do
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
    SCRIPT_PATH="$(readlink "$SCRIPT_PATH")"
    [[ "$SCRIPT_PATH" != /* ]] && SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_PATH"
done
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"

# =============================================================================
# Colors & logging
# =============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC} $*" >&2; }
log_success() { echo -e "${GREEN}[OK]${NC} $*" >&2; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_warning() { echo -e "${YELLOW}[WARN]${NC} $*" >&2; }

# =============================================================================
# Configuration
# =============================================================================

DEVICES_FILE="${SCRIPT_DIR}/devices.yaml"
BACKUP_DIR="${SCRIPT_DIR}/backups"
FORCE=false

# =============================================================================
# YAML helpers (python3 + jq)
# =============================================================================

yaml_to_json() {
    python3 -c "import yaml, json, sys; json.dump(yaml.safe_load(sys.stdin), sys.stdout)" < "$DEVICES_FILE"
}

get_router_host() {
    yaml_to_json | jq -r '.network.router_host'
}

get_router_user() {
    yaml_to_json | jq -r '.network.router_user'
}

get_devices_json() {
    yaml_to_json | jq -c '.devices // [] | map(select(.managed != false))'
}

get_all_devices_json() {
    yaml_to_json | jq -c '.devices // []'
}

get_ranges_json() {
    yaml_to_json | jq -c '.ranges // {}'
}

# Extract last octet from IP
last_octet() {
    echo "${1##*.}"
}

# =============================================================================
# SSH helper
# =============================================================================

router_ssh() {
    local host user
    host=$(get_router_host)
    user=$(get_router_user)
    ssh -o ConnectTimeout=5 -o BatchMode=yes "${user}@${host}" "$@"
}

router_ssh_stdin() {
    local host user
    host=$(get_router_host)
    user=$(get_router_user)
    ssh -o ConnectTimeout=5 -o BatchMode=yes "${user}@${host}" sh
}

check_ssh() {
    if ! router_ssh "echo ok" &>/dev/null; then
        log_error "Cannot SSH to router ($(get_router_user)@$(get_router_host))"
        log_error "Set up SSH key auth: ssh-copy-id $(get_router_user)@$(get_router_host)"
        return 1
    fi
}

# =============================================================================
# Router state helpers
# =============================================================================

# Get current static hosts from router as JSON array:
# [{"name":"foo","mac":"AA:BB:CC:DD:EE:FF","ip":"192.168.1.10","uci_id":"cfg0a1b2c"}, ...]
get_router_hosts() {
    router_ssh "uci show dhcp" 2>/dev/null | awk '
    # Match both positional (@host[N]) and named (cfgXXXXXX) host entries
    # Lines like: dhcp.@host[0]=host  or  dhcp.cfg0a1b2c=host
    /^dhcp\..+=host$/ {
        # Extract the ID portion between dhcp. and =host
        id = $0
        sub(/^dhcp\./, "", id)
        sub(/=host$/, "", id)
        is_host[id] = 1
    }
    /^dhcp\..+\.name=/ {
        id = $0; sub(/^dhcp\./, "", id); sub(/\.name=.*/, "", id)
        val = $0; sub(/^[^=]+=/, "", val); gsub(/\047/, "", val)
        names[id] = val
    }
    /^dhcp\..+\.mac=/ {
        id = $0; sub(/^dhcp\./, "", id); sub(/\.mac=.*/, "", id)
        val = $0; sub(/^[^=]+=/, "", val); gsub(/\047/, "", val)
        macs[id] = toupper(val)
    }
    /^dhcp\..+\.ip=/ {
        id = $0; sub(/^dhcp\./, "", id); sub(/\.ip=.*/, "", id)
        val = $0; sub(/^[^=]+=/, "", val); gsub(/\047/, "", val)
        ips[id] = val
    }
    END {
        sep = ""
        printf "["
        for (id in is_host) {
            if (macs[id] != "") {
                printf "%s{\"name\":\"%s\",\"mac\":\"%s\",\"ip\":\"%s\",\"uci_id\":\"%s\"}",
                    sep, names[id], macs[id], ips[id], id
                sep = ","
            }
        }
        printf "]"
    }
    '
}

# Get active DHCP leases from router as JSON array:
# [{"mac":"AA:BB:CC:DD:EE:FF","ip":"192.168.1.50","hostname":"phone","expires":"12345"}, ...]
get_router_leases() {
    router_ssh "cat /tmp/dhcp.leases" 2>/dev/null | awk '
    {
        # Format: expire_ts mac ip hostname client_id
        printf "%s{\"expires\":\"%s\",\"mac\":\"%s\",\"ip\":\"%s\",\"hostname\":\"%s\"}",
            (NR>1 ? "," : ""), $1, toupper($2), $3, $4
    }
    BEGIN { printf "[" }
    END { printf "]" }
    '
}

# =============================================================================
# Category detection
# =============================================================================

categorize_ip() {
    local ip="$1"
    local octet
    octet=$(last_octet "$ip")
    local ranges
    ranges=$(get_ranges_json)

    echo "$ranges" | jq -r --argjson o "$octet" '
        to_entries[] |
        select(.value.start <= $o and $o <= .value.end) |
        .key
    ' | head -1
}

# =============================================================================
# Commands
# =============================================================================

# --- bootstrap ---------------------------------------------------------------
# Pull current static leases from OpenWRT and populate devices.yaml

cmd_bootstrap() {
    log_info "Bootstrapping devices.yaml from router..."
    check_ssh

    local router_hosts
    router_hosts=$(get_router_hosts)

    local count
    count=$(echo "$router_hosts" | jq 'length')
    log_info "Found ${count} static host entries on router"

    if [[ "$count" -eq 0 ]]; then
        log_warning "No static hosts found on router"
        return 0
    fi

    local result
    result=$(echo "$router_hosts" | python3 -c "
import yaml, json, sys

devices_file = '${DEVICES_FILE}'
router_hosts = json.loads(sys.stdin.read())

with open(devices_file) as f:
    data = yaml.safe_load(f)

existing = data.get('devices', []) or []

# Index existing devices by MAC and hostname
by_mac = {}
by_hostname = {}
for d in existing:
    mac = (d.get('mac') or '').upper().strip()
    if mac:
        by_mac[mac] = d
    hostname = (d.get('hostname') or '').lower().strip()
    if hostname:
        by_hostname[hostname] = d

ranges = data.get('ranges', {})

def categorize(ip):
    octet = int(ip.split('.')[-1])
    for cat, r in ranges.items():
        if r['start'] <= octet <= r['end']:
            return cat
    return 'dynamic'

def in_range(ip, category):
    octet = int(ip.split('.')[-1])
    r = ranges.get(category, {})
    return r.get('start', 0) <= octet <= r.get('end', 0)

added = 0
updated = 0

for host in router_hosts:
    mac = host['mac'].upper()
    name = (host.get('name') or '').lower()
    ip = host.get('ip', '')

    if mac in by_mac:
        entry = by_mac[mac]
        changed = False
        if not entry.get('ip') and ip:
            entry['ip'] = ip
            changed = True
        if not entry.get('mac'):
            entry['mac'] = mac
            changed = True
        if changed:
            updated += 1
        continue

    if name and name in by_hostname:
        entry = by_hostname[name]
        entry['mac'] = mac
        if not entry.get('ip') and ip:
            entry['ip'] = ip
        updated += 1
        continue

    category = categorize(ip) if ip else 'dynamic'
    entry = {
        'hostname': name or 'unknown-' + mac.replace(':', '')[-6:].lower(),
        'mac': mac,
        'ip': ip,
        'category': category,
    }
    if ip and not in_range(ip, category):
        entry['range_exception'] = True
    existing.append(entry)
    by_mac[mac] = entry
    added += 1

data['devices'] = existing

with open(devices_file, 'w') as f:
    yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

print(f'Added {added} new devices, updated {updated} existing ({len(existing)} total)')
")

    log_success "$result"
}

# --- validate ----------------------------------------------------------------
# Check devices.yaml for errors

cmd_validate() {
    log_info "Validating devices.yaml..."

    if [[ ! -f "$DEVICES_FILE" ]]; then
        log_error "devices.yaml not found at $DEVICES_FILE"
        return 1
    fi

    # Run all checks via python for robustness
    yaml_to_json | python3 -c "
import json, sys, re

data = json.load(sys.stdin)
devices = data.get('devices', []) or []
ranges = data.get('ranges', {})

errors = 0
warnings = 0
seen_macs = {}
seen_ips = {}
mac_re = re.compile(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$')
ip_re = re.compile(r'^192\.168\.1\.(\d+)$')

for i, d in enumerate(devices):
    name = d.get('hostname', f'device[{i}]')
    mac = (d.get('mac') or '').upper().strip()
    ip = d.get('ip', '')
    category = d.get('category', '')
    managed = d.get('managed', True)
    range_exception = d.get('range_exception', False)

    # Skip unmanaged devices (like the router itself)
    if not managed:
        continue

    # Check MAC format
    if mac:
        if not mac_re.match(mac):
            print(f'\033[0;31m  ERROR\033[0m  {name}: invalid MAC format \"{mac}\"')
            errors += 1
        elif mac in seen_macs:
            print(f'\033[0;31m  ERROR\033[0m  {name}: duplicate MAC {mac} (also on {seen_macs[mac]})')
            errors += 1
        else:
            seen_macs[mac] = name
    else:
        print(f'\033[0;33m  WARN\033[0m   {name}: missing MAC address')
        warnings += 1

    # Check IP format and duplicates
    if ip:
        m = ip_re.match(ip)
        if not m:
            print(f'\033[0;31m  ERROR\033[0m  {name}: invalid IP \"{ip}\" (expected 192.168.1.x)')
            errors += 1
        elif ip in seen_ips:
            print(f'\033[0;31m  ERROR\033[0m  {name}: duplicate IP {ip} (also on {seen_ips[ip]})')
            errors += 1
        else:
            seen_ips[ip] = name
            # Check range
            octet = int(m.group(1))
            if category and category in ranges and not range_exception:
                r = ranges[category]
                if not (r['start'] <= octet <= r['end']):
                    print(f'\033[0;33m  WARN\033[0m   {name}: IP .{octet} outside {category} range (.{r[\"start\"]}-.{r[\"end\"]})')
                    warnings += 1
    else:
        print(f'\033[0;33m  WARN\033[0m   {name}: missing IP address')
        warnings += 1

    # Check category
    if category and category not in ranges and category != 'dynamic':
        print(f'\033[0;33m  WARN\033[0m   {name}: unknown category \"{category}\"')
        warnings += 1

if errors == 0 and warnings == 0:
    print('\033[0;32m  All checks passed\033[0m')

print()
print(f'  {len(devices)} devices | {errors} errors | {warnings} warnings')
sys.exit(1 if errors > 0 else 0)
"
    return $?
}

# --- status ------------------------------------------------------------------
# Summary of managed vs actual state

cmd_status() {
    log_info "Checking DHCP status..."

    # Local inventory stats
    local devices
    devices=$(get_all_devices_json)

    local total managed_count
    total=$(echo "$devices" | jq 'length')
    managed_count=$(echo "$devices" | jq '[.[] | select(.managed != false)] | length')
    local with_mac
    with_mac=$(echo "$devices" | jq '[.[] | select(.managed != false and .mac != "" and .mac != null)] | length')

    echo ""
    echo -e " ${BOLD}DHCP Inventory Status${NC}"
    echo -e " ${DIM}────────────────────────────────────────────────────${NC}"
    echo -e "  Devices in inventory:  ${BOLD}${total}${NC}"
    echo -e "  Managed (syncable):    ${BOLD}${managed_count}${NC}"
    echo -e "  With MAC address:      ${BOLD}${with_mac}${NC}"

    # Category breakdown
    echo ""
    echo -e " ${BOLD}By Category${NC}"
    echo -e " ${DIM}────────────────────────────────────────────────────${NC}"
    echo "$devices" | jq -r '
        group_by(.category) | .[] |
        "  \(.[0].category // "uncategorized"): \(length)"
    '

    # Router comparison (if reachable)
    if router_ssh "echo ok" &>/dev/null; then
        local router_hosts router_count
        router_hosts=$(get_router_hosts)
        router_count=$(echo "$router_hosts" | jq 'length')

        echo ""
        echo -e " ${BOLD}Router State${NC} ($(get_router_host))"
        echo -e " ${DIM}────────────────────────────────────────────────────${NC}"
        echo -e "  Static leases on router: ${BOLD}${router_count}${NC}"
        echo -e "  Syncable in inventory:   ${BOLD}${with_mac}${NC}"

        # Quick sync status
        local diff_summary
        diff_summary=$(compute_diff "$devices" "$router_hosts" 2>/dev/null || echo "error")
        if [[ "$diff_summary" != "error" ]]; then
            local adds updates removes
            adds=$(echo "$diff_summary" | jq '.adds | length')
            updates=$(echo "$diff_summary" | jq '.updates | length')
            removes=$(echo "$diff_summary" | jq '.removes | length')

            if [[ "$adds" -eq 0 && "$updates" -eq 0 && "$removes" -eq 0 ]]; then
                echo -e "  Sync status:             ${GREEN}${BOLD}in sync${NC}"
            else
                echo -e "  Sync status:             ${YELLOW}${BOLD}${adds} to add, ${updates} to update, ${removes} to remove${NC}"
            fi
        fi
    else
        echo ""
        echo -e " ${YELLOW}Router unreachable — skipping remote comparison${NC}"
    fi
    echo ""
}

# --- discover ----------------------------------------------------------------
# Show active DHCP leases not in the inventory

cmd_discover() {
    log_info "Discovering unregistered devices..."
    check_ssh

    local leases devices
    leases=$(get_router_leases)
    devices=$(get_all_devices_json)

    local lease_count
    lease_count=$(echo "$leases" | jq 'length')
    log_info "Found ${lease_count} active DHCP leases"

    # Find leases whose MAC is not in the inventory
    jq -n --argjson leases "$leases" --argjson devices "$devices" \
        '{"leases": $leases, "devices": $devices}' | python3 -c "
import json, sys

data = json.load(sys.stdin)
leases = data['leases']
devices = data['devices']

known_macs = set()
for d in devices:
    mac = (d.get('mac') or '').upper().strip()
    if mac:
        known_macs.add(mac)

unknown = []
for lease in leases:
    mac = lease.get('mac', '').upper()
    if mac and mac not in known_macs:
        unknown.append(lease)

if not unknown:
    print('\033[0;32m  All active leases are in the inventory\033[0m')
    print()
    sys.exit(0)

print()
print(f'  \033[1m{len(unknown)} unregistered device(s):\033[0m')
print(f'  \033[2m{\"─\" * 60}\033[0m')
print(f'  \033[1m{\"MAC\":<20s} {\"IP\":<18s} {\"HOSTNAME\":<20s}\033[0m')

for u in sorted(unknown, key=lambda x: [int(o) for o in x['ip'].split('.')]):
    print(f'  {u[\"mac\"]:<20s} {u[\"ip\"]:<18s} {u.get(\"hostname\", \"*\"):<20s}')
print()
print(f'  Add these to devices.yaml and run \033[1mdhcp sync\033[0m to assign static leases.')
print()
"
}

# --- diff --------------------------------------------------------------------
# Dry-run: show what sync would change

compute_diff() {
    local devices="$1"
    local router_hosts="$2"

    python3 -c "
import json, sys

devices = json.loads(sys.argv[1])
router_hosts = json.loads(sys.argv[2])

# Only consider managed devices with both MAC and IP
syncable = []
for d in devices:
    if d.get('managed') == False:
        continue
    mac = (d.get('mac') or '').upper().strip()
    ip = d.get('ip', '')
    if mac and ip:
        syncable.append({
            'hostname': d.get('hostname', ''),
            'mac': mac,
            'ip': ip,
        })

# Index router hosts by MAC
router_by_mac = {}
for h in router_hosts:
    mac = h.get('mac', '').upper()
    if mac:
        router_by_mac[mac] = h

# Index desired by MAC
desired_by_mac = {}
for d in syncable:
    desired_by_mac[d['mac']] = d

adds = []
updates = []
removes = []

# Devices to add or update
for d in syncable:
    mac = d['mac']
    if mac not in router_by_mac:
        adds.append(d)
    else:
        r = router_by_mac[mac]
        if r.get('ip') != d['ip'] or r.get('name', '').lower() != d['hostname'].lower():
            updates.append({
                'desired': d,
                'current': r,
                'uci_id': r.get('uci_id', ''),
            })

# Router hosts not in desired state -> remove
for mac, r in router_by_mac.items():
    if mac not in desired_by_mac:
        removes.append(r)

result = {'adds': adds, 'updates': updates, 'removes': removes}
print(json.dumps(result))
" "$devices" "$router_hosts"
}

cmd_diff() {
    log_info "Computing diff (dry-run)..."
    check_ssh

    local devices router_hosts
    devices=$(get_all_devices_json)
    router_hosts=$(get_router_hosts)

    local diff_json
    diff_json=$(compute_diff "$devices" "$router_hosts")

    local adds updates removes
    adds=$(echo "$diff_json" | jq '.adds | length')
    updates=$(echo "$diff_json" | jq '.updates | length')
    removes=$(echo "$diff_json" | jq '.removes | length')

    echo ""

    if [[ "$adds" -eq 0 && "$updates" -eq 0 && "$removes" -eq 0 ]]; then
        echo -e " ${GREEN}${BOLD}Already in sync${NC} — no changes needed"
        echo ""
        return 0
    fi

    echo -e " ${BOLD}Planned Changes${NC}"
    echo -e " ${DIM}────────────────────────────────────────────────────${NC}"

    if [[ "$adds" -gt 0 ]]; then
        echo -e " ${GREEN}+ Add ($adds):${NC}"
        echo "$diff_json" | jq -r '.adds[] | "   + \(.hostname)\t\(.mac)\t\(.ip)"'
    fi

    if [[ "$updates" -gt 0 ]]; then
        echo -e " ${YELLOW}~ Update ($updates):${NC}"
        echo "$diff_json" | jq -r '.updates[] | "   ~ \(.desired.hostname)\t\(.desired.mac)\t\(.current.ip) → \(.desired.ip)"'
    fi

    if [[ "$removes" -gt 0 ]]; then
        echo -e " ${RED}- Remove ($removes):${NC}"
        echo "$diff_json" | jq -r '.removes[] | "   - \(.name)\t\(.mac)\t\(.ip)"'
    fi

    echo ""
}

# --- sync --------------------------------------------------------------------
# Apply devices.yaml to OpenWRT (with backup + confirmation)

cmd_sync() {
    log_info "Syncing devices.yaml to router..."
    check_ssh

    local devices router_hosts
    devices=$(get_all_devices_json)
    router_hosts=$(get_router_hosts)

    local diff_json
    diff_json=$(compute_diff "$devices" "$router_hosts")

    local adds updates removes
    adds=$(echo "$diff_json" | jq '.adds | length')
    updates=$(echo "$diff_json" | jq '.updates | length')
    removes=$(echo "$diff_json" | jq '.removes | length')

    if [[ "$adds" -eq 0 && "$updates" -eq 0 && "$removes" -eq 0 ]]; then
        log_success "Already in sync — no changes needed"
        return 0
    fi

    # Show what will change
    echo ""
    echo -e " ${BOLD}Planned Changes${NC}"
    echo -e " ${DIM}────────────────────────────────────────────────────${NC}"
    if [[ "$adds" -gt 0 ]]; then
        echo -e " ${GREEN}+ Add ($adds):${NC}"
        echo "$diff_json" | jq -r '.adds[] | "   + \(.hostname)\t\(.mac)\t\(.ip)"'
    fi
    if [[ "$updates" -gt 0 ]]; then
        echo -e " ${YELLOW}~ Update ($updates):${NC}"
        echo "$diff_json" | jq -r '.updates[] | "   ~ \(.desired.hostname)\t\(.desired.mac)\t\(.current.ip) → \(.desired.ip)"'
    fi
    if [[ "$removes" -gt 0 ]]; then
        echo -e " ${RED}- Remove ($removes):${NC}"
        echo "$diff_json" | jq -r '.removes[] | "   - \(.name)\t\(.mac)\t\(.ip)"'
    fi
    echo ""

    # Confirm unless --force
    if [[ "$FORCE" != true ]]; then
        if [[ "$removes" -gt 0 ]]; then
            echo -e " ${RED}${BOLD}WARNING: This will remove ${removes} device(s) from the router${NC}"
        fi
        read -p "  Apply these changes? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_warning "Aborted"
            return 0
        fi
    fi

    # Backup current config
    mkdir -p "$BACKUP_DIR"
    local backup_file="${BACKUP_DIR}/dhcp-$(date '+%Y%m%d-%H%M%S').uci"
    log_info "Backing up current config to ${backup_file##*/}..."
    router_ssh "uci export dhcp" > "$backup_file" 2>/dev/null
    log_success "Backup saved ($(wc -c < "$backup_file") bytes)"

    # Build UCI commands
    local uci_commands=""

    # Removes (by UCI config ID)
    if [[ "$removes" -gt 0 ]]; then
        while IFS= read -r uci_id; do
            uci_commands+="uci delete dhcp.${uci_id}"$'\n'
        done < <(echo "$diff_json" | jq -r '.removes[].uci_id')
    fi

    # Updates (delete + re-add for safety)
    if [[ "$updates" -gt 0 ]]; then
        while IFS=$'\t' read -r uci_id hostname mac ip; do
            uci_commands+="uci set dhcp.${uci_id}.name='${hostname}'"$'\n'
            uci_commands+="uci set dhcp.${uci_id}.mac='${mac}'"$'\n'
            uci_commands+="uci set dhcp.${uci_id}.ip='${ip}'"$'\n'
        done < <(echo "$diff_json" | jq -r '.updates[] | "\(.uci_id)\t\(.desired.hostname)\t\(.desired.mac)\t\(.desired.ip)"')
    fi

    # Adds
    if [[ "$adds" -gt 0 ]]; then
        while IFS=$'\t' read -r hostname mac ip; do
            uci_commands+="uci add dhcp host"$'\n'
            uci_commands+="uci set dhcp.@host[-1].name='${hostname}'"$'\n'
            uci_commands+="uci set dhcp.@host[-1].mac='${mac}'"$'\n'
            uci_commands+="uci set dhcp.@host[-1].ip='${ip}'"$'\n'
            uci_commands+="uci set dhcp.@host[-1].dns='1'"$'\n'
        done < <(echo "$diff_json" | jq -r '.adds[] | "\(.hostname)\t\(.mac)\t\(.ip)"')
    fi

    uci_commands+="uci commit dhcp"$'\n'
    uci_commands+="/etc/init.d/dnsmasq restart"$'\n'

    # Execute as single SSH session
    log_info "Applying ${adds} adds, ${updates} updates, ${removes} removes..."
    echo "$uci_commands" | router_ssh_stdin

    log_success "Sync complete — dnsmasq restarted"
}

# --- help --------------------------------------------------------------------

cmd_help() {
    cat <<EOF
dhcp — DHCP Static Lease Manager for OpenWRT

USAGE:
    dhcp <command> [options]

COMMANDS:
    bootstrap   Pull current static leases from router → populate devices.yaml
    validate    Check devices.yaml for errors (duplicate MACs/IPs, ranges)
    diff        Dry-run: show what sync would change
    sync        Apply devices.yaml to router (with backup + confirmation)
    discover    Show active DHCP leases not in the inventory
    status      Summary of managed vs actual state
    help        Show this help message

OPTIONS:
    --force     Skip confirmation prompts (for sync)

EXAMPLES:
    dhcp bootstrap          # populate inventory from router
    dhcp validate           # check for errors
    dhcp diff               # preview changes
    dhcp sync               # apply changes (with confirmation)
    dhcp sync --force       # apply changes without confirmation
    dhcp discover           # find unregistered devices
    dhcp status             # overview of inventory vs router

FILES:
    devices.yaml            Device inventory (single source of truth)
    backups/                UCI config snapshots (before each sync)
EOF
}

# =============================================================================
# Main
# =============================================================================

# Parse global flags
while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=true; shift ;;
        -*) log_error "Unknown option: $1"; cmd_help; exit 1 ;;
        *) break ;;
    esac
done

case "${1:-help}" in
    bootstrap)          cmd_bootstrap ;;
    validate)           cmd_validate ;;
    diff)               cmd_diff ;;
    sync)               cmd_sync ;;
    discover)           cmd_discover ;;
    status)             cmd_status ;;
    help|--help|-h)     cmd_help ;;
    *)
        log_error "Unknown command: $1"
        cmd_help
        exit 1
        ;;
esac
