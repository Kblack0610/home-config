#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# openwrt — Declarative OpenWrt manager (safe v1)
# =============================================================================
# Manages a narrow, high-value subset of OpenWrt config from YAML:
#   - DNS settings in the dhcp package
#   - Firewall redirects in the firewall package
#
# Static DHCP host entries remain owned by infrastructure/dhcp/dhcp.sh.
# =============================================================================

SCRIPT_PATH="${BASH_SOURCE[0]}"
while [[ -L "$SCRIPT_PATH" ]]; do
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
    SCRIPT_PATH="$(readlink "$SCRIPT_PATH")"
    [[ "$SCRIPT_PATH" != /* ]] && SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_PATH"
done
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"

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

CONFIG_FILE="${SCRIPT_DIR}/config.yaml"
DNS_FILE="${SCRIPT_DIR}/dns.yaml"
FIREWALL_FILE="${SCRIPT_DIR}/firewall.yaml"
BACKUP_DIR="${SCRIPT_DIR}/backups"

FORCE=false

yaml_file_to_json() {
    local path="$1"
    python3 - "$path" <<'PY'
import json
import sys
import yaml

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as fh:
    data = yaml.safe_load(fh) or {}
json.dump(data, sys.stdout)
PY
}

get_connection_field() {
    local field="$1"
    yaml_file_to_json "$CONFIG_FILE" | jq -r --arg field "$field" '.connection[$field]'
}

router_ssh() {
    local host user ssh_opts
    host=$(get_connection_field host)
    user=$(get_connection_field user)
    ssh_opts=$(yaml_file_to_json "$CONFIG_FILE" | jq -r '.connection.ssh_options // ["-o","ConnectTimeout=5","-o","BatchMode=yes"] | @sh')
    # shellcheck disable=SC2086
    eval ssh ${ssh_opts} "${user}@${host}" "$@"
}

router_ssh_stdin() {
    local host user ssh_opts
    host=$(get_connection_field host)
    user=$(get_connection_field user)
    ssh_opts=$(yaml_file_to_json "$CONFIG_FILE" | jq -r '.connection.ssh_options // ["-o","ConnectTimeout=5","-o","BatchMode=yes"] | @sh')
    # shellcheck disable=SC2086
    eval ssh ${ssh_opts} "${user}@${host}" sh
}

check_dependencies() {
    local missing=()
    command -v python3 >/dev/null 2>&1 || missing+=("python3")
    command -v jq >/dev/null 2>&1 || missing+=("jq")
    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing dependencies: ${missing[*]}"
        return 1
    fi
}

check_ssh() {
    if ! router_ssh "echo ok" &>/dev/null; then
        log_error "Cannot SSH to router ($(get_connection_field user)@$(get_connection_field host))"
        return 1
    fi
}

ensure_files_exist() {
    local missing=0
    for path in "$CONFIG_FILE" "$DNS_FILE" "$FIREWALL_FILE"; do
        if [[ ! -f "$path" ]]; then
            log_error "Required file not found: $path"
            missing=1
        fi
    done
    return "$missing"
}

normalize_dns_yaml() {
    local raw
    raw=$(yaml_file_to_json "$DNS_FILE")
    python3 - "$raw" <<'PY'
import json
import sys

data = json.loads(sys.argv[1])
result = {
    "dnsmasq": {
        "noresolv": bool(((data.get("dnsmasq") or {}).get("noresolv", False))),
        "server": list((data.get("dnsmasq") or {}).get("server", []) or []),
    },
    "lan": {
        "dhcp_option": list((data.get("lan") or {}).get("dhcp_option", []) or []),
    },
}
json.dump(result, sys.stdout, sort_keys=True)
PY
}

normalize_firewall_yaml() {
    local raw
    raw=$(yaml_file_to_json "$FIREWALL_FILE")
    python3 - "$raw" <<'PY'
import json
import sys

data = json.loads(sys.argv[1])
redirects = []
for item in data.get("redirects", []) or []:
    if not item:
        continue
    redirect = {
        "name": item.get("name", ""),
        "src": item.get("src", "wan"),
        "dest": item.get("dest", "lan"),
        "proto": item.get("proto", "tcp"),
        "src_dport": str(item.get("src_dport", "")),
        "dest_ip": item.get("dest_ip", ""),
        "dest_port": str(item.get("dest_port", "")),
        "enabled": bool(item.get("enabled", True)),
    }
    if item.get("reflection") is not None:
        redirect["reflection"] = bool(item.get("reflection"))
    redirects.append(redirect)
result = {"prune": bool(data.get("prune", False)), "redirects": redirects}
json.dump(result, sys.stdout, sort_keys=True)
PY
}

get_dns_current_json() {
    local raw
    raw=$(router_ssh "uci show dhcp" 2>/dev/null)
    python3 - "$raw" <<'PY'
import json
import re
import sys

dnsmasq = {"noresolv": False, "server": []}
lan_dns_options = []
other_lan_options = []

for raw_line in sys.argv[1].splitlines():
    line = raw_line.strip()
    if not line or "=" not in line:
        continue
    key, value = line.split("=", 1)
    value = value.strip().strip("'")
    if key == "dhcp.@dnsmasq[0].noresolv":
        dnsmasq["noresolv"] = value in {"1", "true", "on", "yes"}
    elif key == "dhcp.@dnsmasq[0].server":
        dnsmasq["server"].append(value)
    elif key == "dhcp.lan.dhcp_option":
        if re.match(r"^6,", value):
            lan_dns_options.append(value)
        else:
            other_lan_options.append(value)

result = {
    "dnsmasq": dnsmasq,
    "lan": {
        "dhcp_option": lan_dns_options,
        "other_dhcp_option": other_lan_options,
    },
}
json.dump(result, sys.stdout, sort_keys=True)
PY
}

get_firewall_current_json() {
    local raw
    raw=$(router_ssh "uci show firewall" 2>/dev/null)
    python3 - "$raw" <<'PY'
import json
import sys

sections = {}
section_order = []

for raw_line in sys.argv[1].splitlines():
    line = raw_line.strip()
    if not line or "=" not in line:
        continue
    key, value = line.split("=", 1)
    value = value.strip().strip("'")
    if key.endswith("=redirect"):
        continue
    if not key.startswith("firewall."):
        continue
    if key.count(".") < 2:
        continue
    section_id = key.split(".", 1)[1].split(".", 1)[0]
    option = key.rsplit(".", 1)[1]
    if section_id not in sections:
        sections[section_id] = {"uci_id": section_id}
        section_order.append(section_id)
    sections[section_id][option] = value

redirects = []
for section_id in section_order:
    item = sections[section_id]
    if item.get("name") or item.get("src_dport") or item.get("dest_ip"):
        if section_id.startswith("@redirect[") or item.get("target") == "DNAT" or item.get("src_dport"):
            redirect = {
                "uci_id": section_id,
                "name": item.get("name", ""),
                "src": item.get("src", ""),
                "dest": item.get("dest", ""),
                "proto": item.get("proto", ""),
                "src_dport": item.get("src_dport", ""),
                "dest_ip": item.get("dest_ip", ""),
                "dest_port": item.get("dest_port", ""),
                "enabled": item.get("enabled", "1") not in {"0", "false", "off", "no"},
            }
            if "reflection" in item:
                redirect["reflection"] = item.get("reflection", "1") not in {"0", "false", "off", "no"}
            redirects.append(redirect)

json.dump({"redirects": redirects}, sys.stdout, sort_keys=True)
PY
}

dns_diff_json() {
    local current desired
    current=$(get_dns_current_json)
    desired=$(normalize_dns_yaml)
    python3 - "$current" "$desired" <<'PY'
import json
import sys

current = json.loads(sys.argv[1])
desired = json.loads(sys.argv[2])

changes = []
if current["dnsmasq"]["noresolv"] != desired["dnsmasq"]["noresolv"]:
    changes.append({
        "field": "dnsmasq.noresolv",
        "current": current["dnsmasq"]["noresolv"],
        "desired": desired["dnsmasq"]["noresolv"],
    })

current_servers = current["dnsmasq"]["server"]
desired_servers = desired["dnsmasq"]["server"]
if current_servers != desired_servers:
    changes.append({
        "field": "dnsmasq.server",
        "current": current_servers,
        "desired": desired_servers,
    })

current_lan = current["lan"]["dhcp_option"]
desired_lan = desired["lan"]["dhcp_option"]
if current_lan != desired_lan:
    changes.append({
        "field": "lan.dhcp_option",
        "current": current_lan,
        "desired": desired_lan,
    })

json.dump({"changes": changes, "in_sync": len(changes) == 0}, sys.stdout, sort_keys=True)
PY
}

firewall_diff_json() {
    local current desired
    current=$(get_firewall_current_json)
    desired=$(normalize_firewall_yaml)
    python3 - "$current" "$desired" <<'PY'
import json
import sys

current = json.loads(sys.argv[1])
desired = json.loads(sys.argv[2])
prune = desired.get("prune", False)

current_map = {
    item["name"]: item for item in current.get("redirects", []) if item.get("name")
}
desired_map = {
    item["name"]: item for item in desired.get("redirects", []) if item.get("name")
}

adds = []
updates = []
removes = []

for name, item in desired_map.items():
    if name not in current_map:
        adds.append(item)
        continue
    current_item = current_map[name]
    compare_current = {k: current_item.get(k) for k in item.keys()}
    if compare_current != item:
        merged = dict(item)
        merged["uci_id"] = current_item["uci_id"]
        updates.append({"current": current_item, "desired": merged})

if prune:
    for name, item in current_map.items():
        if name not in desired_map:
            removes.append(item)

json.dump(
    {
        "adds": adds,
        "updates": updates,
        "removes": removes,
        "prune": prune,
        "in_sync": len(adds) == 0 and len(updates) == 0 and len(removes) == 0,
    },
    sys.stdout,
    sort_keys=True,
)
PY
}

print_dns_diff() {
    local diff_json="$1"
    local count
    count=$(echo "$diff_json" | jq '.changes | length')

    echo ""
    echo -e " ${BOLD}DNS Diff${NC}"
    echo -e " ${DIM}────────────────────────────────────────────────────${NC}"

    if [[ "$count" -eq 0 ]]; then
        echo -e "  ${GREEN}Already in sync${NC}"
        return 0
    fi

    echo "$diff_json" | jq -r '.changes[] | "  ~ \(.field): \(.current | tostring) -> \(.desired | tostring)"'
}

print_firewall_diff() {
    local diff_json="$1"
    local adds updates removes
    adds=$(echo "$diff_json" | jq '.adds | length')
    updates=$(echo "$diff_json" | jq '.updates | length')
    removes=$(echo "$diff_json" | jq '.removes | length')

    echo ""
    echo -e " ${BOLD}Firewall Redirect Diff${NC}"
    echo -e " ${DIM}────────────────────────────────────────────────────${NC}"

    if [[ "$adds" -eq 0 && "$updates" -eq 0 && "$removes" -eq 0 ]]; then
        echo -e "  ${GREEN}Already in sync${NC}"
        return 0
    fi

    if [[ "$adds" -gt 0 ]]; then
        echo "$diff_json" | jq -r '.adds[] | "  + \(.name): \(.src // "-"):\(.src_dport // "-") -> \(.dest_ip // "-"):\(.dest_port // "-") (\(.proto // "-"))"'
    fi
    if [[ "$updates" -gt 0 ]]; then
        echo "$diff_json" | jq -r '.updates[] | "  ~ \(.desired.name): \(.current.src_dport // "-")/\(.current.dest_ip // "-"):\(.current.dest_port // "-") -> \(.desired.src_dport // "-")/\(.desired.dest_ip // "-"):\(.desired.dest_port // "-")"'
    fi
    if [[ "$removes" -gt 0 ]]; then
        echo "$diff_json" | jq -r '.removes[] | "  - \(.name): \(.src // "-"):\(.src_dport // "-") -> \(.dest_ip // "-"):\(.dest_port // "-")"'
    fi
}

backup_package() {
    local package="$1"
    mkdir -p "$BACKUP_DIR"
    local backup_file="${BACKUP_DIR}/${package}-$(date '+%Y%m%d-%H%M%S').uci"
    router_ssh "uci export ${package}" > "$backup_file"
    log_success "Backup saved: ${backup_file##*/}"
}

