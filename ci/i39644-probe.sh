#!/bin/sh
# i39644: probe the dark GCP freqtrade fleet from the operator's own LAN NAT (CI host egress = home WAN)
set -u
echo "=== HOST"; hostname; date -u
echo -n "EGRESS_JSON="; curl -s -m 12 https://ifconfig.co/json | tr -d '\n'; echo
echo -n "EGRESS_IP="; curl -s -m 10 https://api.ipify.org; echo
echo "=== ICMP"
IPS="35.195.67.84 35.205.160.81 104.199.48.75 35.205.159.255 34.168.149.84 35.233.38.30 35.233.103.91 34.79.133.17 34.38.98.172 34.77.36.138 34.76.143.91"
for ip in $IPS; do
  if ping -c 1 -W 2 "$ip" >/dev/null 2>&1; then echo "ICMP_UP $ip"; else echo "ICMP_DOWN $ip"; fi
done
echo "=== CONNECT+HTTP probe (unauthenticated /api/v1/ping, 4s)"
PORTS="$(seq 32321 32340) 8084 8443 6443 443 80"
rm -f /tmp/hits; n=0
for ip in $IPS; do
  for p in $PORTS; do
    n=$((n+1))
    if ! timeout 2 sh -c "</dev/tcp/$ip/$p" 2>/dev/null; then continue; fi
    case "$p" in
      443|8443|6443) scheme=https ;;
      *) scheme=http ;;
    esac
    code=$(curl -s -m 4 -o /tmp/b -w '%{http_code}' "$scheme://$ip:$p/api/v1/ping" 2>/dev/null)
    echo "RESP $ip:$p $scheme => $code body=$(head -c 160 /tmp/b | tr -d '\r\n')"
    echo "$ip:$p:$scheme" >> /tmp/hits
  done
done
echo "PROBED=$n"
echo "=== ONE authed GET /api/v1/show_config per responder"
if [ -s /tmp/hits ]; then
  while IFS=: read -r ip p scheme; do
    ac=$(curl -s -m 8 -o /tmp/c -w '%{http_code}' -u 'freqtrade:&&Liz270117!!' "$scheme://$ip:$p/api/v1/show_config" 2>/dev/null)
    echo "##### SHOWCONFIG $ip:$p => $ac"
    head -c 5000 /tmp/c; echo
  done < /tmp/hits
else
  echo "NO_RESPONDERS"
fi
echo "=== CONTROL portquiz 32328/8084 + freqle.org via LAN"
curl -s -m 8 -o /dev/null -w 'portquiz32328=%{http_code}\n' http://portquiz.net:32328/ 2>&1
curl -s -m 8 -o /dev/null -w 'portquiz8084=%{http_code}\n' http://portquiz.net:8084/ 2>&1
curl -s -m 10 -o /dev/null -w 'freqle_healthz=%{http_code}\n' https://freqle.org/healthz 2>&1
echo FLEETDONE
