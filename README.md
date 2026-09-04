<div align="center" width="100%">
    <img src="static/favicon/transparent.png" width="128" alt="" />
</div>

# MouseSearch
MouseSearch is a self-hosted web application that provides a clean, fast search interface for MyAnonamouse (MAM). It connects directly to the MAM API for searching and supports modular torrent client integrations (qBittorrent, Deluge, Transmission, rTorrent) for one-click downloading, bridging the gap between your favorite tracker and your download client.



## Key Features

* **MAM Search:** Full-text search for torrents on MyAnonamouse.
* **MAM-Only Proxy Routing:** Route MyAnonamouse requests through an HTTP/HTTPS/SOCKS proxy while keeping the MouseSearch UI on its normal network path.
* **Advanced Filtering:** Filter by title, author, narrator, media type, language, and advanced tracker filters (e.g., Freeleech, VIP, Active).
* **Customizable Search Results:** Toggle specific columns (Series, Narrator, File Type, Seeders, etc.) to tailor the results view to your needs.
* **One-Click Downloading:** Send torrents directly to your torrent client (supports qBittorrent, Deluge, Transmission, and rTorrent), assigning a category from the UI.
* **Live Status Dashboards:**
    * View your MAM user stats (username, ratio, bonus points, etc.) directly in the app.
    * Check the connection status to both MAM and your torrent client.
* **Dynamic IP Updater:** Automatically checks your server's public IP and updates MAM's "Dynamic Seedbox IP" setting if a change is detected. This is ideal for home servers with dynamic IPs.
* **VIP Auto-Buy:** Automatically tops up your MAM VIP credit using bonus points on a configurable schedule. One-click manual top-up button also available.
* **Upload Credit Auto-Buy:** Intelligent upload credit management with multiple modes:
    * Auto-purchase when ratio falls below threshold (configurable, MAM minimum is 1.0)
    * Auto-purchase when upload buffer (uploaded - downloaded) is too low
    * **[NEW]** Auto-purchase when bonus points exceed a threshold (spends excess points to build buffer)
    * Pre-download buffer check - prevents downloads larger than available buffer and prompts for upload credit purchase
    * Manual purchase interface with preset amounts, custom whole-number GB amounts (`>= 50`), or the upstream max affordable option
* **Freeleech Tools:** VIP Freeleech awareness in search results, a bonus-store wedge purchase button in the MAM status area, a personal Freeleech wedge button in the download confirmation dialog, and an optional setting to auto-spend an existing wedge on download.
* **Enhanced Details UI:** Responsive cards, improved book details layout, a high-res cover lightbox, and a **MediaInfo Inspector** tree for viewing technical file metadata.
* **Live Torrent Polling:** After adding a torrent, the UI polls your torrent client to show its download status (e.g., "Downloading 50%", "Seeding") in real-time in results and the book details modal. Designates previously downloaded torrents as "Downloaded".
* **Template-Based Organization Paths:** Define a default relative path template (e.g., `{Author}/{Title}` or `{Category}/{Genre}/{Author}/{Title}`) with token helpers and live preview in Settings.
* **[BETA] Auto-Organization:** (See details below) Automatically organizes completed audiobooks from your download folder to a clean library structure (e.g., `Author/Title/file.m4b`) using either hardlinks (instant, no space used) or file copies, with a default destination path plus optional media-type-specific destination paths.

## Technology Stack

* **Backend:** **Quart**
* **Frontend:** **Bootstrap 5** & JavaScript
* **Containerization:** **Docker**
* **APIs:** MyAnonamouse (MAM) & Modular Torrent Clients

## Progressive Web App (PWA) Support

MouseSearch is designed to be **mobile-friendly** and supports **Progressive Web App (PWA)** functionality. This means you can install and run it like a **native app** directly on your phone or desktop for an integrated user experience.

## Installation & Configuration

MouseSearch can be deployed in two ways:
1. **Docker (Recommended)** - Use the pre-built image from Docker Hub
2. **Bare Metal** - Run directly on your system using the provided launch script

---

## Installation Method 1: Docker (Recommended)

### Prerequisites

* Docker and Docker Compose

### Setup Steps

1.  Create a project directory:
    ```bash
    mkdir mousesearch && cd mousesearch
    ```

2.  (Optional) Download the example environment file:
    ```bash
    curl -o .env https://raw.githubusercontent.com/sevenlayercookie/MouseSearch/main/.env.example
    ```

3.  Copy the example Compose file:
    ```bash
    cp compose-example.yaml compose.yaml
    ```

4.  (Optional) Edit `.env` with your settings - alternatively, configure through the web interface after launch

5.  Start the application:
    ```bash
    docker compose up -d
    ```

The application will be available at `http://<your-server-ip>:5000`.

---

## Installation Method 2: Bare Metal

### Prerequisites

* Python 3.12 or higher
* pip

### Setup Steps