confirm_apply() {
    local prompt="$1"
    if [[ "$FORCE" == true ]]; then
        return 0
    fi
    read -r -p "$prompt [y/N] " reply
    [[ "$reply" =~ ^[Yy]$ ]]
}

build_dns_commands() {
    local current desired
    current=$(get_dns_current_json)
    desired=$(normalize_dns_yaml)

    python3 - "$current" "$desired" <<'PY'
import json
import shlex
import sys

current = json.loads(sys.argv[1])
desired = json.loads(sys.argv[2])

commands = []

commands.append(
    "uci set dhcp.@dnsmasq[0].noresolv={}".format(
        shlex.quote("1" if desired["dnsmasq"]["noresolv"] else "0")
    )
)

commands.append("uci delete dhcp.@dnsmasq[0].server 2>/dev/null || true")
for server in desired["dnsmasq"]["server"]:
    commands.append(
        "uci add_list dhcp.@dnsmasq[0].server={}".format(shlex.quote(str(server)))
    )

merged_lan_options = list(current["lan"].get("other_dhcp_option", []))
merged_lan_options.extend(desired["lan"]["dhcp_option"])

commands.append("uci delete dhcp.lan.dhcp_option 2>/dev/null || true")
for option in merged_lan_options:
    commands.append(
        "uci add_list dhcp.lan.dhcp_option={}".format(shlex.quote(str(option)))
    )

commands.append("uci commit dhcp")
commands.append("/etc/init.d/dnsmasq restart")

print("\n".join(commands))
PY
}

