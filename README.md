<div align="center" width="100%">
    <img src="static/favicon/transparent.png" width="128" alt="" />
</div>

# MouseSearch
MouseSearch is a self-hosted web application that provides a clean, fast search interface for MyAnonamouse (MAM). It connects directly to the MAM API for searching and supports modular torrent client integrations (qBittorrent, Deluge, Transmission, rTorrent) for one-click downloading, bridging the gap between your favorite tracker and your download client.



## Key Features

* **MAM Search:** Full-text search for torrents on MyAnonamouse.
* **Advanced Filtering:** Filter by title, author, narrator, media type, language, and advanced tracker filters (e.g., Freeleech, VIP, Active).
* **One-Click Downloading:** Send torrents directly to your torrent client (supports qBittorrent, Deluge, Transmission, and rTorrent), assigning a category from the UI.
* **Live Status Dashboards:**
    * View your MAM user stats (username, ratio, bonus points, etc.) directly in the app.
    * Check the connection status to both MAM and your torrent client.
* **Dynamic IP Updater:** Automatically checks your server's public IP and updates MAM's "Dynamic Seedbox IP" setting if a change is detected. This is ideal for home servers with dynamic IPs.
* **VIP Auto-Buy:** Automatically tops up your MAM VIP credit using bonus points on a configurable schedule. One-click manual top-up button also available.
* **Upload Credit Auto-Buy:** Intelligent upload credit management with multiple modes:
    * Auto-purchase when ratio falls below threshold (configurable, MAM minimum is 1.0)
    * Auto-purchase when upload buffer (uploaded - downloaded) is too low
    * Auto-purchase when bonus points exceed a threshold (continues until below threshold)
    * Pre-download buffer check - prevents downloads larger than available buffer and prompts for upload credit purchase
    * Manual purchase interface with preset amounts (50 GB, 100 GB), custom multiples of 50 GB up to 200 GB, or max affordable option (rounded down to the nearest 50 GB)
* **Freeleech Tools:** VIP Freeleech awareness in search results plus a personal Freeleech wedge button in the download confirmation dialog.
* **Enhanced Results UI:** Responsive cards, improved book details layout, and a high-res cover lightbox.
* **Live Torrent Polling:** After adding a torrent, the UI polls your torrent client to show its download status (e.g., "Downloading 50%", "Seeding") in real-time in results and the book details modal. Designates previously downloaded torrents as "Downloaded".
* **[BETA] Auto-Organization:** (See details below) Automatically hard-links completed audiobooks from your download folder to a clean, organized library structure (e.g., `Author/Title/file.m4b`).

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

3.  Create a `compose.yaml` file (see configuration below)

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

---

## Configuration

**Environment variables are completely optional.** You can configure all settings directly through the web interface after launching the application.

> **Settings saved in `config.json` (i.e. through the web interface) override environment values. To force env-only configuration, delete `config.json`**

### Environment Variables (`.env`)

Open the `.env` file and configure the following settings.

