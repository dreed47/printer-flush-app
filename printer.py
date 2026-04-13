#!/usr/bin/env python3
"""
Printer Color Flush
Sends a color flush PDF to a network printer on a configurable interval via IPP.
No CUPS required — prints directly over the network.
"""

import os
import re
import struct
import subprocess
import tempfile
import time
import logging
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

import requests
import schedule
from flask import Flask, jsonify, request as flask_request

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_BUFFER: deque = deque(maxlen=500)

class _BufferHandler(logging.Handler):
    def emit(self, record):
        LOG_BUFFER.append({
            "t": datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S"),
            "l": record.levelname,
            "m": self.format(record),
        })

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger().addHandler(_BufferHandler())
log = logging.getLogger(__name__)

# ── Config (from environment) ─────────────────────────────────────────────────
PRINTER_IP       = os.environ.get("PRINTER_IP", "192.168.86.99")
PRINTER_PORT     = int(os.getenv("PRINTER_PORT", "631"))
PRINTER_PATH     = os.getenv("PRINTER_PATH", "/ipp/print")
FLUSH_PDF        = os.getenv("FLUSH_PDF", "/data/printer-color-flush.pdf")
RUN_INTERVAL_DAYS= int(os.getenv("RUN_INTERVAL_DAYS", "10"))
ENV_FILE         = os.getenv("ENV_FILE", "/app/.env")
STATE_FILE       = Path(os.getenv("STATE_FILE", "/data/last_print.json"))
WEB_PORT         = int(os.getenv("PORT", "7841"))

PRINTER_URI = f"ipp://{PRINTER_IP}:{PRINTER_PORT}{PRINTER_PATH}"
PRINTER_URL = f"http://{PRINTER_IP}:{PRINTER_PORT}{PRINTER_PATH}"


# ── IPP printing ──────────────────────────────────────────────────────────────
def _ipp_attr(tag: int, name: str, value: bytes) -> bytes:
    """Encode a single IPP attribute."""
    name_enc = name.encode("utf-8")
    return (
        struct.pack(">B", tag)
        + struct.pack(">H", len(name_enc))
        + name_enc
        + struct.pack(">H", len(value))
        + value
    )

def build_print_job_request(printer_uri: str, doc_data: bytes, doc_format: bytes = b"image/jpeg") -> bytes:
    """Build a minimal IPP 1.1 Print-Job request."""
    # Version 1.1 | operation Print-Job (0x0002) | request-id 1
    header = struct.pack(">BBHI", 1, 1, 0x0002, 1)

    attrs  = b"\x01"  # operation-attributes-tag
    attrs += _ipp_attr(0x47, "attributes-charset",          b"utf-8")
    attrs += _ipp_attr(0x48, "attributes-natural-language", b"en-us")
    attrs += _ipp_attr(0x45, "printer-uri",                 printer_uri.encode())
    attrs += _ipp_attr(0x42, "requesting-user-name",        b"printer-flush")
    attrs += _ipp_attr(0x42, "job-name",                    b"Color Flush Page")
    attrs += _ipp_attr(0x49, "document-format",             doc_format)
    attrs += b"\x03"  # end-of-attributes-tag

    return header + attrs + doc_data