build_firewall_commands() {
    local diff_json
    diff_json=$(firewall_diff_json)

    python3 - "$diff_json" <<'PY'
import json
import shlex
import sys

diff = json.loads(sys.argv[1])
commands = []

def emit_redirect_set(prefix, item):
    fields = ["name", "src", "dest", "proto", "src_dport", "dest_ip", "dest_port"]
    for field in fields:
        commands.append(
            "uci set {prefix}.{field}={value}".format(
                prefix=prefix,
                field=field,
                value=shlex.quote(str(item.get(field, ""))),
            )
        )
    commands.append(
        "uci set {prefix}.enabled={value}".format(
            prefix=prefix,
            value=shlex.quote("1" if item.get("enabled", True) else "0"),
        )
    )
    if "reflection" in item:
        commands.append(
            "uci set {prefix}.reflection={value}".format(
                prefix=prefix,
                value=shlex.quote("1" if item.get("reflection", False) else "0"),
            )
        )
    commands.append(
        "uci set {prefix}.target={value}".format(
            prefix=prefix,
            value=shlex.quote("DNAT"),
        )
    )

for item in diff.get("removes", []):
    commands.append("uci delete firewall.{0}".format(item["uci_id"]))

for update in diff.get("updates", []):
    emit_redirect_set("firewall.{0}".format(update["desired"]["uci_id"]), update["desired"])

for item in diff.get("adds", []):
    commands.append("uci add firewall redirect")
    emit_redirect_set("firewall.@redirect[-1]", item)

if commands:
    commands.append("uci commit firewall")
    commands.append("/etc/init.d/firewall reload")

print("\n".join(commands))
PY
}

