# Decisions

- **Python 3.12** – The brain is implemented in Python 3.12 and runs in a slim Alpine image.
- **ProtonMail Bridge sidecar** – All email traffic is handled by ProtonMail Bridge to avoid exposing SMTP credentials.
- **VPN sidecar** – A ProtonVPN gluetun container provides outbound internet access for web scraping tasks.
- **No host port exposure** – The application is only reachable via Docker networks. The MCP API is internal.
- **External configuration** – Sensitive data is stored in Docker secrets (`secrets/*`).
- **TAMU place resolution (2026-08-30)** – Country names, `multiple locations`, and
  other-county offices are dropped quietly. Only strings that look like local
  venues are geocoded; `no_match` on those still emails, now with title and location.
- **event_watch cadence (2026-08-30)** – All 26 kinds run Tue/Fri America/Chicago,
  03:40–09:50, one kind per job. See `modules/event_watch/README.md`.
