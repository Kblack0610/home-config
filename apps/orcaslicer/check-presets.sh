#!/usr/bin/env bash
# Prove every preset under profiles/ still resolves its `inherits` on an OrcaSlicer image tag,
# by slicing a cube with the image's own CLI in a throwaway container.
# Usage: check-presets.sh <tag>   e.g. check-presets.sh v2.5.0-ls49
# Why this shape: 2.5 ships each vendor as an opaque <vendor>.opc (the profiles dirs are gone),
# and the CLI only resolves a user preset's parent when the file sits in <datadir>/user/default
# and the vendor bundle sits in <datadir>/system. A negative control (bogus inherits) must fail.
set -euo pipefail
tag=${1:?usage: check-presets.sh <image tag>}
here=$(cd "$(dirname "$0")" && pwd)
img=lscr.io/linuxserver/orcaslicer:$tag
docker run --rm --entrypoint bash -v "$here/profiles:/profiles:ro" "$img" -c '
set -u
export QT_QPA_PLATFORM=offscreen
D=/tmp/d; U=$D/user/default
mkdir -p $D/system $U
cp /opt/orcaslicer/resources/profiles/{Elegoo,BBL}.opc $D/system/
cp -r /profiles/{machine,filament,process} $U/
python3 - <<PY
import json
d=json.load(open("$U/machine/Elegoo Neptune 4 Pro (0.4 nozzle)-OpenNept4une.json"))
d["inherits"]="No Such Parent"; d["name"]="ZZ-negative-control"
json.dump(d,open("$U/machine/ZZ-negative-control.json","w"))
PY
python3 -c "
import struct
v=[(0,0,0),(20,0,0),(20,20,0),(0,20,0),(0,0,20),(20,0,20),(20,20,20),(0,20,20)]
f=[(0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),(1,2,6),(1,6,5),(2,3,7),(2,7,6),(3,0,4),(3,4,7)]
s=\"solid c\n\"
for t in f: s+=\"facet normal 0 0 0\nouter loop\n\"+\"\".join(\"vertex %d %d %d\n\"%v[i] for i in t)+\"endloop\nendfacet\n\"
open(\"/tmp/cube.stl\",\"w\").write(s+\"endsolid c\n\")"
fail=0; n=0
try() { # label machine process filament
  n=$((n+1)); rm -rf /tmp/out
  if /opt/orcaslicer/bin/orca-slicer --datadir $D --load-settings "$U/machine/$2.json;$U/process/$3.json" \
      --load-filaments "$U/filament/$4.json" --slice 0 --outputdir /tmp/out /tmp/cube.stl >/tmp/log 2>&1 \
      && grep -q "\"return_code\": 0" /tmp/out/result.json; then echo "ok   $1"; else echo "FAIL $1: $(tail -2 /tmp/log | head -1 | cut -c1-160)"; fail=1; fi
}
N4="Elegoo Neptune 4 Pro (0.4 nozzle)-OpenNept4une"; N8="Elegoo Neptune 4 Pro (0.8 nozzle)-OpenNept4une"
S4="0.20mm Standard @Elegoo Neptune4Pro (0.4 nozzle)-OpenNept4une"; P4="0.20mm PETG @Elegoo Neptune4Pro (0.4 nozzle)-OpenNept4une"
S8="0.40mm Standard @Elegoo Neptune4Pro (0.8 nozzle)-OpenNept4une"
PLA="Elegoo Generic PLA-OpenNept4une"; PETG="Elegoo Generic PETG-OpenNept4une"
try "0.4 standard PLA" "$N4" "$S4" "$PLA"
try "0.4 PETG PETG"   "$N4" "$P4" "$PETG"
try "0.8 standard PLA" "$N8" "$S8" "$PLA"
resolve() { # label machine: the parent resolved when printer_model is filled from it
  n=$((n+1)); rm -f /tmp/m.json
  /opt/orcaslicer/bin/orca-slicer --datadir $D --load-settings "$U/machine/$2.json" --export-settings /tmp/m.json >/tmp/log 2>&1 || true
  if grep -q "\"printer_model\": \"[A-Za-z]" /tmp/m.json 2>/dev/null; then echo "ok   $1"; return 0; fi
  echo "FAIL $1: $(tail -2 /tmp/log | head -1 | cut -c1-160)"; return 1
}
resolve "A1 machine" "Bambu Lab A1 (0.4 nozzle)-LAN" || fail=1
if resolve "negative control" ZZ-negative-control >/dev/null; then echo "FAIL negative control resolved: the check proves nothing"; fail=1; else echo "ok   negative control rejected"; fi
exit $fail
'
