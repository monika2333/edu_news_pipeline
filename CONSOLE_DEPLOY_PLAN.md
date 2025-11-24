# Edu News Console Deployment Plan

This document summarizes how to run the FastAPI console (article search, dashboard, etc.) so it can be accessed from other machines securely.

---

## 1. Service Overview

- Entry point: `python run_console.py` (loads `src.console.app:app`, FastAPI + Uvicorn).
- Auth: Basic auth via `CONSOLE_BASIC_USERNAME` / `CONSOLE_BASIC_PASSWORD` **or** bearer token `CONSOLE_API_TOKEN` (set in `.env.local`).
- Purpose: Manual monitoring (dashboard), trigger runs, inspect/export summaries via `/articles/search`.

---

## 2. Prepare Environment

1. Copy `.env.local` template and ensure at minimum:
   ```
   DB_HOST=...
   DB_PORT=...
   DB_NAME=...
   DB_USER=...
   DB_PASSWORD=...
   CONSOLE_BASIC_USERNAME=admin
   CONSOLE_BASIC_PASSWORD=strong-password
   ```
2. Install dependencies once: `pip install -r requirements.txt` inside the project virtualenv.
3. Test locally: `python run_console.py` → open `http://127.0.0.1:8000/dashboard`.

---

## 3. Run as a Service

### Option A – Systemd (Linux)

1. Create service file `/etc/systemd/system/edu-news-console.service`:
   ```ini
   [Unit]
   Description=Edu News Console
   After=network.target

   [Service]
   WorkingDirectory=/srv/edu_news_pipeline
   ExecStart=/srv/edu_news_pipeline/.venv/bin/python run_console.py
   EnvironmentFile=/srv/edu_news_pipeline/.env.local
   Restart=on-failure

   [Install]
   WantedBy=multi-user.target
   ```
2. `sudo systemctl daemon-reload && sudo systemctl enable --now edu-news-console`.

### Option B – Windows Service (Task Scheduler)

1. Use `scripts\register_console_task.ps1 -TaskName EduNews_Console` to register a startup task that calls `scripts\run_console_service.ps1`. Override `-PythonPath` / `-LogPath` if needed.
2. Task Scheduler will run PowerShell as SYSTEM (or chosen account) and keep restarting the console if it exits unexpectedly.

### Option C – Docker Compose

1. Create `Dockerfile` with uvicorn + project files.
2. Use `docker-compose.yml` with:
   ```yaml
   services:
     console:
       build: .
       command: uvicorn src.console.app:app --host 0.0.0.0 --port 8000
       env_file: .env.local
       ports:
         - "8000:8000"
   ```

---

## 4. Network Exposure

1. Keep Uvicorn listening on `0.0.0.0:8000`.
2. Open firewall / security group for the chosen port (e.g., 8000). Better: expose only internally (VPN) or via reverse proxy.
3. **Recommended**: place Nginx/Traefik in front:
   - Terminate HTTPS (Let’s Encrypt cert).
   - Reverse proxy `https://console.yourdomain.com` → `http://127.0.0.1:8000`.
   - Enforce HTTP Basic auth headers if not handled by the app (defense in depth).

Example Nginx snippet:
```nginx
server {
    listen 443 ssl;
    server_name console.example.com;
    ssl_certificate ...;
    ssl_certificate_key ...;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

4. If you cannot use a proxy, at least bind Uvicorn to HTTPS via stunnel/Caddy or run `uvicorn ... --ssl-keyfile ... --ssl-certfile ...`.

---

## 5. Access from Other Machines

1. Ensure the server’s IP / DNS is reachable from the client machine.
2. Navigate to `https://console.example.com/articles/search` (or `http://server-ip:8000/...` if only testing).
3. Enter the Basic Auth credentials when prompted.
4. Use `/api/articles/search` for programmatic access (Bearer token recommended).

---

## 6. Security Checklist

- [ ] Strong Basic Auth or API token configured.
- [ ] HTTPS enabled (reverse proxy or equivalent).
- [ ] Port restricted to trusted networks or VPN where possible.
- [ ] Regularly patch dependencies (`pip install -r requirements.txt --upgrade`).
- [ ] Monitor logs (`uvicorn` log output, system journal, reverse proxy access logs).
- [ ] Rotate credentials and backup `.env.local` securely.

---

## 7. Operational Notes

- Restart service after updating code: e.g., `sudo systemctl restart edu-news-console`.
- For zero-downtime upgrades, consider running behind a process manager that supports reload (e.g., gunicorn with Uvicorn workers).
- Logs by default go to stdout / journal; configure log files via supervisor/systemd `StandardOutput=append:/var/log/edu-console.log`.
- If the console and crawlers share the same repo, pull latest commits and redeploy the service to pick up template changes.

---

This plan should cover turning the local console into a multi-user web portal reachable from other machines while keeping it secure and maintainable. Update as your infrastructure evolves.