cmd_validate() {
    log_info "Validating OpenWrt config..."
    ensure_files_exist || return 1

    local dns_json firewall_json
    dns_json=$(normalize_dns_yaml)
    firewall_json=$(normalize_firewall_yaml)

    python3 - "$dns_json" "$firewall_json" <<'PY'
import ipaddress
import json
import re
import sys

dns = json.loads(sys.argv[1])
firewall = json.loads(sys.argv[2])

errors = 0
warnings = 0

def error(msg):
    global errors
    print(f"\033[0;31m  ERROR\033[0m  {msg}")
    errors += 1

def warning(msg):
    global warnings
    print(f"\033[0;33m  WARN\033[0m   {msg}")
    warnings += 1

for server in dns["dnsmasq"]["server"]:
    try:
        ipaddress.ip_address(server)
    except ValueError:
        warning(f"dnsmasq.server contains non-IP value: {server}")

for option in dns["lan"]["dhcp_option"]:
    if not re.match(r"^6,", option):
        error(f"lan.dhcp_option must be DNS option 6 entries only: {option}")

seen = set()
for item in firewall["redirects"]:
    name = item["name"].strip()
    if not name:
        error("redirect missing name")
        continue
    if name in seen:
        error(f"duplicate redirect name: {name}")
    seen.add(name)

    for field in ("src_dport", "dest_port"):
        value = item[field]
        if not value.isdigit():
            error(f"redirect {name}: {field} must be numeric")
        else:
            port = int(value)
            if port < 1 or port > 65535:
                error(f"redirect {name}: {field} out of range")

    try:
        ipaddress.ip_address(item["dest_ip"])
    except ValueError:
        error(f"redirect {name}: invalid dest_ip {item['dest_ip']}")

if errors == 0 and warnings == 0:
    print("\033[0;32m  All checks passed\033[0m")

print()
print(f"  {errors} errors | {warnings} warnings")
sys.exit(1 if errors > 0 else 0)
PY
}