1.  Clone this repository:
    ```bash
    git clone https://github.com/sevenlayercookie/MouseSearch.git
    cd MouseSearch
    ```

2.  Create a virtual environment:
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    ```

3.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

3.  (Optional) Create your environment file:
    ```bash
    cp .env.example .env
    ```

4.  (Optional) Edit `.env` with your settings - alternatively, configure through the web interface after launch

5.  Launch the application:
    ```bash
    ./launch.sh
    ```

    Or specify a custom port:
    ```bash
    ./launch.sh --port 8080
    ```

The application will be available at `http://<your-server-ip>:5000` (or your custom port).

### Serving Under a URL Prefix (Reverse Proxy Subpath)

By default the application is served from the root of its host (`http://<your-server-ip>:5000`). If you want to serve it under a subpath behind a reverse proxy (e.g., `https://<your-domain>/mousesearch/`, common on shared seedboxes), launch it with `--root-path`:

```bash
./launch.sh --port 8080 --root-path /mousesearch
```

You can also set `ROOT_PATH=/mousesearch` in `.env` instead of passing the flag. When running behind a reverse proxy on the same machine, add `--bind 127.0.0.1` so the app is only reachable through the proxy.

Then point your reverse proxy at the app **without stripping the prefix** — the app removes it itself via the ASGI `root_path` mechanism. Example Nginx location:

```nginx
location /mousesearch/ {
    proxy_pass          http://127.0.0.1:8080;   # no trailing slash: keep the prefix
    proxy_http_version  1.1;
    proxy_set_header    Host                $http_host;
    proxy_set_header    X-Forwarded-Proto   $scheme;
    proxy_set_header    X-Forwarded-For     $proxy_add_x_forwarded_for;

    # Required for live status updates (Server-Sent Events)
    proxy_buffering     off;
    proxy_read_timeout  1h;
}
```

Notes:

* The prefix must not have a trailing slash (`/mousesearch`, not `/mousesearch/`).
* Without `--root-path`, nothing changes — the app behaves exactly as before.
* `proxy_buffering off` and the long `proxy_read_timeout` matter: MouseSearch streams live torrent status over Server-Sent Events (`/events`), which stalls behind a buffering proxy and is cut off by Nginx's default 60-second read timeout.

---

## Configuration

**Environment variables are completely optional.** You can configure all settings directly through the web interface after launching the application.

> **Settings saved in `config.json` (i.e. through the web interface) override environment values. To force env-only configuration, delete `config.json`**

### Environment Variables (`.env`)

Open the `.env` file and configure the following settings.

