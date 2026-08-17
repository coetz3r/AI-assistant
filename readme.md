# AI-Assistant Web Interface

A lightweight, responsive web interface built with vanilla HTML and CSS to interact with local AI models and serve web controls.

## Features

- **Responsive UI:** Dynamic layout designed for mobile and desktop screens.
- **Custom Overlays:** Built-in floating controls and preview panel.
- **Automated Deployment:** Integrated Git workflow with scheduled background server synchronization.

## Local Server Setup

To run the local server manually:

python3 -m http.server 8000 --bind 0.0.0.0

Access the interface on your local network or Tailscale mesh:

http://<SERVER-IP>:8000

## Auto-Pull Cron Configuration

To keep the server automatically updated with the latest GitHub pushes, run the included auto_pull.sh script via cron:

* * * * * /home/piet3r/AI-assistant/auto_pull.sh