write_dns_yaml_from_router() {
    local current_json="$1"
    python3 - "$current_json" "$DNS_FILE" <<'PY'
import json
import sys
import yaml

current = json.loads(sys.argv[1])
path = sys.argv[2]

data = {
    "dnsmasq": {
        "noresolv": current["dnsmasq"]["noresolv"],
        "server": current["dnsmasq"]["server"],
    },
    "lan": {
        "dhcp_option": current["lan"]["dhcp_option"],
    },
}

with open(path, "w", encoding="utf-8") as fh:
    yaml.dump(data, fh, default_flow_style=False, sort_keys=False)
PY
}

write_firewall_yaml_from_router() {
    local current_json="$1"
    python3 - "$current_json" "$FIREWALL_FILE" <<'PY'
import json
import sys
import yaml

current = json.loads(sys.argv[1])
path = sys.argv[2]

redirects = []
counter = 1
for item in current.get("redirects", []):
    name = item.get("name") or f"redirect-{counter}"
    counter += 1
    entry = {
        "name": name,
        "src": item.get("src", "wan"),
        "dest": item.get("dest", "lan"),
        "proto": item.get("proto", "tcp"),
        "src_dport": item.get("src_dport", ""),
        "dest_ip": item.get("dest_ip", ""),
        "dest_port": item.get("dest_port", ""),
        "enabled": bool(item.get("enabled", True)),
    }
    if "reflection" in item:
        entry["reflection"] = bool(item.get("reflection"))
    redirects.append(entry)

data = {"prune": False, "redirects": redirects}

with open(path, "w", encoding="utf-8") as fh:
    yaml.dump(data, fh, default_flow_style=False, sort_keys=False)
PY
}

cmd_bootstrap() {
    local subsystem="${1:-all}"
    check_dependencies
    check_ssh

    case "$subsystem" in
        dns)
            log_info "Bootstrapping dns.yaml from router..."
            write_dns_yaml_from_router "$(get_dns_current_json)"
            log_success "Wrote ${DNS_FILE##*/}"
            ;;
        firewall)
            log_info "Bootstrapping firewall.yaml from router..."
            write_firewall_yaml_from_router "$(get_firewall_current_json)"
            log_success "Wrote ${FIREWALL_FILE##*/}"
            ;;
        all)
            cmd_bootstrap dns
            cmd_bootstrap firewall
            ;;
        *)
            log_error "Unknown subsystem: $subsystem"
            return 1
            ;;
    esac
}

cmd_export() {
    local subsystem="${1:-all}"
    check_ssh
    case "$subsystem" in
        dns)      router_ssh "uci export dhcp" ;;
        firewall) router_ssh "uci export firewall" ;;
        all)
            router_ssh "uci export dhcp"
            echo
            router_ssh "uci export firewall"
            ;;
        *)
            log_error "Unknown subsystem: $subsystem"
            return 1
            ;;
    esac
}

cmd_diff() {
    local subsystem="${1:-all}"
    check_dependencies
    ensure_files_exist || return 1
    check_ssh

    case "$subsystem" in
        dns)
            print_dns_diff "$(dns_diff_json)"
            ;;
        firewall)
            print_firewall_diff "$(firewall_diff_json)"
            ;;
        all)
            print_dns_diff "$(dns_diff_json)"
            print_firewall_diff "$(firewall_diff_json)"
            ;;
        *)
            log_error "Unknown subsystem: $subsystem"
            return 1
            ;;
    esac
}

cmd_sync() {
    local subsystem="${1:-all}"
    check_dependencies
    ensure_files_exist || return 1
    check_ssh
    cmd_validate

    case "$subsystem" in
        dns)
            local dns_diff
            dns_diff=$(dns_diff_json)
            print_dns_diff "$dns_diff"
            if [[ "$(echo "$dns_diff" | jq -r '.in_sync')" == "true" ]]; then
                return 0
            fi
            confirm_apply "Apply DNS changes?" || { log_warning "Aborted"; return 0; }
            backup_package dhcp
            build_dns_commands | router_ssh_stdin
            log_success "DNS sync complete"
            ;;
        firewall)
            local fw_diff fw_commands
            fw_diff=$(firewall_diff_json)
            print_firewall_diff "$fw_diff"
            if [[ "$(echo "$fw_diff" | jq -r '.in_sync')" == "true" ]]; then
                return 0
            fi
            confirm_apply "Apply firewall redirect changes?" || { log_warning "Aborted"; return 0; }
            backup_package firewall
            fw_commands=$(build_firewall_commands)
            if [[ -n "$fw_commands" ]]; then
                echo "$fw_commands" | router_ssh_stdin
            fi
            log_success "Firewall sync complete"
            ;;
        all)
            cmd_sync dns
            cmd_sync firewall
            ;;
        *)
            log_error "Unknown subsystem: $subsystem"
            return 1
            ;;
    esac
}