| Variable | Required | Description |
| :--- | :--- | :--- |
| `QUART_SECRET_KEY` | **Yes** | A long, random string for session security. You can generate one with `openssl rand -hex 32` (or just smash on the keyboard a bit) |
| `MAM_ID` | **Yes** | Your initial `mam_id` cookie value from [MyAnonamouse](https://www.myanonamouse.net/preferences/index.php?view=security). MouseSearch maintains its own session: it automatically adopts rotated `mam_id` values returned by MAM and persists the latest one. Use a session dedicated to MouseSearch so it does not share a cookie chain with other apps. |
| `MAM_PROXY_ENABLED` | No | Enables the MAM-specific proxy feature. Defaults to `false`. |
| `MAM_PROXY_URL` | No | Optional outbound proxy used for MAM requests, such as `http://gluetun:8888`, `http://user:pass@proxy:8080`, or `socks5h://user:pass@proxy:1080`. |
| `MAM_PROXY_ONLY` | No | Keeps the proxy scoped to MAM traffic when `true`. Defaults to `true`. |
| `MAM_PROXY_FALLBACK_DIRECT` | No | If the configured MAM proxy is unavailable, fall back to a direct connection when `true`. Defaults to `true`. |

### MAM Proxy Routing

MouseSearch can route only MyAnonamouse traffic through a proxy while leaving the web UI reachable on its normal LAN or Docker network path. This is useful when your torrent client is behind Gluetun or another VPN/proxy gateway, but you do not want MouseSearch itself to share that network namespace.

Why this matters:

* MouseSearch depends on a valid `mam_id` cookie for MAM requests.
* Many setups also rely on MAM's Dynamic Seedbox IP update flow so downloads are allowed from the expected public IP.
* MAM download permissions are IP-sensitive: in practice, your browser session, MouseSearch, and torrent client often need to agree on which public IP is being presented to MAM.
* If MouseSearch talks to MAM from a different public IP than the rest of your stack, MAM cookie auth may still work, but downloads or Dynamic Seedbox API behavior can become inconsistent.
* A MAM-only proxy lets MouseSearch keep its UI on the normal network path while sending MAM-bound traffic through the same VPN/proxy egress as qBittorrent or another downloader.

Typical example:

```env
MAM_PROXY_ENABLED=true
MAM_PROXY_URL=http://gluetun:8888
MAM_PROXY_ONLY=true
MAM_PROXY_FALLBACK_DIRECT=true
```

Notes:

* `MAM_PROXY_ENABLED=true` turns the feature on.
* `MAM_PROXY_URL` supports `http://`, `https://`, `socks5://`, and `socks5h://`.
* Proxy auth can be embedded inline, for example `http://user:pass@gluetun:8888`.
* `MAM_PROXY_ONLY=true` keeps non-MAM traffic off the proxy.
* `MAM_PROXY_FALLBACK_DIRECT=true` allows MAM requests to fall back direct if the proxy is down. Set it to `false` if you want fail-closed behavior instead.
* If you use SOCKS and want remote DNS resolution, prefer `socks5h://` over `socks5://`.

### Torrent Client Configuration

MouseSearch supports modular torrent clients. Currently supported: **qBittorrent**, **Deluge**, **Transmission**, and **rTorrent**.

| Variable | Required | Description |
| :--- | :--- | :--- |
| `TORRENT_CLIENT_TYPE` | No | The type of torrent client (default: `qbittorrent`). Options: `qbittorrent`, `deluge`, `transmission`, `rtorrent`. |
| `TORRENT_CLIENT_URL` | **Yes** | The full URL to your torrent client WebUI (e.g., `http://192.168.1.10:8080` or `http://qbittorrent:6767` if on the same Docker network). **rTorrent is the exception** — it needs an XML-RPC endpoint, not the ruTorrent WebUI URL. See [rTorrent / ruTorrent endpoints](#rtorrent--rutorrent-endpoints). |
| `TORRENT_CLIENT_USERNAME` | **Yes** | Your torrent client username. |
| `TORRENT_CLIENT_PASSWORD` | **Yes** | Your torrent client password. |
| `TORRENT_CLIENT_CATEGORY` | No | (Optional) A default category to assign to downloads (e.g., `audiobooks`). |
| `QBITTORRENT_VERIFY_WEBUI_CERTIFICATE` | No | qBittorrent only. Defaults to `true`. Set to `false` if the qBittorrent WebUI uses HTTPS with a self-signed or otherwise untrusted certificate. |
| `QB_FORCE_START` | No | qBittorrent only. Set to `true` to force-start each torrent immediately after MouseSearch adds it. Defaults to `false`. |
| `RTORRENT_DIGEST_AUTH` | No | rTorrent only. Set to `true` to use HTTP Digest authentication instead of Basic auth. Required by some seedbox providers. Defaults to `false`. |

#### rTorrent / ruTorrent endpoints

MouseSearch talks to rTorrent over **XML-RPC**, so `TORRENT_CLIENT_URL` must point at an XML-RPC endpoint. Pointing it at the ruTorrent WebUI itself (e.g. `https://seedbox.example.com/rutorrent/`) will not work — that URL serves HTML, and MouseSearch will fail to parse the response.

Which endpoint to use depends on how your setup exposes rTorrent:

| Setup | `TORRENT_CLIENT_URL` |
| :--- | :--- |
| Self-hosted, XML-RPC proxied by Nginx/Apache (the usual `scgi_pass` / `mod_scgi` setup) | `http://<host>/RPC2` |
| Local rTorrent SCGI socket (`network.scgi.open_local = /path/to/rpc.sock` in `.rtorrent.rc`) | `scgi+unix:///path/to/rpc.sock` |
| Shared seedbox running ruTorrent, no direct XML-RPC exposed | `https://<seedbox address>/rutorrent/plugins/httprpc/action.php` |
| Some providers give each user a named path | `https://<seedbox address>/<username>/rutorrent/plugins/httprpc/action.php` |

Most shared seedbox providers do **not** expose a direct XML-RPC interface, so the ruTorrent `httprpc` plugin endpoint (`plugins/httprpc/action.php`) is typically the one that works. It is served by ruTorrent itself, which means it inherits ruTorrent's HTTP authentication — set `TORRENT_CLIENT_USERNAME` / `TORRENT_CLIENT_PASSWORD` to the same credentials you use to log into ruTorrent in a browser.

Notes:

- With a `scgi+unix://` URL, MouseSearch talks to rTorrent's native SCGI listener directly — no webserver involved. Username/password and `RTORRENT_DIGEST_AUTH` do not apply, and the MouseSearch process needs read/write permission on the socket file.
- If your provider's panel challenges you with HTTP Digest auth rather than Basic (common on seedboxes), also set `RTORRENT_DIGEST_AUTH=true`.
- The `httprpc` plugin must be enabled in ruTorrent. If it is not in your ruTorrent plugin list, ask your provider to enable it or check whether they document a dedicated XML-RPC URL.
- To confirm an endpoint before configuring MouseSearch:
  ```bash
  curl -u '<user>:<pass>' --data-binary \
    "<?xml version='1.0'?><methodCall><methodName>system.client_version</methodName><params></params></methodCall>" \
    -H 'Content-Type: text/xml' \
    'https://<seedbox address>/rutorrent/plugins/httprpc/action.php'
  ```
  A working endpoint returns an XML `<methodResponse>` containing your rTorrent version. HTML back means you have the wrong URL; a `401` means the credentials or auth scheme (see `RTORRENT_DIGEST_AUTH`) are wrong.

### Hardcover API permissions

MouseSearch uses a server-side Hardcover Personal Access Token (PAT). For the complete integration, including book enrichment and reading-status controls, grant these scopes:

* `read:catalog` — search and retrieve book, edition, author, and series metadata
* `read:me:content` — identify the signed-in Hardcover account without exposing email or role data
* `read:library` — display existing library and reading-status records
* `write:library` — add, update, and remove reading statuses

[Create a Hardcover PAT with the recommended MouseSearch scopes](https://hardcover.app/account/api/keys/new?scope=read%3Acatalog+read%3Ame%3Acontent+read%3Alibrary+write%3Alibrary).

For book enrichment only, the minimum required scope is `read:catalog:search`. Library status display and changes will remain unavailable without the additional account and library scopes. MouseSearch does not require the unrestricted `all` scope.

### Additional Configuration

| Variable | Required | Description |
| :--- | :--- | :--- |
| `DATA_PATH` | No | Directory path for storing app data files (config.json, database.json, ip_state.json). Defaults to `./data`. |
| `ENABLE_DYNAMIC_IP_UPDATE` | No | Set to `true` to enable automatic IP checking and updating of MAM's "Dynamic Seedbox IP" setting. Defaults to `false`. |
| `DYNAMIC_IP_CHECK_INTERVAL_SECONDS` | No | Number of seconds between host IP/ASN checks when automatic MAM updates are enabled. MouseSearch only calls MAM when a refresh is actually needed. Defaults to `300`. |
| `DYNAMIC_IP_STALE_RESPONSE_SECONDS` | No | Maximum age of the last successful MAM update response before MouseSearch refreshes it even if IP and ASN have not changed. Defaults to `86400`. |
| `DYNAMIC_IP_UPDATE_INTERVAL_HOURS` | Legacy fallback | Older hour-based interval. If `DYNAMIC_IP_CHECK_INTERVAL_SECONDS` is unset, MouseSearch converts this value to seconds for backward compatibility. |
| `AUTO_BUY_VIP` | No | Set to `true` to enable automatic VIP credit top-ups using bonus points. Defaults to `false`. |
| `AUTO_BUY_VIP_INTERVAL_HOURS` | No | Number of hours between automatic VIP purchases (only applies if `AUTO_BUY_VIP` is `true`). Defaults to `24`. |
| `AUTO_BUY_UPLOAD_ON_RATIO` | No | Set to `true` to enable automatic upload credit purchase when ratio falls below threshold. Defaults to `false`. |
| `AUTO_BUY_UPLOAD_RATIO_THRESHOLD` | No | If ratio falls below this value, automatically purchase upload credit. MAM requires minimum 1.0 ratio. Defaults to `1.5`. |
| `AUTO_BUY_UPLOAD_RATIO_AMOUNT` | No | Amount of upload credit (in GB) to purchase when ratio threshold is hit. Use whole numbers `>= 50`. Defaults to `50`. |
| `AUTO_BUY_UPLOAD_ON_BUFFER` | No | Set to `true` to enable automatic upload credit purchase when buffer is too low. Defaults to `false`. |
| `AUTO_BUY_UPLOAD_BUFFER_THRESHOLD` | No | If upload buffer (uploaded - downloaded) falls below this many GB, automatically purchase upload credit. Defaults to `10`. |
| `AUTO_BUY_UPLOAD_BUFFER_AMOUNT` | No | Amount of upload credit (in GB) to purchase when buffer threshold is hit. Use whole numbers `>= 50`. Defaults to `50`. |
| `AUTO_BUY_UPLOAD_ON_BONUS` | No | Set to `true` to enable automatic upload credit purchase when bonus points exceed a threshold. Defaults to `false`. |
| `AUTO_BUY_UPLOAD_BONUS_THRESHOLD` | No | If bonus points are at or above this value, auto-purchase upload credit until below threshold. Defaults to `5000`. |
| `AUTO_BUY_UPLOAD_BONUS_AMOUNT` | No | Amount of upload credit (in GB) to purchase per bonus-threshold check. Use whole numbers `>= 50`. Defaults to `50`. |
| `AUTO_BUY_UPLOAD_CHECK_INTERVAL_HOURS` | No | Number of hours between ratio/buffer/bonus checks (only applies if auto-buy upload is enabled). Defaults to `6`. |
| `AUTO_TASK_WEBHOOK_URL` | No | Optional webhook endpoint for auto-task notifications. When set, MouseSearch can notify on supported automatic task success/failure events. |
| `AUTO_TASK_WEBHOOK_EVENTS` | No | Optional event allowlist for webhook notifications. Accepts a JSON array or comma-separated list such as `["auto_buy_vip"]` or `auto_buy_vip,auto_buy_upload_bonus`. If unset, all supported auto-task webhook events are sent. |
| `AUTO_TASK_WEBHOOK_METHOD` | No | Webhook method: `POST` or `GET`. Defaults to `POST`. |
| `AUTO_TASK_WEBHOOK_PARAMS` | No | Optional query parameters for the webhook. Accepts either a JSON object or a query-string template such as `source=mousesearch&event={event}&status={status}`. |
| `AUTO_TASK_WEBHOOK_BODY` | No | Optional POST body template. Accepts JSON or raw text. Ignored for `GET` requests. |
| `BLOCK_DOWNLOAD_ON_LOW_BUFFER` | No | Set to `true` to prevent downloads when torrent size exceeds available buffer (prompts user to purchase upload credit). Defaults to `true`. |
| `AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD` | No | Set to `true` to auto-attempt spending a personal Freeleech wedge you already own before each download add. If the wedge cannot be applied, the torrent is still added. Defaults to `false`. |
| `AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_ENABLED` | No | Set to `true` to only auto-spend a personal Freeleech wedge when the torrent size is greater than the configured minimum. Defaults to `false`. |
| `AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_MB` | No | Minimum torrent size, in MB, required before auto-spending a personal Freeleech wedge when the minimum-size gate is enabled. Defaults to `0`. |
| `HAPTICS_ENABLED` | No | Set to `true` to enable frontend haptic feedback where the browser/device supports it. Defaults to `true`. |
| `AUTO_ORGANIZE_ON_ADD` | No | Set to `true` to enable auto-organization when torrents are added. Defaults to `false`. |
| `AUTO_ORGANIZE_ON_SCHEDULE` | No | Set to `true` to enable scheduled auto-organization. Defaults to `false`. |
| `AUTO_ORGANIZE_INTERVAL_HOURS` | No | Number of hours between scheduled organization scans (only applies if `AUTO_ORGANIZE_ON_SCHEDULE` is `true`). Defaults to `1`. |
| `AUTO_ORGANIZE_USE_COPY` | No | Set to `true` to copy files instead of hardlinking. Useful if download/organize paths are on different filesystems. Defaults to `false`. |
| `DEFAULT_RELATIVE_PATH_TEMPLATE` | No | Default relative folder template used for organization paths. Supports `{Author}`, `{Series}`, `{SeriesNumber}`, `{Title}`, `{Category}`, `{Genre}`, and `{Format}` tokens. Defaults to `{Author}/{Title}`. |
| `ORGANIZED_PATH` | If auto-organization is enabled | The default *container* path for your organized library (e.g., `/downloads/organized/`). Additional destination paths can be configured from the Settings UI and assigned to media types. |
| `LOCAL_TORRENT_DOWNLOAD_PATH` | If auto-organization is enabled | The local path MouseSearch can access for completed torrent files (e.g., `/downloads/torrents/`). |
| `REMOTE_TORRENT_DOWNLOAD_PATH` | No | Optional remote/client-side view of that same download directory. Recommended when your torrent client runs in a different filesystem namespace, such as Docker vs bare metal. |
| `ENABLE_FILESYSTEM_THUMBNAIL_CACHE` | No | Set to `true` to enable filesystem caching of thumbnail images (stores in `DATA_PATH/cache/thumbnails`). Defaults to `true`. **Enable this if you experience slow thumbnail loading or suspect you're hitting MAM rate limits.** Cached thumbnails expire after 30 days. |
| `THUMBNAIL_CACHE_MAX_SIZE_MB` | No | Maximum cache size in megabytes (only applies when `ENABLE_FILESYSTEM_THUMBNAIL_CACHE` is enabled). Oldest files are deleted first when limit is exceeded. Defaults to `500`. |
| `MAX_SEARCH_RESULTS` | No | Maximum number of search results returned per query. Defaults to `50`. |
| `MAX_AUTOCOMPLETE_RESULTS` | No | Maximum number of autocomplete suggestions returned per query. Defaults to `20`. |
| `HARDCOVER_ENRICHMENT_ENABLED` | No | Enables server-side Hardcover enrichment for MAM search results. Defaults to `true`; requires `HARDCOVER_API_TOKEN`. |
| `HARDCOVER_API_TOKEN` | No | Hardcover GraphQL API token. Keep this server-side; it is never sent to browser code. Use the raw token; `Bearer ` is added automatically if omitted. |
| `HARDCOVER_API_URL` | No | Hardcover GraphQL endpoint. Defaults to `https://api.hardcover.app/v1/graphql`. |
| `HARDCOVER_USER_AGENT` | No | Descriptive User-Agent sent to Hardcover. Defaults to `MouseSearch Hardcover Enrichment`. |
| `HARDCOVER_RATE_LIMIT` | No | Shared global rate cap for all server-side Hardcover API requests, in requests per minute. Defaults to `60`; MouseSearch also honors the limits advertised in Hardcover's response headers. |
| `HARDCOVER_MATCH_THRESHOLD` | No | Fuzzy validation threshold on a 0-100 scale. Defaults to `78`. |
| `HARDCOVER_CONCURRENCY` | No | Maximum in-flight Hardcover enrichments. Defaults to `6`. |
| `HARDCOVER_SEARCH_PER_PAGE` | No | Hardcover candidates checked per search path. Defaults to `5`. |
| `RESULTS_DISPLAY_FIELDS` | No | List of fields to display in search results. Options: `date_uploaded`, `file_type`, `file_size`, `snatches`, `seeders`, `category`, `language`, `narrator`, `series`. |
| `RESULTS_SORT_MODE` | No | Initial search-results sort mode. The latest selection is persisted in `config.json`. Defaults to `quality_desc`. |
| `APP_LOG_LEVEL` | No | Application log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). Defaults to `INFO`. |
| `LOG_HTTP_REQUESTS` | No | Enables app-level HTTP request logging with sanitized query params. Defaults to `false`. |
| `LOG_HTTP_REQUESTS_INCLUDE_STATIC` | No | Includes `/static/*` and favicon requests in app-level request logs. Defaults to `false`. |
| `LOG_HTTP_REQUESTS_INCLUDE_EVENTS` | No | Includes `/events` (SSE) requests in app-level request logs. Defaults to `false`. |
| `ACCESS_LOGFILE` | No | Hypercorn raw access log destination (`/dev/null` disables, `-` logs to stdout). Defaults to `/dev/null`. |
| `PUID` | No | (Docker only) User ID to run the container as. Set to your host user's UID for correct file permissions. |
| `PGID` | No | (Docker only) Group ID to run the container as. Set to your host user's GID for correct file permissions. |

Legacy compatibility: `TORRENT_DOWNLOAD_PATH` is still accepted as an alias for `LOCAL_TORRENT_DOWNLOAD_PATH`, but new installs should use the new name.

### Auto-Task Webhook Templates

If `AUTO_TASK_WEBHOOK_URL` is set, MouseSearch sends webhook notifications for:

* `auto_buy_vip`
* `auto_buy_upload_ratio`
* `auto_buy_upload_buffer`
* `auto_buy_upload_bonus`
* `auto_update_ip`
* `auto_organize_on_download`
* `auto_organize_on_schedule`

Set `AUTO_TASK_WEBHOOK_EVENTS` if you only want a subset of those events.

If you do not set `AUTO_TASK_WEBHOOK_PARAMS` or `AUTO_TASK_WEBHOOK_BODY`, MouseSearch sends a default structured payload:

* `GET`: default event fields are sent as query parameters.
* `POST`: default event fields are sent as a JSON body.

Templates may use placeholders such as `{event}`, `{task}`, `{status}`, `{success}`, `{timestamp}`, `{amount}`, `{seedbonus}`, `{error}`, `{reason}`, `{threshold}`, `{purchase_size}`, `{purchase_count}`, `{starting_seedbonus}`, and `{summary}`. Missing fields render as empty strings.

Example `POST` webhook:

```env
AUTO_TASK_WEBHOOK_URL=https://hooks.example.com/mousesearch
AUTO_TASK_WEBHOOK_EVENTS=["auto_buy_vip","auto_buy_upload_ratio","auto_buy_upload_buffer","auto_buy_upload_bonus","auto_update_ip","auto_organize_on_download","auto_organize_on_schedule"]
AUTO_TASK_WEBHOOK_METHOD=POST
AUTO_TASK_WEBHOOK_PARAMS={"source":"mousesearch","event":"{event}","status":"{status}"}
AUTO_TASK_WEBHOOK_BODY={"status":"{status}","summary":"{summary}"}
```

Example `GET` webhook:

```env
AUTO_TASK_WEBHOOK_URL=https://hooks.example.com/mousesearch
AUTO_TASK_WEBHOOK_EVENTS=auto_buy_vip
AUTO_TASK_WEBHOOK_METHOD=GET
AUTO_TASK_WEBHOOK_PARAMS=source=mousesearch&event={event}&status={status}&summary={summary}
```

**About the `MAM_ID` session cookie:**

MouseSearch treats the configured `MAM_ID` as the starting cookie only. During normal API traffic it will use any newer `mam_id` returned by MAM and save that rotated value so restarts continue with the latest session cookie.

Because MAM rotates the `mam_id` on every request, give MouseSearch its own dedicated session (created on the [Security](https://www.myanonamouse.net/preferences/index.php?view=security) page) rather than sharing a session/cookie with another application — otherwise the two will invalidate each other's cookies and authentication will break intermittently.

If authentication fails because your server's public IP is not allowed for the session, MouseSearch surfaces the current public IP (with a copy button and a link to the security page) in **Settings → MyAnonaMouse Auth** so you can add it to the session's allowed IPs/ASNs.

**How to find your `MAM_ID`:**
1.  In any web browser, navigate to [Security](https://www.myanonamouse.net/preferences/index.php?view=security) on Myanonamouse
2.  Create a new session
    - IP address: run `curl icanhazip.com` from the server that will be hosting MouseSearch, and put output here
    - IP or ASN: `ASN` (ASN is more forgiving)
    - Dynamic Seedbox: choose `Yes` to allow MouseSearch to keep IP updated
    - Session Label: `MouseSearch`
3.  **IMPORTANT**: copy the `mam_id` value for configuring MouseSearch

### 4. Configure `compose.yaml`

Your `compose.yaml` file tells Docker how to run the app and, most importantly, where your files are. You **must** map your download and data directories.

Here is an example `compose.yaml`:

```yaml
# Copy this file to compose.yaml before running `docker compose up -d`.
services:
  mousesearch:
    image: sevenlayercookie/mousesearch:latest
    container_name: mousesearch
    restart: unless-stopped
    ports:
      - "5000:5000"
    volumes:
      - ./data:/data  # location that config, cache, and state files will be stored
      - /downloads:/downloads  # required if LOCAL_TORRENT_DOWNLOAD_PATH or ORGANIZED_PATH live under /downloads

      # If your torrent client reports a different in-container path, set
      # REMOTE_TORRENT_DOWNLOAD_PATH in .env to that client-visible path.

    env_file: .env  # optional: load environment variables from a file

    environment:
      - TZ=America/Chicago
      # Change these to match your host user (run 'id' in terminal to check)
      - PUID=${PUID:-1000}
      - PGID=${PGID:-1000}
```

**Note:** To build from source instead of using the pre-built image, replace `image: sevenlayercookie/mousesearch:latest` with `build: .` and ensure you've cloned the repository.

### 5. Run the Application

With your `.env` and `compose.yaml` files configured, start the application:

```bash
docker compose up -d
```

The application will be available at `http://<your-server-ip>:5000`.

## Usage

1.  Open the application in your browser.
2.  The app will show "NOT CONNECTED" for MAM and your torrent client.
3.  **Configure your settings:** You can configure all settings directly through the web interface, or use the `.env` file.
4.  Once configured, the dashboards should automatically update to "CONNECTED" and populate your user info.
4.  Use the search bar to find content.
5.  In the results, select a torrent category (if desired) and click "Download". A confirmation dialog will appear. If auto-organize is enabled, you can review/edit the relative organization path and choose a destination path before sending to your client.
6.  The torrent will be added, and a status badge will appear, polling your torrent client for live progress.

### Path Customization:
MouseSearch supports configurable default organization templates via Settings -> Directory Structure.

- **Template Editor**: Set `REL_PATH_TEMPLATE` from the UI using the tokens below, with quick-insert buttons and live preview.

| Token | Value | Example |
| --- | --- | --- |
| `{Author}` | Author name | `Frank Herbert` |
| `{Series}` | Series name (blank if the torrent has no series) | `Dune` |
| `{SeriesNumber}` | Position within the series | `1` |
| `{Title}` | Torrent title | `Dune` |
| `{Category}` | Media type from the MAM category | `Audiobooks`, `Ebooks` |
| `{Genre}` | MAM subcategory | `Science Fiction` |
| `{Format}` | File format, uppercased | `M4B`, `EPUB MOBI` |

  Tokens that have no value collapse out of the path rather than leaving an empty folder level, so `{Author}/{Series}/{Title}` yields `Frank Herbert/Dune` for a standalone book. To reproduce a `Audiobooks/Science Fiction/Frank Herbert/Dune` layout, use `{Category}/{Genre}/{Author}/{Title}`.
- **Environment Default**: Set `DEFAULT_RELATIVE_PATH_TEMPLATE` in `.env` to control the default template used by the app.
- **Per-Download Tag Builder**: When `AUTO_ORGANIZE_ON_ADD` is enabled, the download confirmation modal shows the same tag builder as Settings, seeded with your configured template and this torrent's values:
  - **Path Template** — the template for this one download. Tag buttons add a token, or remove it if it is already present; a tag with no value for this torrent (no series, no genre) is greyed out. The reset button restores your configured default.
  - **Relative Path** — the resolved path, freely editable for a one-off override. Changing the template rebuilds it.
  - Neither field affects your saved default; edits apply only to the download being confirmed.
- **Destination Selection**: In Settings → Auto-Organize, set a **Default Organized Destination Path** and optionally add extra destination paths that can be assigned as defaults per media type (Audiobooks, E-Books, Musicology, Radio).
- **Category-Based Destination Defaults**: In the download confirmation modal, the destination dropdown is auto-selected from your configured media-type destination when available; otherwise it falls back to the default organized destination path.
- **Download-Only Confirm**: When `AUTO_ORGANIZE_ON_ADD` is disabled, the same modal is used as a lightweight confirmation without path editing.

## [BETA] Auto-Organization Feature

This feature is designed to automate your media library. When enabled, it hard-links (default) or copies completed audio files from your "messy" download directory into a "clean" library directory, organized in subdirectories by `Author/Title` or `Author/Series/Title`.

It **defaults to hard links**, which means it takes up **no additional disk space**, and **it will not interfere with torrent seeding**.

### Configuration Options

You can control two separate aspects of auto-organization:

- **`AUTO_ORGANIZE_ON_ADD`**: Automatically organize files when torrents are added to your torrent client via the MouseSearch interface.
- **`AUTO_ORGANIZE_ON_SCHEDULE`**: Periodically check for unorganized files at a configurable interval (mainly used as a backup to the ON_ADD functionality).
- **`AUTO_ORGANIZE_INTERVAL_HOURS`**: How often (in hours) to run the scheduled organization scan (defaults to 1 hour).
- **`AUTO_ORGANIZE_USE_COPY`**: If set to true, MouseSearch will copy files instead of hardlinking them. Enable this if your downloads and organized library reside on different filesystems/disks.
- **Default Organized Destination Path (UI)**: Base destination path used for organization and as a fallback when no media-type mapping is configured.
- **Additional Destination Paths (UI)**: Optional extra destination roots; each can be marked as the default for one media type.

### How It Works

1.  When `AUTO_ORGANIZE_ON_ADD` is enabled and you add a torrent, MouseSearch calculates its infohash and saves the Author/Title metadata from Myanonamouse to `./data/database.json`.
2.  When `AUTO_ORGANIZE_ON_SCHEDULE` is enabled, the app includes a scheduler that runs at the configured interval (default: every hour, configurable via `AUTO_ORGANIZE_INTERVAL_HOURS`) to check for unorganized files.
3.  Both methods check `database.json` for any torrents downloaded via MouseSearch that are currently unorganized.
4.  For each unorganized torrent, MouseSearch talks with your torrent client to figure out where the torrent files currently are, then hardlinks (or copies) them to your `organized` directory.

> **Note:** currently MouseSearch only organizes torrents that have been downloaded using MouseSearch **after** this feature has been enabled. May in the future make this more flexible.

### Critical Setup Requirement

**If using the default Hardlink mode (`AUTO_ORGANIZE_USE_COPY=false`):**

Your source (`LOCAL_TORRENT_DOWNLOAD_PATH`) and destination directories (`ORGANIZED_PATH` and/or any additional destination paths) **must**:
1. exist on the same filesystem
2. **AND** within the same volume mount (if using Docker)

**If using Copy mode (`AUTO_ORGANIZE_USE_COPY=true`):**

You may use different filesystems or Docker volumes, but be aware that this will double the disk usage for every downloaded file.

#### Recommended File Structure (for Hardlink mode)

       downloads
       ├── organized <- where your organized files will appear (point Audiobookshelf here)
       └── torrents <- where your torrent client downloads files to

**Correct `.env` and Host Path Example:**

* **Host Path:** `/mnt/storage/downloads`
* **Volume Mount (Docker):** `- /mnt/storage/downloads:/downloads`
* **.env `LOCAL_TORRENT_DOWNLOAD_PATH`:** `/downloads/torrents/`
* **.env `ORGANIZED_PATH`:** `/downloads/organized/`

If your torrent client reports a different path than MouseSearch can access locally, also set `REMOTE_TORRENT_DOWNLOAD_PATH`. Example: `REMOTE_TORRENT_DOWNLOAD_PATH=/data/torrents` and `LOCAL_TORRENT_DOWNLOAD_PATH=/downloads/torrents`.

This setup guarantees that both paths point to the same underlying device, allowing hard links to be created.

---

## Feature Roadmap

Planned features and enhancements for future releases:

#### Enhanced Organization
- [ ] **LLM-Powered Auto-Organization**: Leverage large language models to intelligently organize media with improved accuracy for:
  - Better author/title extraction and normalization
  - Series detection and ordering
  - Handling edge cases and non-standard naming conventions
  - Smart metadata enrichment
- [ ] **Organize Existing Library**: MouseSearch currently only organizes new books added via MouseSearch. May expand this to existing books later.

#### Torrent Client Support
- [x] **qBittorrent** support
- [x] **Transmission** support
- [x] **Deluge** support
- [x] **rTorrent** support

**Have a feature request?** Open an issue on [GitHub](https://github.com/sevenlayercookie/MouseSearch/issues) to suggest new features
