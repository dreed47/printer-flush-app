# Printer Flush

A lightweight Docker container that automatically sends a color flush page to a network printer on a configurable schedule. Keeps ink nozzles from drying out when a printer sits idle for extended periods.

No CUPS required — prints directly over the network using the IPP protocol.

## Features

- **Scheduled flush** — prints automatically every N days (configurable)
- **Web UI** — monitor logs, change the interval, and trigger a print manually
- **IPP direct printing** — talks directly to the printer over the network; no printer drivers or CUPS needed
- **Persistent state** — remembers the last print time across container restarts
- **Docker-based** — runs anywhere Docker is available

## Requirements

- Docker and Docker Compose
- A network-connected printer that supports IPP (port 631)
- A color flush PDF placed in the `./data/` directory

## Setup

**1. Clone the repository**

```bash
git clone https://github.com/dreed47/printer-flush-app.git
cd printer-flush-app
```

**2. Add your flush PDF**

Place your color flush page PDF at:

```
data/printer-color-flush.pdf
```

**3. Configure the environment**

Copy the example and edit to match your printer:

```bash
cp .env.example .env   # or edit .env directly
```

| Variable | Default | Description |
|---|---|---|
| `PRINTER_IP` | `192.168.86.99` | Printer's IP address on your network |
| `PRINTER_PORT` | `631` | IPP port (631 is standard) |
| `PRINTER_PATH` | `/ipp/print` | IPP endpoint path |
| `FLUSH_PDF` | `/data/printer-color-flush.pdf` | Path to the flush PDF inside the container |
| `RUN_INTERVAL_DAYS` | `10` | Days between automatic flushes (`0` = manual only) |
| `PORT` | `7841` | Host port for the web UI |

**4. Start the container**

```bash
docker compose up -d
```

The web UI will be available at `http://localhost:7841` (or whichever `PORT` you set).

## Web UI

The web UI provides:

- **Live log stream** — real-time output from the application
- **Last print time** — shows when the printer was last flushed
- **Interval selector** — change the flush schedule on the fly (persisted to `.env`)
- **Print Now button** — trigger an immediate flush manually

## Updating

```bash
./update.sh             # pull latest code and rebuild
./update.sh --no-cache  # full clean rebuild
```

## How It Works

1. On the configured schedule (or when triggered via the web UI), the app converts the flush PDF to a JPEG using Ghostscript.
2. The JPEG is wrapped in a minimal IPP 1.1 `Print-Job` request and sent directly to the printer over HTTP.
3. The last print timestamp is saved to `data/last_print.json` so state survives container restarts.

## Project Structure

```
printer-flush-app/
├── printer.py            # Main application
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env                  # Local config (not committed)
├── update.sh             # Helper to pull and rebuild
└── data/
    ├── printer-color-flush.pdf   # Your flush page (add this)
    └── last_print.json           # Auto-generated state file
```

## License

MIT