cmd_restore() {
    local backup_file="${1:-}"
    if [[ -z "$backup_file" ]]; then
        log_error "Usage: openwrt restore <backup-file>"
        return 1
    fi
    if [[ ! -f "$backup_file" ]]; then
        log_error "Backup file not found: $backup_file"
        return 1
    fi

    check_ssh

    local package
    case "$(basename "$backup_file")" in
        dhcp-*.uci) package="dhcp" ;;
        firewall-*.uci) package="firewall" ;;
        *)
            log_error "Cannot infer package from backup filename"
            return 1
            ;;
    esac

    confirm_apply "Restore ${package} from $(basename "$backup_file")?" || { log_warning "Aborted"; return 0; }
    backup_package "$package"
    {
        cat "$backup_file"
        echo "uci commit ${package}"
        if [[ "$package" == "dhcp" ]]; then
            echo "/etc/init.d/dnsmasq restart"
        else
            echo "/etc/init.d/firewall reload"
        fi
    } | router_ssh_stdin
    log_success "Restore complete"
}

cmd_status() {
    check_dependencies
    ensure_files_exist || return 1

    echo ""
    echo -e " ${BOLD}OpenWrt Status${NC}"
    echo -e " ${DIM}────────────────────────────────────────────────────${NC}"
    echo -e "  Router: $(get_connection_field user)@$(get_connection_field host)"

    if ! router_ssh "echo ok" &>/dev/null; then
        echo -e "  Router reachable: ${RED}no${NC}"
        return 1
    fi

    local dns_current fw_current dns_diff fw_diff
    dns_current=$(get_dns_current_json)
    fw_current=$(get_firewall_current_json)
    dns_diff=$(dns_diff_json)
    fw_diff=$(firewall_diff_json)

    echo -e "  Router reachable: ${GREEN}yes${NC}"
    echo -e "  DNS upstreams: $(echo "$dns_current" | jq -r '.dnsmasq.server | join(", ") // "-"')"
    echo -e "  LAN DNS options: $(echo "$dns_current" | jq -r '.lan.dhcp_option | join(", ") // "-"')"
    echo -e "  Redirects on router: $(echo "$fw_current" | jq '.redirects | length')"
    echo -e "  DNS in sync: $(echo "$dns_diff" | jq -r '.in_sync')"
    echo -e "  Firewall in sync: $(echo "$fw_diff" | jq -r '.in_sync')"
}

cmd_help() {
    cat <<EOF
openwrt — Declarative OpenWrt manager (safe v1)

USAGE:
    openwrt [--force] <command> [subsystem]

COMMANDS:
    bootstrap [dns|firewall|all]  Pull current router state into YAML
    validate                      Validate local YAML config
    diff [dns|firewall|all]       Show planned changes
    sync [dns|firewall|all]       Apply YAML config to router
    export [dns|firewall|all]     Print raw UCI export
    restore <backup-file>         Restore a saved UCI snapshot
    status                        Show router summary and sync state
    help                          Show this help message

NOTES:
    - DNS sync manages only:
      * dhcp.@dnsmasq[0].noresolv
      * dhcp.@dnsmasq[0].server
      * DNS-related dhcp.lan.dhcp_option entries (option 6)
    - Static DHCP hosts remain managed by the separate 'dhcp' CLI.
    - Firewall sync manages redirect entries keyed by redirect name.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE=true; shift ;;
        -*) log_error "Unknown option: $1"; cmd_help; exit 1 ;;
        *) break ;;
    esac
done

command="${1:-help}"
shift || true

case "$command" in
    bootstrap) cmd_bootstrap "${1:-all}" ;;
    validate)  cmd_validate ;;
    diff)      cmd_diff "${1:-all}" ;;
    sync)      cmd_sync "${1:-all}" ;;
    export)    cmd_export "${1:-all}" ;;
    restore)   cmd_restore "${1:-}" ;;
    status)    cmd_status ;;
    help|--help|-h) cmd_help ;;
    *)
        log_error "Unknown command: $command"
        cmd_help
        exit 1
        ;;
esac
