#!/usr/bin/env python3
"""print-service: headless OrcaSlicer slice + Moonraker dispatch for the home 3D printers.

This is the "print" capability backend in the orchestration architecture
(docs: the plan in ~/.claude/plans, and apps/print-service/README.md). It is a
dependency-free stdlib HTTP API (no runtime pip) that runs on the OrcaSlicer
image, so the `orca-slicer` binary and a python3 are already present.

Endpoints:
  GET  /healthz                       -> liveness
  GET  /printers                      -> known printers + slice options
  POST /slice   {file,printer,filament,process}        -> slice an STL on the NAS to gcode
  POST /print   {file,printer,filament,process,start}  -> slice -> upload to Moonraker -> (optionally) start
  GET  /status?printer=neptune        -> Moonraker print_stats/webhooks state
  POST /cancel  {printer}             -> Moonraker cancel

Slicing recipe (2.5.0, verified 2026-11-10; see README "Why the datadir"):
  OrcaSlicer 2.5 ships each vendor as an opaque <vendor>.opc, so there are no
  system preset files to read. The CLI resolves a user preset's `inherits` only
  when the preset file sits in <datadir>/user/default/<type>/ and the vendor
  bundle sits in <datadir>/system/. So each slice builds a throwaway datadir:
  the Elegoo bundle, the seeded OpenNept4une machine preset (it carries the
  PRINT_START/PRINT_END gcode and inherits the system Neptune 4 Pro), and thin
  stub process/filament presets that only inherit the system ones by name.
  Runs headless with QT_QPA_PLATFORM=offscreen (no X server).
"""
import glob
import json
import os
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ORCA = "/opt/orcaslicer/bin/orca-slicer"
BUNDLE = "/opt/orcaslicer/resources/profiles/Elegoo.opc"
SEED = os.environ.get("SEED_DIR", "/seed")        # mounted orcaslicer-profiles configmap
PRINTS = os.environ.get("PRINTS_DIR", "/prints")  # NAS 3d-printing share
LISTEN_PORT = int(os.environ.get("PORT", "8080"))

# Printer registry. Neptune first; Bambu is a documented follow-up (FTP+MQTT, not
# Moonraker) so it is intentionally absent from the dispatch path here.
#
# MACHINE is the seeded OpenNept4une user preset. PROCESS and FILAMENT are stubs that
# inherit the system presets by name: the user process/filament files carry values a
# new Orca version can reject (2.4.2 refused small_perimeter_speed "0"), and process =
# layer height (material-agnostic) while the filament picks the temps (PLA 210, PETG 240).
PRINTERS = {
    "neptune": {
        "label": "Elegoo Neptune 4 Pro",
        "moonraker": os.environ.get("NEPTUNE_MOONRAKER_URL", "http://neptune.neptune.svc.cluster.local:7125"),
        "seed_machine": f"{SEED}/machine-neptune4pro.json",
        "processes": {
            "standard": "0.20mm Standard @Elegoo N4Pro 0.4 nozzle",
            "fine": "0.12mm Fine @Elegoo N4Pro 0.4 nozzle",
            "draft": "0.24mm Draft @Elegoo N4Pro 0.4 nozzle",
        },
        "filaments": {
            "pla": "Generic PLA @Elegoo",
            "petg": "Generic PETG @Elegoo",
        },
    },
}
DEFAULT_PRINTER = "neptune"


# ---------------------------------------------------------------- slicing ----
def _stub(ptype, parent):
    """A user preset that only inherits a system preset by name.

    The CLI finds a user preset by file name == preset name, and skips one with no "version"."""
    return {"type": ptype, "name": f"ps-{ptype}", "from": "User", "version": "1.9.0.2", "inherits": parent}


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(data, fh)
    return path


def _safe_stl(name):
    """Resolve a requested STL name under PRINTS, rejecting path traversal."""
    p = os.path.realpath(os.path.join(PRINTS, name))
    if not p.startswith(os.path.realpath(PRINTS) + os.sep):
        raise ValueError("file path escapes the prints directory")
    if not os.path.isfile(p):
        raise FileNotFoundError(f"{name} not found under {PRINTS}")
    return p