def pdf_to_jpeg(pdf_path: Path) -> bytes | None:
    """Convert the first page of a PDF to JPEG using Ghostscript."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [
                "gs", "-dNOPAUSE", "-dBATCH", "-dSAFER",
                "-sDEVICE=jpeg", "-r300", "-dJPEGQ=95",
                "-dFirstPage=1", "-dLastPage=1",
                f"-sOutputFile={tmp_path}",
                str(pdf_path),
            ],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            log.error(f"Ghostscript failed: {result.stderr.decode()[:300]}")
            return None
        return Path(tmp_path).read_bytes()
    except FileNotFoundError:
        log.error("Ghostscript (gs) not found — is it installed in the container?")
        return None
    except Exception as e:
        log.error(f"PDF conversion error: {e}")
        return None
    finally:
        try:
            Path(tmp_path).unlink()
        except Exception:
            pass


def print_flush_page() -> bool:
    """Convert the flush PDF to JPEG and send it to the printer via IPP."""
    pdf_path = Path(FLUSH_PDF)
    if not pdf_path.exists():
        log.error(f"Flush PDF not found at {FLUSH_PDF}")
        return False

    log.info("Converting PDF to JPEG for IPP transmission…")
    jpeg_data = pdf_to_jpeg(pdf_path)
    if not jpeg_data:
        return False
    log.info(f"Converted — {len(jpeg_data) // 1024} KB JPEG ready")

    ipp_body = build_print_job_request(PRINTER_URI, jpeg_data, doc_format=b"image/jpeg")

    try:
        resp = requests.post(
            PRINTER_URL,
            data=ipp_body,
            headers={"Content-Type": "application/ipp"},
            timeout=30,
        )
    except requests.exceptions.ConnectionError:
        log.error(f"Cannot reach printer at {PRINTER_IP}:{PRINTER_PORT} — is it on?")
        return False
    except Exception as e:
        log.error(f"IPP request failed: {e}")
        return False

    if resp.status_code == 200:
        if len(resp.content) >= 4:
            ipp_status = struct.unpack(">H", resp.content[2:4])[0]
            if ipp_status == 0x0000:
                log.info("✓  Print job accepted by printer")
                _save_last_print()
                return True
            else:
                log.error(f"Printer returned IPP status 0x{ipp_status:04x}")
                return False
        log.info("✓  Print job sent (no detailed status)")
        _save_last_print()
        return True
    else:
        log.error(f"HTTP {resp.status_code} from printer")
        return False


# ── State (last print time) ───────────────────────────────────────────────────
def _save_last_print():
    try:
        STATE_FILE.write_text(
            '{"last_print": "' + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + '"}'
        )
    except Exception as e:
        log.warning(f"Could not save state: {e}")

def _load_last_print() -> str:
    try:
        if STATE_FILE.exists():
            data = STATE_FILE.read_text()
            import json
            return json.loads(data).get("last_print", "Never")
    except Exception:
        pass
    return "Never"


# ── Main run ──────────────────────────────────────────────────────────────────
def run():
    log.info("━" * 60)
    log.info("Printer Flush — starting run")
    log.info(f"Target: {PRINTER_URI}")
    log.info(f"PDF:    {FLUSH_PDF}")
    log.info("━" * 60)
    success = print_flush_page()
    status = "SUCCESS" if success else "FAILED"
    log.info(f"Run complete — {status}")
    log.info("━" * 60)


# ── Web UI ────────────────────────────────────────────────────────────────────
_run_lock = threading.Lock()
_web_app  = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Printer Flush</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🖨️</text></svg>">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0f0f0f; color: #d4d4d4; font-family: monospace; font-size: 13px; }
  header { display: flex; align-items: center; gap: 12px; padding: 12px 16px;
           background: #1a1a2e; border-bottom: 1px solid #333; }
  header h1 { font-size: 15px; color: #7eb8f7; flex: 1; }
  select { background: #222; color: #d4d4d4; border: 1px solid #444; border-radius: 4px;
           padding: 5px 8px; font-size: 13px; font-family: monospace; cursor: pointer; }
  label  { font-size: 12px; color: #888; }
  button { padding: 7px 18px; border: none; border-radius: 4px; cursor: pointer;
           font-size: 13px; font-family: monospace; }
  #btn-run   { background: #4caf50; color: #000; }
  #btn-run:disabled { background: #555; color: #888; cursor: default; }
  #btn-clear { background: #444; color: #ccc; }
  #status    { font-size: 12px; color: #888; }
  #last-print { font-size: 12px; color: #5c9e5c; }
  #log { padding: 10px 16px; height: calc(100vh - 50px); overflow-y: auto;
         white-space: pre-wrap; word-break: break-all; }
  .INFO    { color: #d4d4d4; }
  .WARNING { color: #e5c07b; }
  .ERROR   { color: #e06c75; }
  .DEBUG   { color: #666; }
</style>
</head>
<body>
<header>
  <h1>🖨️ Printer Flush</h1>
  <span id="last-print">Last print: loading…</span>
  <span id="status">connecting…</span>
  <label>Interval
    <select id="sel-interval" onchange="saveConfig()">
      <option value="1">Every 1 day</option>
      <option value="5">Every 5 days</option>
      <option value="7">Every 7 days</option>
      <option value="10" selected>Every 10 days</option>
      <option value="14">Every 14 days</option>
      <option value="0">Manual only</option>
    </select>
  </label>
  <button id="btn-clear" onclick="clearLog()">Clear</button>
  <button id="btn-run"   onclick="triggerRun()">▶ Print Now</button>
</header>
<div id="log"></div>
<script>
  let offset = 0;
  const logEl    = document.getElementById('log');
  const statusEl = document.getElementById('status');
  const lastEl   = document.getElementById('last-print');
  const btnRun   = document.getElementById('btn-run');
  const selInt   = document.getElementById('sel-interval');

  async function loadConfig() {
    try {
      const r = await fetch('/config');
      const d = await r.json();
      selInt.value = String(d.run_interval_days);
    } catch(e) {}
  }

  async function saveConfig() {
    await fetch('/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ run_interval_days: parseInt(selInt.value) })
    });
  }

  function appendLines(lines) {
    lines.forEach(l => {
      const span = document.createElement('span');
      span.className = l.l;
      span.textContent = l.m + '\\n';
      logEl.appendChild(span);
    });
    logEl.scrollTop = logEl.scrollHeight;
  }

  async function poll() {
    try {
      const r = await fetch('/logs?since=' + offset);
      const d = await r.json();
      if (d.lines.length) { appendLines(d.lines); offset = d.total; }
      statusEl.textContent = d.running ? 'printing…' : 'idle';
      lastEl.textContent   = 'Last print: ' + d.last_print;
      btnRun.disabled      = d.running;
    } catch(e) { statusEl.textContent = 'disconnected'; }
  }

  function clearLog() { logEl.innerHTML = ''; offset = 0; }

  async function triggerRun() {
    btnRun.disabled = true;
    statusEl.textContent = 'starting…';
    await fetch('/run', { method: 'POST' });
  }

  loadConfig();
  poll();
  setInterval(poll, 3000);
</script>
</body>
</html>"""

