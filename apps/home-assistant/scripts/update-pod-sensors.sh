#!/bin/bash
# Script to find current pod chart names in Netdata
# Run this after pods restart to get updated chart names for HA sensors
#
# Usage: ./update-pod-sensors.sh [app-name]
# Example: ./update-pod-sensors.sh home-assistant

NODES=(
    "192.168.1.20"   # raspberrypi (control plane)
    "192.168.1.21"   # raspberrypi-23a7710c
    "192.168.1.22"   # raspberrypi-e3a771f1
    "192.168.1.23"   # raspberrypi-771be84c
    "192.168.1.124"  # raspberrypi-b814834e
)

APP_NAME="${1:-}"

echo "=== K3s Pod Chart Names Finder ==="
echo ""

if [ -n "$APP_NAME" ]; then
    echo "Searching for: $APP_NAME"
    echo "---"
    for node in "${NODES[@]}"; do
        result=$(curl -s "http://${node}:19999/api/v1/charts" 2>/dev/null | jq -r '.charts | keys[]' | grep -i "$APP_NAME" | grep "\.cpu$")
        if [ -n "$result" ]; then
            echo "Node $node:"
            echo "$result" | sed 's/^/  /'
            echo ""
        fi
    done
else
    echo "All Application Pods:"
    echo "---"
    for node in "${NODES[@]}"; do
        echo "Node $node:"
        curl -s "http://${node}:19999/api/v1/charts" 2>/dev/null | \
            jq -r '.charts | keys[]' | \
            grep "k8s_cntr" | \
            grep -v "svclb\|lb-tcp" | \
            grep "\.cpu$" | \
            sed 's/^/  /' || echo "  (no pods or unreachable)"
        echo ""
    done
fi

echo "=== Key Apps Summary ==="
echo ""
echo "To use in Home Assistant, copy the chart name (without .cpu) and use:"
echo "  - .cpu for CPU usage"
echo "  - .mem_usage for RAM (bytes)"
echo "  - .mem for memory stats"
echo ""