def slice_stl(name, printer=DEFAULT_PRINTER, filament="pla", process="standard"):
    cfg = PRINTERS[printer]
    if process not in cfg["processes"]:
        raise ValueError(f"unknown process {process!r}; options: {sorted(cfg['processes'])}")
    if filament not in cfg["filaments"]:
        raise ValueError(f"unknown filament {filament!r}; options: {sorted(cfg['filaments'])}")
    stl = _safe_stl(name)
    tmp = tempfile.mkdtemp(prefix="slice-")
    try:
        datadir = f"{tmp}/datadir"; outdir = f"{tmp}/out"; user = f"{datadir}/user/default"
        os.makedirs(f"{datadir}/system"); os.makedirs(outdir)
        shutil.copy(BUNDLE, f"{datadir}/system/")
        with open(cfg["seed_machine"]) as fh:
            seed = json.load(fh)
        mp = _write(f"{user}/machine/{seed['name']}.json", {**seed, "type": "machine"})
        pp = _write(f"{user}/process/ps-process.json", _stub("process", cfg["processes"][process]))
        fp = _write(f"{user}/filament/ps-filament.json", _stub("filament", cfg["filaments"][filament]))
        env = dict(os.environ); env.pop("DISPLAY", None); env.pop("WAYLAND_DISPLAY", None)
        env["QT_QPA_PLATFORM"] = "offscreen"
        r = subprocess.run(
            [ORCA, "--datadir", datadir, "--load-settings", f"{mp};{pp}",
             "--load-filaments", fp, "--slice", "0", "--outputdir", outdir, stl],
            capture_output=True, text=True, timeout=900, env=env)
        res_path = os.path.join(outdir, "result.json")
        res = json.load(open(res_path)) if os.path.exists(res_path) else {}
        if res.get("return_code") != 0 or r.returncode != 0:
            raise RuntimeError(res.get("error_string") or r.stderr[-300:] or "slice failed")
        gc = sorted(glob.glob(outdir + "/*.gcode"))
        if not gc:
            raise RuntimeError("slicer reported success but produced no gcode")
        base = os.path.splitext(os.path.basename(stl))[0]
        dest = os.path.join(PRINTS, f"{base}.gcode")
        shutil.move(gc[0], dest)
        plates = res.get("sliced_plates", [{}])
        return {"gcode": os.path.basename(dest), "gcode_path": dest,
                "triangles": plates[0].get("triangle_count"),
                "warning": plates[0].get("warning_message") or None}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# -------------------------------------------------------------- moonraker ----
def _mr(printer):
    return PRINTERS[printer]["moonraker"].rstrip("/")


def moonraker_upload(printer, gcode_path, start=False):
    boundary = "----printsvc" + uuid.uuid4().hex
    fn = os.path.basename(gcode_path)
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{fn}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode(),
        open(gcode_path, "rb").read(), b"\r\n",
        f'--{boundary}\r\nContent-Disposition: form-data; name="print"\r\n\r\n'
        f'{"true" if start else "false"}\r\n'.encode(),
        f"--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)
    req = urllib.request.Request(
        _mr(printer) + "/server/files/upload", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.load(resp)


def moonraker_start(printer, filename):
    req = urllib.request.Request(
        _mr(printer) + "/printer/print/start?filename=" + urllib.parse.quote(filename), method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode(errors="replace")


def moonraker_status(printer):
    url = _mr(printer) + "/printer/objects/query?print_stats&webhooks&display_status"
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.load(resp).get("result", {}).get("status", {})


def moonraker_cancel(printer):
    req = urllib.request.Request(_mr(printer) + "/printer/print/cancel", method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode(errors="replace")


# ------------------------------------------------------------------- http ----
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", "0") or "0")
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def log_message(self, fmt, *args):  # quieter default logging
        print("print-service:", fmt % args)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path == "/healthz":
                return self._send(200, {"ok": True})
            if u.path == "/printers":
                return self._send(200, {"printers": {k: {"label": v["label"],
                                        "processes": sorted(v["processes"]),
                                        "filaments": sorted(v["filaments"])}
                                        for k, v in PRINTERS.items()}, "default": DEFAULT_PRINTER})
            if u.path == "/status":
                printer = (q.get("printer") or [DEFAULT_PRINTER])[0]
                return self._send(200, {"printer": printer, "status": moonraker_status(printer)})
            return self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001 - surface any failure as JSON
            return self._send(502, {"error": str(e)})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        try:
            b = self._body()
            printer = b.get("printer", DEFAULT_PRINTER)
            if printer not in PRINTERS:
                return self._send(400, {"error": f"unknown printer {printer!r}"})
            if u.path == "/slice":
                out = slice_stl(b["file"], printer, b.get("filament", "pla"), b.get("process", "standard"))
                return self._send(200, {"printer": printer, **out})
            if u.path == "/print":
                sliced = slice_stl(b["file"], printer, b.get("filament", "pla"), b.get("process", "standard"))
                start = bool(b.get("start", True))
                up = moonraker_upload(printer, sliced["gcode_path"], start=start)
                return self._send(200, {"printer": printer, "started": start, **sliced, "moonraker": up})
            if u.path == "/cancel":
                return self._send(200, {"printer": printer, "moonraker": moonraker_cancel(printer)})
            return self._send(404, {"error": "not found"})
        except (KeyError,) as e:
            return self._send(400, {"error": f"missing field {e}"})
        except (FileNotFoundError, ValueError) as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._send(502, {"error": str(e)})


if __name__ == "__main__":
    print(f"print-service listening on :{LISTEN_PORT} (printers: {', '.join(PRINTERS)})")
    ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler).serve_forever()
