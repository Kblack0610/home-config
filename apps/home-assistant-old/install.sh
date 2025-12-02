#!/bin/bash
# Home Assistant Installation Script for Kubernetes
# This script will guide you through installing Home Assistant on your cluster

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}Home Assistant Kubernetes Installation${NC}"
echo "========================================"
echo ""

# Check prerequisites
echo "Checking prerequisites..."

if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}ERROR: kubectl is not installed${NC}"
    exit 1
fi

if ! command -v helm &> /dev/null; then
    echo -e "${RED}ERROR: helm is not installed${NC}"
    exit 1
fi

# Check cluster connection
if ! kubectl cluster-info &> /dev/null; then
    echo -e "${RED}ERROR: Cannot connect to Kubernetes cluster${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Prerequisites met${NC}"
echo ""

# Prompt for configuration
echo "Please answer the following questions:"
echo ""

# Namespace
read -p "Namespace to install into (default: home-assistant): " NAMESPACE
NAMESPACE=${NAMESPACE:-home-assistant}

# Values file selection
echo ""
echo "Select your deployment type:"
echo "1) Standard (LoadBalancer)"
echo "2) k3s optimized"
echo "3) With Ingress"
echo "4) With USB devices"
read -p "Enter choice (1-4): " DEPLOYMENT_TYPE

VALUES_FILES="-f values.yaml"

case $DEPLOYMENT_TYPE in
    2)
        VALUES_FILES="$VALUES_FILES -f k3s-values.yaml"
        echo -e "${YELLOW}Using k3s-optimized configuration${NC}"
        ;;
    3)
        VALUES_FILES="$VALUES_FILES -f ingress-values.yaml"
        echo -e "${YELLOW}Using Ingress configuration${NC}"
        read -p "Enter your domain name (e.g., home.example.com): " DOMAIN
        if [ -n "$DOMAIN" ]; then
            echo "Remember to update ingress-values.yaml with your domain: $DOMAIN"
        fi
        ;;
    4)
        VALUES_FILES="$VALUES_FILES -f usb-device-values.yaml"
        echo -e "${YELLOW}Using USB device configuration${NC}"
        echo "Remember to:"
        echo "1. Label your node: kubectl label node <node-name> hardware.homeassistant=usb-devices"
        echo "2. Update usb-device-values.yaml with your device path"
        read -p "Press enter to continue..."
        ;;
    *)
        echo -e "${YELLOW}Using standard configuration${NC}"
        ;;
esac

echo ""
read -p "Timezone (e.g., America/Chicago): " TIMEZONE
if [ -n "$TIMEZONE" ]; then
    echo -e "${YELLOW}Remember to update values.yaml with timezone: $TIMEZONE${NC}"
fi

# Confirm installation
echo ""
echo "Ready to install with the following settings:"
echo "  Namespace: $NAMESPACE"
echo "  Values files: $VALUES_FILES"
echo ""
read -p "Proceed with installation? (y/n): " CONFIRM

if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
    echo "Installation cancelled"
    exit 0
fi

# Add Helm repo
echo ""
echo "Adding Helm repository..."
helm repo add pajikos https://pajikos.github.io/home-assistant-helm-chart/ || true
helm repo update

# Create namespace
echo ""
echo "Creating namespace: $NAMESPACE"
kubectl create namespace $NAMESPACE --dry-run=client -o yaml | kubectl apply -f -

# Install Home Assistant
echo ""
echo "Installing Home Assistant..."
helm install home-assistant pajikos/home-assistant \
    --namespace $NAMESPACE \
    $VALUES_FILES

# Wait for pod to be ready
echo ""
echo "Waiting for Home Assistant to be ready..."
kubectl wait --for=condition=ready pod \
    -l app.kubernetes.io/name=home-assistant \
    -n $NAMESPACE \
    --timeout=300s || true

# Get access information
echo ""
echo -e "${GREEN}Installation complete!${NC}"
echo ""
echo "Access information:"
echo "==================="

SERVICE_TYPE=$(kubectl get svc -n $NAMESPACE home-assistant -o jsonpath='{.spec.type}')

case $SERVICE_TYPE in
    LoadBalancer)
        echo "Service type: LoadBalancer"
        echo -n "Getting external IP... "
        EXTERNAL_IP=""
        for i in {1..30}; do
            EXTERNAL_IP=$(kubectl get svc -n $NAMESPACE home-assistant -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
            if [ -n "$EXTERNAL_IP" ]; then
                break
            fi
            sleep 2
        done

        if [ -n "$EXTERNAL_IP" ]; then
            echo -e "${GREEN}$EXTERNAL_IP${NC}"
            echo ""
            echo -e "Access Home Assistant at: ${GREEN}http://$EXTERNAL_IP:8123${NC}"
        else
            echo -e "${YELLOW}Pending${NC}"
            echo "Run 'kubectl get svc -n $NAMESPACE' to check when IP is assigned"
        fi
        ;;
    NodePort)
        NODE_PORT=$(kubectl get svc -n $NAMESPACE home-assistant -o jsonpath='{.spec.ports[0].nodePort}')
        NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
        echo "Service type: NodePort"
        echo -e "Access Home Assistant at: ${GREEN}http://$NODE_IP:$NODE_PORT${NC}"
        ;;
    ClusterIP)
        echo "Service type: ClusterIP (Ingress)"
        INGRESS_HOST=$(kubectl get ingress -n $NAMESPACE -o jsonpath='{.items[0].spec.rules[0].host}' 2>/dev/null || echo "not-configured")
        if [ "$INGRESS_HOST" != "not-configured" ]; then
            echo -e "Access Home Assistant at: ${GREEN}http://$INGRESS_HOST${NC}"
        else
            echo "Configure Ingress to access Home Assistant"
        fi
        ;;
esac

echo ""
echo "Useful commands:"
echo "  View logs:   kubectl logs -n $NAMESPACE -l app.kubernetes.io/name=home-assistant -f"
echo "  View status: kubectl get pods -n $NAMESPACE"
echo "  Shell:       kubectl exec -it -n $NAMESPACE \$(kubectl get pod -n $NAMESPACE -l app.kubernetes.io/name=home-assistant -o name) -- /bin/bash"
echo ""
echo -e "${GREEN}Setup complete! Visit Home Assistant to create your account.${NC}"