@_web_app.route("/")
def _index():
    return _HTML

@_web_app.route("/logs")
def _logs():
    since = int(flask_request.args.get("since", 0))
    buf   = list(LOG_BUFFER)
    return jsonify({
        "lines":      buf[since:],
        "total":      len(buf),
        "running":    _run_lock.locked(),
        "last_print": _load_last_print(),
    })

@_web_app.route("/config", methods=["GET"])
def _get_config():
    return jsonify({"run_interval_days": RUN_INTERVAL_DAYS})

def _update_env_file(key: str, value: str):
    p = Path(ENV_FILE)
    if not p.exists():
        return
    lines = p.read_text().splitlines(keepends=True)
    found = False
    for i, line in enumerate(lines):
        if re.match(rf"^{key}\s*=", line):
            lines[i] = f"{key}={value}\n"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}\n")
    p.write_text("".join(lines))

@_web_app.route("/config", methods=["POST"])
def _set_config():
    global RUN_INTERVAL_DAYS
    data = flask_request.get_json(force=True)
    if "run_interval_days" in data:
        val = int(data["run_interval_days"])
        RUN_INTERVAL_DAYS = val
        _update_env_file("RUN_INTERVAL_DAYS", str(val))
        schedule.clear()
        if val > 0:
            schedule.every(val).days.do(run)
            log.info(f"Schedule updated — flushing every {val} day(s)")
        else:
            log.info("Schedule cleared — manual only")
    return jsonify({"run_interval_days": RUN_INTERVAL_DAYS})

@_web_app.route("/run", methods=["POST"])
def _trigger_run():
    if _run_lock.locked():
        return jsonify({"status": "already running"}), 409
    def _do():
        with _run_lock:
            run()
    threading.Thread(target=_do, daemon=True).start()
    return jsonify({"status": "started"})

def _start_web():
    _web_app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    threading.Thread(target=_start_web, daemon=True).start()
    log.info(f"Web UI available at http://localhost:{WEB_PORT}")

    if RUN_INTERVAL_DAYS > 0:
        log.info(f"Scheduling flush every {RUN_INTERVAL_DAYS} day(s)")
        schedule.every(RUN_INTERVAL_DAYS).days.do(run)
        while True:
            schedule.run_pending()
            time.sleep(60)