| Variable | Required | Description |
| :--- | :--- | :--- |
| `QUART_SECRET_KEY` | **Yes** | A long, random string for session security. You can generate one with `openssl rand -hex 32` (or just smash on the keyboard a bit) |
| `MAM_ID` | **Yes** | Your `mam_id` cookie value from [MyAnonamouse](https://www.myanonamouse.net/preferences/index.php?view=security). |

### Torrent Client Configuration

MouseSearch supports modular torrent clients. Currently supported: **qBittorrent**, **Deluge**, **Transmission**, and **rTorrent**.

| Variable | Required | Description |
| :--- | :--- | :--- |
| `TORRENT_CLIENT_TYPE` | No | The type of torrent client (default: `qbittorrent`). Options: `qbittorrent`, `deluge`, `transmission`, `rtorrent`. |
| `TORRENT_CLIENT_URL` | **Yes** | The full URL to your torrent client WebUI (e.g., `http://192.168.1.10:8080` or `http://qbittorrent:6767` if on the same Docker network). |
| `TORRENT_CLIENT_USERNAME` | **Yes** | Your torrent client username. |
| `TORRENT_CLIENT_PASSWORD` | **Yes** | Your torrent client password. |
| `TORRENT_CLIENT_CATEGORY` | No | (Optional) A default category to assign to downloads (e.g., `audiobooks`). |

### Additional Configuration

| Variable | Required | Description |
| :--- | :--- | :--- |
| `DATA_PATH` | No | Directory path for storing app data files (config.json, database.json, ip_state.json). Defaults to `./data`. |
| `ENABLE_DYNAMIC_IP_UPDATE` | No | Set to `true` to enable automatic IP checking and updating of MAM's "Dynamic Seedbox IP" setting. Defaults to `false`. |
| `DYNAMIC_IP_UPDATE_INTERVAL_HOURS` | No | Number of hours between automatic IP checks (only applies if `ENABLE_DYNAMIC_IP_UPDATE` is `true`). Defaults to `3`. |
| `AUTO_BUY_VIP` | No | Set to `true` to enable automatic VIP credit top-ups using bonus points. Defaults to `false`. |
| `AUTO_BUY_VIP_INTERVAL_HOURS` | No | Number of hours between automatic VIP purchases (only applies if `AUTO_BUY_VIP` is `true`). Defaults to `24`. |
| `AUTO_BUY_UPLOAD_ON_RATIO` | No | Set to `true` to enable automatic upload credit purchase when ratio falls below threshold. Defaults to `false`. |
| `AUTO_BUY_UPLOAD_RATIO_THRESHOLD` | No | If ratio falls below this value, automatically purchase upload credit. MAM requires minimum 1.0 ratio. Defaults to `1.5`. |
| `AUTO_BUY_UPLOAD_RATIO_AMOUNT` | No | Amount of upload credit (in GB) to purchase when ratio threshold is hit (multiples of 50 only). Defaults to `50`. |
| `AUTO_BUY_UPLOAD_ON_BUFFER` | No | Set to `true` to enable automatic upload credit purchase when buffer is too low. Defaults to `false`. |
| `AUTO_BUY_UPLOAD_BUFFER_THRESHOLD` | No | If upload buffer (uploaded - downloaded) falls below this many GB, automatically purchase upload credit. Defaults to `10`. |
| `AUTO_BUY_UPLOAD_BUFFER_AMOUNT` | No | Amount of upload credit (in GB) to purchase when buffer threshold is hit (multiples of 50 only). Defaults to `50`. |
| `AUTO_BUY_UPLOAD_ON_BONUS` | No | Set to `true` to enable automatic upload credit purchase when bonus points exceed a threshold. Defaults to `false`. |
| `AUTO_BUY_UPLOAD_BONUS_THRESHOLD` | No | If bonus points are at or above this value, auto-purchase upload credit until below threshold. Defaults to `5000`. |
| `AUTO_BUY_UPLOAD_BONUS_AMOUNT` | No | Amount of upload credit (in GB) to purchase per bonus-threshold check (multiples of 50 only). Defaults to `50`. |
| `AUTO_BUY_UPLOAD_CHECK_INTERVAL_HOURS` | No | Number of hours between ratio/buffer/bonus checks (only applies if auto-buy upload is enabled). Defaults to `6`. |
| `BLOCK_DOWNLOAD_ON_LOW_BUFFER` | No | Set to `true` to prevent downloads when torrent size exceeds available buffer (prompts user to purchase upload credit). Defaults to `true`. |
| `AUTO_ORGANIZE_ON_ADD` | No | Set to `true` to enable auto-organization when torrents are added. Defaults to `false`. |
| `AUTO_ORGANIZE_ON_SCHEDULE` | No | Set to `true` to enable scheduled auto-organization. Defaults to `false`. |
| `AUTO_ORGANIZE_INTERVAL_HOURS` | No | Number of hours between scheduled organization scans (only applies if `AUTO_ORGANIZE_ON_SCHEDULE` is `true`). Defaults to `1`. |
| `ORGANIZED_PATH` | If auto-organization is enabled | The *container* path for your organized library (e.g., `/downloads/organized/`). |
| `TORRENT_DOWNLOAD_PATH` | If auto-organization is enabled | The *container* path where your torrent client saves completed files for this category (e.g., `/downloads/torrents/`). |
| `ENABLE_FILESYSTEM_THUMBNAIL_CACHE` | No | Set to `true` to enable filesystem caching of thumbnail images (stores in `DATA_PATH/cache/thumbnails`). Defaults to `false`. **Enable this if you experience slow thumbnail loading or suspect you're hitting MAM rate limits.** Cached thumbnails expire after 30 days. |
| `THUMBNAIL_CACHE_MAX_SIZE_MB` | No | Maximum cache size in megabytes (only applies when `ENABLE_FILESYSTEM_THUMBNAIL_CACHE` is enabled). Oldest files are deleted first when limit is exceeded. Defaults to `500`. |
| `PUID` | No | (Docker only) User ID to run the container as. Set to your host user's UID for correct file permissions. |
| `PGID` | No | (Docker only) Group ID to run the container as. Set to your host user's GID for correct file permissions. |

**How to find your `MAM_ID`:**
1.  In any web browser, navigate to [Security](https://www.myanonamouse.net/preferences/index.php?view=security) on Myanonamouse
2.  Create a new session
    - IP address: run `curl ifconfig.me` from the server that will be hosting MouseSearch, and put output here
    - IP or ASN: `ASN` (ASN is more forgiving)
    - Dynamic Seedbox: choose `Yes` to allow MouseSearch to keep IP updated
    - Session Label: `MouseSearch`
3.  **IMPORTANT**: copy the `mam_id` value for configuring MouseSearch

### 4. Configure `compose.yaml`

Your `compose.yaml` file tells Docker how to run the app and, most importantly, where your files are. You **must** map your download and data directories.

Here is an example `compose.yaml`:

```yaml
services:
  mousesearch:
    image: sevenlayercookie/mousesearch:latest
    container_name: mousesearch
    restart: unless-stopped
    ports:
      - "5000:5000"
    volumes:
      - ./data:/data  # location that config and state files will be stored
      - /downloads:/downloads # where all downloads are stored (torrent client downloads and organized files) -- only needed if using auto-organize
      
      # see README.md for recommended structure and paths
      
    env_file: .env # optional: load environment variables from a file

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
5.  In the results, select a torrent category (if desired) and click "Download". A dialog will appear, allowing you to confirm or change the final `organized` destination.
6.  The torrent will be added, and a status badge will appear, polling your torrent client for live progress.

### Path Customization & Series Support:
When adding a torrent with `AUTO_ORGANIZE_ON_ADD` enabled, MouseSearch will present a confirmation window.

- **Review Path**: You can modify the calculated Author/Title path manually before sending it to the client.

- **Series Toggle**: If the book is part of a series, a "Series" button will appear. Clicking this automatically injects the series name into the path (e.g., Author/Series/Title).

## [BETA] Auto-Organization Feature

This feature is designed to automate your media library. When enabled, it hard-links completed audio files from your "messy" download directory into a "clean" library directory, organized in subdirectories by `Author/Title` or `Author/Series/Title`.

It **uses hard links**, not copies. This means it takes up **no additional disk space**, and **it will not interfere with torrent seeding** (does not modify or restructure the original torrent files)

### Configuration Options

You can control two separate aspects of auto-organization:

- **`AUTO_ORGANIZE_ON_ADD`**: Automatically organize files when torrents are added to your torrent client via the MouseSearch interface
- **`AUTO_ORGANIZE_ON_SCHEDULE`**: Periodically check for unorganized files at a configurable interval (mainly used as a backup to the ON_ADD functionality)
- **`AUTO_ORGANIZE_INTERVAL_HOURS`**: How often (in hours) to run the scheduled organization scan (defaults to 1 hour)

These can be enabled independently of each other:
- Enable only `AUTO_ORGANIZE_ON_ADD` for immediate organization when files are added
- Enable only `AUTO_ORGANIZE_ON_SCHEDULE` for batch processing on a schedule
- Enable both for maximum coverage (recommended)
- Adjust `AUTO_ORGANIZE_INTERVAL_HOURS` to control how frequently the scheduler runs (e.g., every 2 hours, every 6 hours, etc.)

### How It Works

1.  When `AUTO_ORGANIZE_ON_ADD` is enabled and you add a torrent, MouseSearch calculates its infohash and saves the Author/Title metadata from Myanonamouse to `./data/database.json`.
2.  When `AUTO_ORGANIZE_ON_SCHEDULE` is enabled, the app includes a scheduler that runs at the configured interval (default: every hour, configurable via `AUTO_ORGANIZE_INTERVAL_HOURS`) to check for unorganized files.
3.  Both methods check `database.json` for any torrents downloaded via MouseSearch that are currently unorganized.
4.  For each unorganized torrent, MouseSearch talks with your torrent client to figure out where the torrent files currently are, then hardlinks them to your `organized` directory.

> **Note:** currently MouseSearch only organizes torrents that have been downloaded using MouseSearch **after** this feature has been enabled. May in the future make this more flexible.

### Critical Setup Requirement

For hard links to work, your source (`TORRENT_DOWNLOAD_PATH`) and destination (`ORGANIZED_PATH`) directories **must**:
1. exist on the same filesystem

    **AND** 2. within the same volume mount (if using Docker)

The easiest way to ensure this is to have a single parent directory (e.g., `/mnt/storage/downloads`) on your host machine that contains *both* your torrents and your organized media. You then pass this single parent directory as a volume in your `compose.yaml`, as shown in the example.

#### Recommended File Structure (if using auto-organize)

       downloads
       ├── organized <- where your organized files will appear (point Audiobookshelf here)
       └── torrents <- where your torrent client downloads files to

**Correct `.env` and Host Path Example:**

* **Host Path:** `/mnt/storage/downloads`
* **Volume Mount (Docker):** `- /mnt/storage/downloads:/downloads`
* **.env `TORRENT_DOWNLOAD_PATH`:** `/downloads/torrents/`
* **.env `ORGANIZED_PATH`:** `/downloads/organized/`

**For Bare Metal installations:** Simply use absolute paths on your host system, again ensuring they are both on the same fileysystem/disk (e.g., `/mnt/storage/downloads/torrents/` and `/mnt/storage/downloads/organized/`).

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
