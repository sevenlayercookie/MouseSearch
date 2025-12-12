// main.js

// ============================================================
//  1. GLOBAL HELPERS & STATE
// ============================================================

// Icon definitions
const greenCheckIcon = `<img src="/static/icons/check_circle.svg" alt="connected" style="height: 16px; width: 16px;">`;
const redXIcon = `<img src="/static/icons/x_circle.svg" alt="not connected" style="height: 16px; width: 16px;">`;

// Global State
const torrentHashMap = {};
const hashToElementMap = new Map();
let lastClientStatus = null;
window.currentVipUntil = null;
window.currentBonusPoints = 0;
// Validation for upload purchase amounts
window.VALID_UPLOAD_AMOUNTS = [10, 50, 100, 500, 1000];

/**
 * Global helper to toggle switch when header is clicked.
 * Exposed to window for HTML onclick attributes.
 */
window.toggleCardSwitch = function (checkboxId) {
    const checkbox = document.getElementById(checkboxId);
    if (checkbox) checkbox.click();
};

/**
 * Handle broken images in the modal.
 * 1. Swaps broken img -> placeholder
 * 2. Swaps broken/empty background -> nice generic gradient
 */
function handleBookCoverError(imgElement) {
    // 1. Prevent infinite loop if placeholder is also missing
    imgElement.onerror = null;
    imgElement.src = '/static/icons/no_cover.png';

    // 2. Set a fallback background for the hero
    // (Blurring the "no_cover.png" usually looks bad, so we use a gradient instead)
    const heroBg = document.getElementById('detail-hero-bg');
    if (heroBg) {
        // A neutral, deep purple/blue gradient that looks premium
        heroBg.style.backgroundImage = 'linear-gradient(135deg, rgb(59 114 193) 0%, rgb(86 49 91) 100%)';
        // Remove the filter so it looks clean, not blurry mud
        heroBg.style.filter = 'none';
        heroBg.style.transform = 'none';
        heroBg.style.opacity = '1';
    }
}

// Explicitly attach to window to ensure global access
window.handleBookCoverError = handleBookCoverError;

// 1. Language Helper (Simplified)
// We initialize with 'en' so the resulting names are in English (e.g. outputs "German" instead of "Deutsch")
// const languageNames = new Intl.DisplayNames(['en'], { type: 'language' });
const languageNames = new Intl.DisplayNames(undefined, { type: 'language' });

function getLanguageName(code) {
    if (!code) return "Unknown";
    try {
        // Intl handles 3-letter codes (ISO 639-2) like 'ENG', 'SPA' natively (case-insensitive)
        return languageNames.of(code);
    } catch (e) {
        // Fallback to the code itself if Intl throws an error (e.g. invalid format)
        return code;
    }
}

// Helper to parse MAM specific JSON strings (e.g. "{\"91\":\"Douglas Adams\"}")
function parseMamJson(jsonStr) {
    if (!jsonStr) return null;
    try {
        const obj = typeof jsonStr === 'object' ? jsonStr : JSON.parse(jsonStr);
        // MAM returns objects with IDs as keys, we just want the values joined by comma
        // If it's an array (Series usually), handle that differently
        if (Array.isArray(obj)) return obj.join(', ');

        // Handle Series Object format: {"id": ["Name", "", -1]}
        const values = Object.values(obj);
        if (values.length > 0 && Array.isArray(values[0])) {
            return values.map(v => v[0]).join(', ');
        }

        // Handle Standard Object format: {"id": "Name"}
        return Object.values(obj).join(', ');
    } catch (e) {
        return jsonStr; // Return raw string if parse fails
    }
}

/**
 * Displays a toast message on the screen.
 */
function showToast(message, type = 'primary') {
    const toastElement = document.getElementById('server-response-toast');
    const toastMessage = document.getElementById('toast-message');
    if (!toastElement || !toastMessage) return;

    toastMessage.innerText = message;
    toastElement.className = `toast align-items-center text-bg-${type} border-0`;
    const toast = new bootstrap.Toast(toastElement);
    toast.show();
}

/**
 * Formats seconds into a human-readable string (e.g., 1h 5m)
 */
function formatDuration(seconds) {
    if (seconds >= 8640000) return '∞';
    if (seconds <= 0) return '0s';

    const units = [
        { label: 'd', value: 86400 },
        { label: 'h', value: 3600 },
        { label: 'm', value: 60 },
        { label: 's', value: 1 }
    ];

    let result = [];
    for (const unit of units) {
        if (seconds >= unit.value) {
            const count = Math.floor(seconds / unit.value);
            seconds %= unit.value;
            result.push(count + unit.label);
        }
    }
    return result.slice(0, 2).join(' ');
}

/**
 * Converts UTC strings to the user's local date (No Time).
 */
function localizeDates(scope = document) {
    scope.querySelectorAll('.render-local-date').forEach(el => {
        const rawDate = el.getAttribute('data-date');
        if (!rawDate || el.dataset.processed) return;

        try {
            // Standardize format: "2023-11-05 14:30:00" -> "2023-11-05T14:30:00Z"
            let cleanDate = rawDate.trim().replace(" ", "T");
            if (!cleanDate.endsWith('Z')) cleanDate += 'Z';

            const dateObj = new Date(cleanDate);
            if (!isNaN(dateObj)) {
                // CHANGED: used toLocaleDateString() instead of toLocaleString()
                // and removed hour/minute options.
                el.textContent = dateObj.toLocaleDateString();

                el.dataset.processed = "true";
            }
        } catch (e) {
            console.error("Date localization error:", e);
        }
    });
}

function sanitizeFilename(name) {
    if (!name) return "Unknown";
    return name.replace(/[<>:"/\\|?*]/g, '').trim();
}

function getSeriesName(seriesJsonStr) {
    try {
        if (!seriesJsonStr) return null;
        const data = JSON.parse(seriesJsonStr);
        const values = Object.values(data);
        if (values.length > 0 && Array.isArray(values[0])) {
            return values[0][0];
        }
    } catch (e) {
        console.error("Error parsing series info:", e);
    }
    return null;
}

// ============================================================
//  2. SERVER COMMUNICATION (SSE & FETCH)
// ============================================================

/**
 * Initializes Server-Sent Events (SSE)
 */
function initializeEventStream() {
    const eventSource = new EventSource('/events');

    eventSource.onmessage = function (event) {
        try {
            const data = JSON.parse(event.data);
            switch (data.event) {
                case 'toast':
                    showToast(data.message, data.type);
                    break;
                case 'torrent-progress':
                    const torrents = data.torrents || {};
                    for (const [hash, torrentData] of Object.entries(torrents)) {
                        const resultItem = hashToElementMap.get(hash);
                        if (resultItem) updateTorrentUI(hash, torrentData, resultItem);
                    }
                    break;
                case 'client-status':
                    if (lastClientStatus === data.status) break;
                    lastClientStatus = data.status;
                    const statusSpan = document.getElementById("client-status");
                    const statusIconSpan = document.getElementById("client-status-icon");
                    const clientTypeDisplay = document.getElementById('client-type-display');
                    const isConnected = data.status === "connected";

                    if (statusSpan) {
                        statusSpan.textContent = isConnected ? "CONNECTED" : "NOT CONNECTED";
                        statusSpan.className = isConnected ? "text-success" : "text-danger";
                    }
                    if (statusIconSpan) statusIconSpan.innerHTML = isConnected ? greenCheckIcon : redXIcon;

                    // FIX: Update display name regardless of connection status
                    if (data.display_name && clientTypeDisplay) {
                        clientTypeDisplay.textContent = data.display_name;
                    }
                    break;
                case 'mam-stats':
                    const userData = data.data || {};
                    const fields = {
                        'mam-username': 'username',
                        'mam-class': 'classname',
                        'mam-uploaded': 'uploaded',
                        'mam-downloaded': 'downloaded',
                        'mam-ratio': 'ratio',
                        'mam-bonus': 'seedbonus_formatted'
                    };
                    for (const [elementId, dataKey] of Object.entries(fields)) {
                        const element = document.getElementById(elementId);
                        if (element) element.textContent = userData[dataKey] || userData['seedbonus'] || 'N/A';
                    }
                    break;
                case 'vip_purchase':
                    if (data.success) {
                        showToast(`Auto VIP top-up: Added ${data.amount.toFixed(1)} weeks.`, 'success');
                        loadMamUserData();
                    }
                    break;
                case 'upload_purchase':
                    if (data.success) {
                        const reason = data.reason === 'ratio' ? 'low ratio' : data.reason === 'buffer' ? 'low buffer' : 'manual';
                        showToast(`Upload credit purchased (${reason}): Added ${data.amount} GB.`, 'success');
                        loadMamUserData();
                    }
                    break;
                default:
                    console.warn('[SSE] Unknown event type:', data.event);
            }
        } catch (error) {
            console.error('[SSE] Failed to parse event data:', error);
        }
    };
    eventSource.onerror = function (error) { console.error('[SSE] Error:', error); };
}

async function getTorrentHashByMID(torrentId) {
    if (torrentHashMap[torrentId]) return torrentHashMap[torrentId];
    try {
        const response = await fetch('/client/resolve_mid', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mid: torrentId })
        });
        if (!response.ok) return null;
        const data = await response.json();
        if (data.hash) {
            torrentHashMap[torrentId] = data.hash;
            return data.hash;
        }
    } catch (error) { console.error("Error resolving MID:", error); }
    return null;
}

function updateTorrentUI(hash, data, resultItem) {
    // 1. Find ALL containers (Desktop & Mobile)
    const statusContainers = resultItem.querySelectorAll('.torrent-status-container');
    if (!statusContainers.length) return;

    const state = data.state || 'unknown';
    const progressPercent = Math.floor((data.progress || 0) * 100);
    const etaSeconds = data.eta || 0;

    const errorStates = ['error', 'missingFiles'];
    const seedingStates = ['uploading', 'stalledUP', 'checkingUP', 'forcedUP', 'pausedUP', 'queuedUP'];
    const downloadingStates = ['downloading', 'metaDL', 'stalledDL', 'checkingDL', 'forcedDL', 'allocating', 'moving', 'checkingResumeData', 'queuedDL', 'pausedDL'];

    let htmlContent = '';
    if (downloadingStates.includes(state)) {
        const isPaused = state.includes('paused');
        const animatedClass = isPaused ? '' : 'progress-bar-striped progress-bar-animated';
        const bgClass = isPaused ? 'bg-secondary' : 'bg-primary';
        const etaText = isPaused ? 'Paused' : `ETA: ${formatDuration(etaSeconds)}`;
        const stateLabel = state === 'metaDL' ? 'Metadata' : (isPaused ? 'Paused' : 'Downloading');
        htmlContent = `
            <div class="d-flex justify-content-between small mb-1 text-muted">
                <span>${stateLabel}</span><span>${etaText}</span>
            </div>
            <div class="progress" role="progressbar" aria-label="Download progress" aria-valuenow="${progressPercent}" aria-valuemin="0" aria-valuemax="100" style="height: 20px;">
                <div class="progress-bar ${animatedClass} ${bgClass}" style="width: ${progressPercent}%">${progressPercent}%</div>
            </div>`;
    } else if (seedingStates.includes(state) || progressPercent >= 100) {
        htmlContent = `
             <div class="d-flex justify-content-between small mb-1 text-success">
                <span>Complete</span><span><i class="bi bi-check-all"></i></span>
            </div>
            <div class="progress" role="progressbar" style="height: 20px;">
                <div class="progress-bar bg-success" style="width: 100%">Seeding</div>
            </div>`;
    } else if (errorStates.includes(state)) {
        htmlContent = `<div class="alert alert-danger py-1 px-2 mb-0 small text-center"><i class="bi bi-exclamation-triangle-fill"></i> Error: ${state}</div>`;
    } else {
        htmlContent = `<div class="badge bg-secondary">State: ${state}</div>`;
    }

    // 2. Loop through and update BOTH containers
    statusContainers.forEach(container => {
        container.innerHTML = htmlContent;
    });
}

function pollTorrentStatus(hash, resultItem) {
    // 1. Find ALL containers
    const statusContainers = resultItem.querySelectorAll('.torrent-status-container');
    if (!statusContainers.length) return;

    hashToElementMap.set(hash, resultItem);

    // 2. Loop through and set the "Waiting" message on BOTH
    statusContainers.forEach(container => {
        container.innerHTML = `<span class="badge bg-info text-wrap">Waiting for updates...</span>`;
    });
}

function checkClientStatus() {
    const statusSpan = document.getElementById("client-status");
    const statusIconSpan = document.getElementById("client-status-icon");
    const clientTypeDisplay = document.getElementById('client-type-display');

    fetch('/client/status', { cache: "no-store" })
        .then(response => response.json())
        .then(data => {
            const isSuccess = data.status === "success";

            if (statusSpan) {
                statusSpan.textContent = isSuccess ? "CONNECTED" : "NOT CONNECTED";
                statusSpan.className = isSuccess ? "text-success" : "text-danger";
            }
            if (statusIconSpan) statusIconSpan.innerHTML = isSuccess ? greenCheckIcon : redXIcon;

            // FIX: Always update the name if the server sends it, even on error
            if (data.display_name && clientTypeDisplay) {
                clientTypeDisplay.textContent = data.display_name;
            }

            if (isSuccess) refreshCategories();
        })
        .catch(error => {
            if (statusSpan) { statusSpan.textContent = "NOT CONNECTED"; statusSpan.className = "text-danger"; }
            if (statusIconSpan) statusIconSpan.innerHTML = redXIcon;
        });
}

function refreshCategories() {
    fetch('/client/categories', { cache: "no-store" })
        .then(response => response.json())
        .then(data => {
            const resultDropdowns = document.querySelectorAll('.category-dropdown');
            const defaultCategory = document.getElementById('TORRENT_CLIENT_CATEGORY')?.value || '';

            resultDropdowns.forEach(dropdown => {
                dropdown.disabled = false; // <--- ADD THIS
                const currentVal = dropdown.value;
                dropdown.innerHTML = '<option value="">Category</option>';
                if (data && typeof data === 'object') {
                    for (const key in data) dropdown.add(new Option(data[key].name, data[key].name));
                }
                dropdown.value = currentVal || defaultCategory;
            });

            const settingsDropdown = document.getElementById('TORRENT_CLIENT_CATEGORY');
            if (settingsDropdown) {
                settingsDropdown.disabled = false; // <--- ADD THIS
                const currentValue = settingsDropdown.dataset.currentValue || '';
                settingsDropdown.innerHTML = '<option value="">None</option>';
                if (data && typeof data === 'object') {
                    for (const key in data) {
                        const option = new Option(data[key].name, data[key].name);
                        if (data[key].name === currentValue) option.selected = true;
                        settingsDropdown.add(option);
                    }
                }
                if (currentValue && ![...settingsDropdown.options].some(o => o.value === currentValue)) {
                    const option = new Option(currentValue, currentValue);
                    option.selected = true;
                    settingsDropdown.add(option);
                }
            }
        });
}

function loadMamUserData() {
    fetch('/mam/user_data', { cache: "no-store" })
        .then(response => { if (!response.ok) throw new Error(); return response.json(); })
        .then(data => {
            const statusSpan = document.getElementById('mam-status');
            const statusIconSpan = document.getElementById('mam-status-icon');
            if (statusSpan) { statusSpan.textContent = 'CONNECTED'; statusSpan.className = 'text-success'; }
            if (statusIconSpan) statusIconSpan.innerHTML = greenCheckIcon;

            document.getElementById('mam-username').textContent = data.username || 'N/A';
            document.getElementById('mam-class').textContent = data.classname || 'N/A';
            document.getElementById('mam-uploaded').textContent = data.uploaded || 'N/A';
            document.getElementById('mam-downloaded').textContent = data.downloaded || 'N/A';
            document.getElementById('mam-ratio').textContent = data.ratio || 'N/A';
            document.getElementById('mam-bonus').textContent = data.seedbonus_formatted || 'N/A';

            window.currentVipUntil = data.vip_until;
            window.currentBonusPoints = parseFloat(data.seedbonus || 0);

            const vipWeeksContainer = document.getElementById('vip-weeks-container');
            const vipWeeksSpan = document.getElementById('vip-weeks-remaining');
            if (data.vip_until && vipWeeksContainer && vipWeeksSpan) {
                const now = new Date();
                const vipDate = new Date(data.vip_until.replace(' ', 'T'));
                const diffMs = vipDate - now;
                const diffWeeks = diffMs / (1000 * 60 * 60 * 24 * 7);
                vipWeeksSpan.textContent = diffWeeks > 0 ? `${diffWeeks.toFixed(1)} weeks` : 'Expired';
                vipWeeksContainer.style.display = 'block';
            }
        })
        .catch(error => {
            const statusSpan = document.getElementById('mam-status');
            const statusIconSpan = document.getElementById('mam-status-icon');

            if (statusSpan) { statusSpan.textContent = 'NOT CONNECTED'; statusSpan.className = 'text-danger'; }
            if (statusIconSpan) statusIconSpan.innerHTML = redXIcon;
        });
}

function initializeSnatchedTorrents() {
    document.querySelectorAll('.result-item[data-snatched="1"]').forEach(async (item) => {
        const torrentId = item.dataset.torrentId;
        if (torrentId) {
            const hash = await getTorrentHashByMID(torrentId);
            if (hash) {
                pollTorrentStatus(hash, item);
                fetchAndUpdateTorrentStatus(hash, item);
            }
        }
    });
}

async function fetchAndUpdateTorrentStatus(hash, resultItem) {
    try {
        const response = await fetch(`/client/info/${hash}`, { cache: "no-store" });
        if (response.ok) {
            const data = await response.json();
            updateTorrentUI(hash, data, resultItem);
        }
    } catch (error) { console.error(`Error fetching hash ${hash}:`, error); }
}

async function fetchPublicIP() {
    fetch('/system/public_ip')
        .then(r => r.json())
        .then(data => {
            if (data.ip) {
                document.querySelectorAll('.backend-ip-display').forEach(el => el.textContent = data.ip);
                document.querySelectorAll('.backend-ip-display-badge').forEach(el => el.style.display = 'inline-block');
                document.querySelectorAll('.copy-ip-btn').forEach(btn => {
                    if (navigator.clipboard) {
                        btn.onclick = (e) => {
                            navigator.clipboard.writeText(data.ip);
                            const originalIcon = btn.innerHTML;
                            btn.innerHTML = '<i class="bi bi-check2 text-success"></i>';
                            setTimeout(() => btn.innerHTML = originalIcon, 2000);
                        };
                    } else {
                        btn.style.display = 'none';
                    }
                });
            } else {
                document.querySelectorAll('.backend-ip-display').forEach(el => el.textContent = "Error");
            }
        })
        .catch(err => {
            console.error("Failed to fetch IP", err);
            document.querySelectorAll('.backend-ip-display').forEach(el => el.textContent = "Unavailable");
        });
}

// ============================================================
//  3. MAIN DOM EVENT LISTENERS
// ============================================================

document.addEventListener("DOMContentLoaded", function () {
    initializeEventStream();

    // Init Tooltips
    [...document.querySelectorAll('[data-bs-toggle="tooltip"]')].map(el => new bootstrap.Tooltip(el));

    localizeDates();

    // Initial Fetches
    fetchPublicIP();
    checkClientStatus();
    loadMamUserData();

    // --- A. Settings & Toggle Logic ---
    const toggleInputs = document.querySelectorAll('.form-check-input[data-collapse-target]');

    toggleInputs.forEach(input => {
        const targetId = input.dataset.collapseTarget;
        const targetEl = document.querySelector(targetId);

        if (!targetEl) return;

        // Prevent double click during animation
        input.addEventListener('click', function (e) {
            e.stopPropagation();
            if (targetEl.classList.contains('collapsing')) {
                e.preventDefault();
                return false;
            }
        });

        // Sync Accordion
        input.addEventListener('change', function () {
            const bsCollapse = bootstrap.Collapse.getOrCreateInstance(targetEl, { toggle: false });
            this.checked ? bsCollapse.show() : bsCollapse.hide();
            updateDependentFields();
        });

        // Safety net (Auto-correct state)
        targetEl.addEventListener('shown.bs.collapse', () => {
            if (!input.checked) { input.checked = true; updateDependentFields(); }
        });
        targetEl.addEventListener('hidden.bs.collapse', () => {
            if (input.checked) { input.checked = false; updateDependentFields(); }
        });
    });

    // Dependent Fields Logic
    function updateDependentFields() {
        const isChecked = (id) => document.getElementById(id)?.checked || false;

        const config = [
            { trigger: 'ENABLE_DYNAMIC_IP_UPDATE', target: 'DYNAMIC_IP_UPDATE_INTERVAL_HOURS' },
            { trigger: 'AUTO_BUY_VIP', target: 'AUTO_BUY_VIP_INTERVAL_HOURS' },
            { trigger: 'AUTO_BUY_UPLOAD_ON_RATIO', target: ['AUTO_BUY_UPLOAD_RATIO_THRESHOLD', 'AUTO_BUY_UPLOAD_RATIO_AMOUNT'] },
            { trigger: 'AUTO_BUY_UPLOAD_ON_BUFFER', target: ['AUTO_BUY_UPLOAD_BUFFER_THRESHOLD', 'AUTO_BUY_UPLOAD_BUFFER_AMOUNT'] }
        ];

        config.forEach(item => {
            const enabled = isChecked(item.trigger);
            const targets = Array.isArray(item.target) ? item.target : [item.target];
            targets.forEach(tId => {
                const el = document.getElementById(tId);
                if (el) el.disabled = !enabled;
            });
        });

        // Upload Check Interval Logic
        const ratioOn = isChecked('AUTO_BUY_UPLOAD_ON_RATIO');
        const bufferOn = isChecked('AUTO_BUY_UPLOAD_ON_BUFFER');
        const uploadContainer = document.getElementById('upload-check-interval-container');
        const uploadInput = document.getElementById('AUTO_BUY_UPLOAD_CHECK_INTERVAL_HOURS');

        if (uploadContainer) {
            uploadContainer.classList.toggle('d-none', !ratioOn && !bufferOn);
        }
        if (uploadInput) uploadInput.disabled = (!ratioOn && !bufferOn);

        // Auto Organize Path Logic
        const organizeOnAdd = isChecked('AUTO_ORGANIZE_ON_ADD');
        const organizeOnSchedule = isChecked('AUTO_ORGANIZE_ON_SCHEDULE');
        const pathContainer = document.getElementById('path-configuration-container');
        if (pathContainer) {
            pathContainer.classList.toggle('d-none', !organizeOnAdd && !organizeOnSchedule);
        }
    }


    ['AUTO_ORGANIZE_ON_ADD', 'AUTO_ORGANIZE_ON_SCHEDULE'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', updateDependentFields);
    });

    updateDependentFields();

    // --- Client Type Change Listener ---
    const clientTypeSelect = document.getElementById('TORRENT_CLIENT_TYPE');
    const settingsCatSelect = document.getElementById('TORRENT_CLIENT_CATEGORY');

    if (clientTypeSelect) {
        clientTypeSelect.addEventListener('change', function () {
            const tempMsg = '<option value="">Save settings to load...</option>';

            // 1. Disable and reset Settings dropdown
            if (settingsCatSelect) {
                settingsCatSelect.innerHTML = tempMsg;
                settingsCatSelect.disabled = true;
            }

            // 2. Disable and reset all Result card dropdowns 
            document.querySelectorAll('.category-dropdown').forEach(dd => {
                dd.innerHTML = tempMsg;
                dd.disabled = true;
            });
        });
    }

    // Upload Amount Validation
    function findNearestValidAmount(value) {
        if (!window.VALID_UPLOAD_AMOUNTS || window.VALID_UPLOAD_AMOUNTS.length === 0) return value;
        const numValue = parseFloat(value);
        if (isNaN(numValue) || numValue < 1) return window.VALID_UPLOAD_AMOUNTS[0];
        if (window.VALID_UPLOAD_AMOUNTS.includes(numValue)) return numValue;
        let nearest = window.VALID_UPLOAD_AMOUNTS[0];
        let minDiff = Math.abs(numValue - nearest);
        for (const validAmount of window.VALID_UPLOAD_AMOUNTS) {
            const diff = Math.abs(numValue - validAmount);
            if (diff < minDiff) { minDiff = diff; nearest = validAmount; }
        }
        return nearest;
    }
    document.querySelectorAll('.upload-amount-input').forEach(input => {
        input.addEventListener('blur', function () {
            const valid = findNearestValidAmount(this.value);
            if (parseFloat(this.value) !== valid) this.value = valid;
        });
    });

    // --- B. Button Handlers (Save, VIP, Upload) ---

    // Save Settings
    document.getElementById('save-settings-button')?.addEventListener('click', function () {
        fetch('/update_settings', { method: 'POST', body: new FormData(document.getElementById('settings-form')) })
            .then(response => response.json())
            .then(data => {
                showToast(data.message, data.status === 'success' ? 'success' : 'danger');
                if (data.status === 'success') {
                    const catDropdown = document.getElementById('TORRENT_CLIENT_CATEGORY');
                    if (catDropdown) catDropdown.dataset.currentValue = catDropdown.value;

                    const clientLink = document.getElementById('clientLink');
                    const clientUrl = document.getElementById('TORRENT_CLIENT_URL').value;
                    if (clientLink) { clientLink.href = clientUrl; clientLink.textContent = clientUrl; }
                    checkClientStatus();
                    loadMamUserData();
                }
            })
            .catch(() => showToast("Error saving settings.", 'danger'));
    });

    // Buy VIP Logic
    const buyVipButton = document.getElementById('buy-vip-button');
    const vipModalEl = document.getElementById('vipPurchaseModal');
    const vipModal = vipModalEl ? new bootstrap.Modal(vipModalEl) : null;
    const VIP_COST_PER_WEEK = 1250;
    const MAX_VIP_WEEKS = 12.85;

    if (buyVipButton && vipModal) {
        buyVipButton.addEventListener('click', function () {
            let currentWeeks = 0;
            if (window.currentVipUntil) {
                const now = new Date();
                const vipDate = new Date(window.currentVipUntil.replace(' ', 'T'));
                if (vipDate > now) currentWeeks = (vipDate - now) / (1000 * 60 * 60 * 24 * 7);
            }

            document.getElementById('vip-modal-current-bp').textContent = window.currentBonusPoints.toLocaleString();
            document.getElementById('vip-modal-current-weeks').textContent = currentWeeks > 0 ? `${currentWeeks.toFixed(1)} weeks` : "0 weeks";

            const weeksToCap = Math.max(0, MAX_VIP_WEEKS - currentWeeks);
            const weeksAffordable = window.currentBonusPoints / VIP_COST_PER_WEEK;
            let purchaseWeeks = Math.min(weeksToCap, weeksAffordable);
            purchaseWeeks = Math.floor(purchaseWeeks * 10) / 10;

            const maxBtn = document.getElementById('vip-buy-max-btn');
            const maxTitle = document.getElementById('vip-max-title');
            const maxSubtitle = document.getElementById('vip-max-subtitle');
            const maxCostBadge = document.getElementById('vip-max-cost');

            maxBtn.disabled = false;
            maxBtn.classList.remove('btn-secondary');

            if (purchaseWeeks < 0.1) {
                maxTitle.textContent = "Top Up Max";
                maxSubtitle.textContent = "Already at limit";
                maxCostBadge.textContent = "0 BP";
            } else {
                const purchaseCost = Math.ceil(purchaseWeeks * VIP_COST_PER_WEEK);
                maxTitle.textContent = `Top Up +${purchaseWeeks.toFixed(1)} Weeks`;
                maxSubtitle.textContent = weeksAffordable < weeksToCap ? "Limited by points" : "Reach 12.8 week limit";
                maxCostBadge.textContent = `${purchaseCost.toLocaleString()} BP`;
                maxBtn.classList.add('btn-success');
            }

            document.querySelectorAll('.vip-buy-btn[data-duration="4"], .vip-buy-btn[data-duration="8"]').forEach(btn => {
                const weeks = parseInt(btn.dataset.duration);
                const cost = weeks * VIP_COST_PER_WEEK;
                const canAfford = window.currentBonusPoints >= cost;
                const wouldExceed = (currentWeeks + weeks) > MAX_VIP_WEEKS;
                const badge = btn.querySelector('.badge');

                if (!canAfford) {
                    btn.disabled = true; badge.className = 'badge bg-danger'; badge.textContent = 'Not enough BP';
                } else if (wouldExceed) {
                    btn.disabled = true; badge.className = 'badge bg-warning text-dark'; badge.textContent = 'Exceeds Limit';
                } else {
                    btn.disabled = false; badge.className = 'badge bg-secondary'; badge.textContent = `${cost.toLocaleString()} BP`;
                }
            });
            vipModal.show();
        });

        document.querySelectorAll('.vip-buy-btn').forEach(btn => {
            btn.addEventListener('click', function () {
                if (this.disabled) return;
                const duration = this.dataset.duration;
                const originalHtml = this.innerHTML;
                this.disabled = true;
                this.innerHTML = `<div class="d-flex align-items-center"><span class="spinner-border spinner-border-sm me-2"></span> Processing...</div>`;
                document.querySelectorAll('.vip-buy-btn').forEach(b => b.classList.add('disabled'));

                fetch('/mam/buy_vip', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ duration: duration })
                })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            const added = data.amount || (duration === 'max' ? 'Max' : duration);
                            if (parseFloat(data.amount) === 0 && duration === 'max') {
                                showToast(`Already at maximum VIP limit.`, 'success');
                            } else {
                                showToast(`Success! Added ${added} weeks. Remaining: ${data.seedbonus} BP`, 'success');
                            }
                            loadMamUserData();
                            vipModal.hide();
                        } else {
                            showToast(data.error || 'Purchase failed', 'danger');
                        }
                    })
                    .catch(() => showToast('Connection error', 'danger'))
                    .finally(() => {
                        this.disabled = false;
                        this.innerHTML = originalHtml;
                        document.querySelectorAll('.vip-buy-btn').forEach(b => b.classList.remove('disabled'));
                    });
            });
        });
    }

    // Buy Upload Handlers
    const uploadAmountOptions = document.getElementById('upload-amount-options');
    if (uploadAmountOptions) {
        uploadAmountOptions.addEventListener('click', function (e) {
            const button = e.target.closest('button');
            if (!button) return;
            const amount = button.dataset.amount;
            const buttons = uploadAmountOptions.querySelectorAll('button');
            buttons.forEach(btn => btn.disabled = true);
            const originalHtml = button.innerHTML;
            button.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Buying...';

            fetch('/mam/buy_upload', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ amount: amount === 'max' ? 'max' : parseFloat(amount) })
            })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        showToast(`Purchased ${data.amount} GB.`, 'success');
                        loadMamUserData();
                        bootstrap.Modal.getInstance(document.getElementById('uploadPurchaseModal'))?.hide();
                    } else { showToast(data.error || 'Failed', 'danger'); }
                })
                .catch(() => showToast('Error purchasing upload', 'danger'))
                .finally(() => {
                    buttons.forEach(btn => btn.disabled = false);
                    button.innerHTML = originalHtml;
                });
        });
    }

    // ============================================================
    //  C. SEARCH & DOWNLOAD LOGIC
    // ============================================================

    const searchForm = document.getElementById("search-form");
    const resultsContainer = document.getElementById("results-container");
    const searchButton = document.getElementById("searchButton");
    const wrapper = document.getElementById('results-container-wrapper');
    const resultsTitle = document.getElementById('results-title');

    // Download Confirmation & Modal Variables
    let pendingDownloadData = null;
    let pendingButton = null;
    const confirmModalEl = document.getElementById('downloadConfirmModal');
    const confirmModal = confirmModalEl ? new bootstrap.Modal(confirmModalEl) : null;
    const confirmInput = document.getElementById('confirm-path-input');
    const previewSpan = document.getElementById('full-path-preview');

    if (confirmInput && previewSpan) confirmInput.addEventListener('input', function () { previewSpan.textContent = this.value; });

    function performSearch(queryString, isHistoryNavigation = false) {
        if (!queryString) return Promise.resolve(); // Return resolved promise if no query

        searchButton.disabled = true;
        searchButton.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Searching...`;
        if (resultsTitle) resultsTitle.textContent = 'Results';
        hashToElementMap.clear();

        // ADD 'return' HERE
        return fetch(`/mam/search?${queryString}`)
            .then(response => response.text())
            .then(html => {
                wrapper.style.display = 'block';
                resultsContainer.innerHTML = html;
                localizeDates(resultsContainer);
                const count = resultsContainer.querySelectorAll('.result-item').length;
                if (resultsTitle) resultsTitle.textContent = `Results (${count})`;
                if (!isHistoryNavigation) {
                    wrapper.scrollIntoView({ behavior: 'smooth', block: 'start' });
                }
                refreshCategories();
                initializeSnatchedTorrents();
            })
            .catch(error => {
                wrapper.style.display = 'block';
                resultsContainer.innerHTML = `<div class="alert alert-danger">Search failed.</div>`;
            })
            .finally(() => {
                searchButton.disabled = false;
                searchButton.innerHTML = "Search";
            });
    }

    function restoreFormFromURL(params) {
        document.getElementById('query').value = params.get('query') || '';
        ['search_in_title', 'search_in_author', 'search_in_narrator', 'search_in_series'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.checked = params.has(id);
        });
        if (params.get('media_type')) document.getElementById('media_type').value = params.get('media_type');
        if (params.get('language')) document.getElementById('language').value = params.get('language');
    }

    if (searchForm) {
        searchForm.addEventListener("submit", function (e) {
            e.preventDefault();
            document.getElementById('query').blur();
            const formData = new FormData(searchForm);
            const queryParams = new URLSearchParams(formData);
            const queryString = queryParams.toString();
            const newUrl = `${window.location.pathname}?${queryString}`;

            history.pushState({ type: 'search', query: queryString }, '', newUrl);
            performSearch(queryString);
        });
    }

    // ============================================================
    //  UNIFIED HISTORY & NAVIGATION MANAGER
    // ============================================================

    // 1. Central History Listener
    window.addEventListener('popstate', (event) => {
        // UI Elements
        const bookModalEl = document.getElementById('bookDetailsModal');
        const bookModal = bootstrap.Modal.getOrCreateInstance(bookModalEl);

        const settingsEl = document.getElementById('settingsOffcanvas');
        const settingsOffcanvas = bootstrap.Offcanvas.getOrCreateInstance(settingsEl);

        // Close everything first (clean slate)
        bookModal.hide();
        settingsOffcanvas.hide();

        if (event.state) {
            // --- STATE: BOOK DETAILS ---
            if (event.state.type === 'book_details') {
                renderBookDetails(event.state.bookData, event.state.coverSrc);
                bookModal.show();
            }
            // --- STATE: SETTINGS ---
            else if (event.state.type === 'settings') {
                settingsOffcanvas.show();
            }
            // --- STATE: SEARCH RESULTS ---
            else if (event.state.type === 'search') {
                restoreFormFromURL(new URLSearchParams(event.state.query));
                performSearch(event.state.query, true);
            }
        } else {
            // --- STATE: LANDING PAGE (No state) ---
            // If there are query params in URL (e.g. refresh), load search
            const urlParams = new URLSearchParams(window.location.search);
            if (urlParams.has('query')) {
                performSearch(urlParams.toString(), true);
            }
        }
    });

    // 2. Book Modal: Sync History on Manual Close
    document.getElementById('bookDetailsModal')?.addEventListener('hide.bs.modal', function () {
        // Only go back if we are currently IN the book_details state.
        // This prevents a double-back loop if the user pressed the Browser Back button.
        if (history.state && history.state.type === 'book_details') {
            history.back();
        }
    });

    // 3. Settings Offcanvas: Sync History on Manual Close/Open
    const settingsEl = document.getElementById('settingsOffcanvas');
    if (settingsEl) {
        // When manually OPENED (clicked the gear icon)
        settingsEl.addEventListener('show.bs.offcanvas', function (e) {
            // Prevent pushing state if we are just restoring it from history (popstate)
            if (!e.relatedTarget) return; // bootstrap sets relatedTarget to null if triggered via JS (.show())

            // Push state
            history.pushState({ type: 'settings' }, '', '#settings');
        });

        // When manually CLOSED (clicked X or backdrop)
        settingsEl.addEventListener('hide.bs.offcanvas', function () {
            if (history.state && history.state.type === 'settings') {
                history.back();
            }
        });
    }

    // Deep Linking (Load search on refresh)
    const initialParams = new URLSearchParams(window.location.search);

    // Check if we have a book hash (#book=12345)
    const hash = window.location.hash;
    const deepLinkID = hash.startsWith('#book=') ? hash.split('=')[1] : null;

    if (initialParams.has('query')) {
        // SCENARIO 1: We have a search query (Standard Refresh)
        restoreFormFromURL(initialParams);
        performSearch(initialParams.toString()).then(() => {
            if (deepLinkID) openDeepLink(deepLinkID);
        });
    }
    else if (deepLinkID) {
        // SCENARIO 2: We have NO search query, but we have a Book ID (Direct Link)
        // We artificially create a search for this specific ID to get the data
        const fakeQuery = new URLSearchParams();
        fakeQuery.set('query', deepLinkID); // Searching the ID usually works on trackers

        // Update the search bar visually so the user knows what happened
        document.getElementById('query').value = deepLinkID;

        performSearch(fakeQuery.toString()).then(() => {
            openDeepLink(deepLinkID);
        });
    }

    // Helper to find the row and open the modal
    function openDeepLink(id) {
        const targetRow = document.querySelector(`.result-item[data-torrent-id="${id}"]`);
        if (targetRow) {
            const rawJson = targetRow.dataset.json;
            if (rawJson) {
                try {
                    const data = JSON.parse(rawJson);
                    openBookDetailsModal(data, targetRow);
                } catch (e) { console.error("Deep link parse error", e); }
            }
        }
    }

    // Settings Offcanvas History Support
    const settingsOffcanvas = document.getElementById('settingsOffcanvas');
    if (settingsOffcanvas) {
        settingsOffcanvas.addEventListener('shown.bs.offcanvas', () => history.pushState({ type: 'settings' }, '', '#settings'));
        settingsOffcanvas.addEventListener('hidden.bs.offcanvas', () => {
            if (window.location.hash === '#settings') history.back();
        });
    }

    // Result Click Handling (Download/Series)
    if (resultsContainer) {
        resultsContainer.addEventListener('click', function (event) {

            // CASE A: Clicked the "Download" button
            const button = event.target.closest('.add-to-client-button');
            if (button) {
                event.preventDefault();
                event.stopPropagation(); // Prevent opening the details modal

                const resultItem = button.closest('.result-item');
                initiateDownloadFlow(button, resultItem);
                return;
            }

            // CASE B: Clicked a Dropdown or Link (e.g., Author link)
            // We want default browser behavior, NOT opening the details modal
            if (event.target.closest('select') || event.target.closest('a')) {
                return;
            }

            // CASE C: Clicked the Row (Result Item) -> Open Details Modal
            const resultItem = event.target.closest('.result-item');
            if (resultItem) {
                // Retrieve the full JSON we injected into the HTML
                const rawJson = resultItem.dataset.json;
                if (rawJson) {
                    try {
                        const data = JSON.parse(rawJson);
                        // Open the modal (make sure openBookDetailsModal is defined in main.js)
                        openBookDetailsModal(data, resultItem);
                    } catch (e) {
                        console.error("Error parsing book data", e);
                    }
                }
            }
        });
    }

    /**
 * REFACTORED: Handles the download logic. 
 * Can be called from the main list OR the details modal.
 * @param {HTMLElement} button - The button clicked (contains data attributes)
 * @param {HTMLElement} resultItem - The row element (contains the category dropdown)
 */
    function initiateDownloadFlow(button, resultItem) {
        const rawSeries = button.dataset.seriesInfo;
        const seriesName = getSeriesName(rawSeries);

        // 1. Construct the download payload from the button's data attributes
        const downloadData = {
            torrent_url: button.dataset.torrentUrl,
            // Try to find the dropdown in the resultItem; default to empty if not found
            category: resultItem ? (resultItem.querySelector('.category-dropdown')?.value || '') : '',
            id: button.dataset.id,
            author: button.dataset.author || "Unknown",
            title: button.dataset.title || "Unknown",
            size: button.dataset.size || '0 GiB',
            main_cat: button.dataset.mainCat || '',
            series_info: rawSeries
        };

        // 2. Check if Auto-Organize is enabled
        const autoOrganizeEnabled = document.getElementById('AUTO_ORGANIZE_ON_ADD')?.checked;

        if (autoOrganizeEnabled && confirmModal) {
            // --- Auto-Organize Logic (Populate Confirm Modal) ---

            const cleanAuthor = sanitizeFilename(downloadData.author);
            const cleanTitle = sanitizeFilename(downloadData.title);

            // Set default path: Author / Title
            confirmInput.value = `${cleanAuthor}/${cleanTitle}`;
            previewSpan.textContent = confirmInput.value;
            document.getElementById('path-format-hint').textContent = "Format: Author / Title";

            // Logic for the "+ Series" button inside the modal
            const addSeriesBtn = document.getElementById('add-series-btn');
            const seriesPreviewEl = document.getElementById('series-name-preview');

            if (addSeriesBtn) {
                // Reset button state
                addSeriesBtn.dataset.cleanAuthor = cleanAuthor;
                addSeriesBtn.dataset.cleanTitle = cleanTitle;
                addSeriesBtn.dataset.active = "false";
                addSeriesBtn.classList.replace('btn-secondary', 'btn-outline-secondary');
                addSeriesBtn.classList.remove('text-white');
                addSeriesBtn.innerHTML = '<i class="bi bi-plus-lg"></i> Series';

                if (seriesName) {
                    const cleanSeries = sanitizeFilename(seriesName);
                    addSeriesBtn.dataset.cleanSeries = cleanSeries;
                    addSeriesBtn.disabled = false;
                    if (seriesPreviewEl) {
                        seriesPreviewEl.textContent = `"${cleanSeries}"`;
                        seriesPreviewEl.style.display = 'inline';
                    }
                } else {
                    addSeriesBtn.dataset.cleanSeries = "";
                    addSeriesBtn.disabled = true;
                    if (seriesPreviewEl) seriesPreviewEl.style.display = 'none';
                }
            }

            // Save data to global vars for the "Confirm" button to use later
            pendingDownloadData = downloadData;
            pendingButton = button;

            confirmModal.show();
        } else {
            // --- Direct Download (No Confirm Modal) ---
            performDownload(downloadData, button);
        }
    }

    // ============================================================
    //  MODAL RENDERING LOGIC
    // ============================================================

    /**
     * 1. OPEN FUNCTION
     * Called when you CLICK a row.
     * Pushes state to history -> Renders content -> Shows Modal.
     */
    function openBookDetailsModal(data, originElement) {
        // Get Cover Source (fallback to placeholder if missing)
        const coverSrc = originElement.querySelector('img')?.src || '/static/icons/no_cover.png';

        // Push History State so the "Back" button works
        const newUrl = window.location.pathname + window.location.search + `#book=${data.id}`;
        history.pushState({
            type: 'book_details',
            bookData: data,
            coverSrc: coverSrc
        }, '', newUrl);

        // Render & Show
        renderBookDetails(data, coverSrc);
        const modal = bootstrap.Modal.getOrCreateInstance(document.getElementById('bookDetailsModal'));
        modal.show();
    }

    /**
     * 2. RENDER FUNCTION
     * Called by openBookDetailsModal AND by the History Manager (popstate).
     * Updates the DOM elements inside the modal.
     */
    function renderBookDetails(data, coverSrc) {
        // Parse Complex Fields
        const authors = parseMamJson(data.author_info);
        const narrators = parseMamJson(data.narrator_info) || "N/A";
        const series = parseMamJson(data.series_info);

        // Populate Text
        document.getElementById('detail-title').innerHTML = data.title;
        document.getElementById('detail-subtitle').innerHTML = series ? `<span class="badge bg-secondary opacity-75">Series</span> ${series}` : '';
        document.getElementById('detail-authors').textContent = authors;
        document.getElementById('detail-narrators').textContent = narrators;
        document.getElementById('detail-description').innerHTML = data.description || "No description available.";

        // Populate Image
        const imgEl = document.getElementById('detail-cover');
        // Reset the error handler (in case it was nulled out previously)
        imgEl.onerror = function () { handleBookCoverError(this); };
        imgEl.src = coverSrc;

        // Dynamic Hero Background (Blurred Image)
        const heroBg = document.getElementById('detail-hero-bg');
        heroBg.style.backgroundImage = `url('${coverSrc}')`;

        // RESET styles in case previous book triggered the error handler
        heroBg.style.filter = 'blur(50px)';
        heroBg.style.transform = 'scale(1.2)';
        heroBg.style.opacity = '0.5';

        // Populate Metadata Sidebar
        document.getElementById('detail-category').innerHTML = data.catname;
        document.getElementById('detail-language').textContent = getLanguageName(data.lang_code);
        document.getElementById('detail-filetype').textContent = data.filetype;
        document.getElementById('detail-size').textContent = data.size.replace('iB', 'B');
        document.getElementById('detail-added').textContent = new Date(data.added).toLocaleDateString();
        document.getElementById('detail-seeders').textContent = data.seeders;
        document.getElementById('detail-leechers').textContent = data.leechers;

        // Populate Tags
        const tagsContainer = document.getElementById('detail-tags');
        tagsContainer.innerHTML = '';
        if (data.tags) {
            data.tags.split(',').forEach(tag => {
                if (!tag.trim()) return;
                const badge = document.createElement('span');
                badge.className = 'badge bg-body-secondary text-body-emphasis border border-secondary-subtle fw-normal text-wrap text-start lh-base';
                badge.textContent = tag.trim();
                tagsContainer.appendChild(badge);
            });
        }

        // Setup Download Button
        const dlBtn = document.getElementById('detail-download-btn');

        // Copy data attributes to the button so initiateDownloadFlow can read them
        dlBtn.dataset.torrentUrl = data.download_link;
        dlBtn.dataset.id = data.id;
        dlBtn.dataset.author = authors;
        dlBtn.dataset.title = data.title;
        dlBtn.dataset.size = data.size;
        dlBtn.dataset.mainCat = data.main_cat;
        dlBtn.dataset.seriesInfo = data.series_info;

        // Clone button to remove old event listeners (prevents multiple clicks firing)
        const newDlBtn = dlBtn.cloneNode(true);
        dlBtn.parentNode.replaceChild(newDlBtn, dlBtn);

        // Add Click Listener
        newDlBtn.addEventListener('click', function () {
            // Pass null for resultItem because the modal doesn't have the category dropdown
            // The initiateDownloadFlow function handles null gracefully
            initiateDownloadFlow(this, null);
        });

        // Setup .torrent link
        document.getElementById('detail-torrent-link').href = data.download_link;
    }

    // Confirm Download Modal Action
    document.getElementById('confirm-download-btn')?.addEventListener('click', function () {
        if (!pendingDownloadData) return;
        pendingDownloadData.custom_relative_path = confirmInput.value;
        confirmModal.hide();
        performDownload(pendingDownloadData, pendingButton);
    });

    // Toggle Series in Path Button
    document.getElementById('add-series-btn')?.addEventListener('click', function () {
        const input = document.getElementById('confirm-path-input');
        const hintEl = document.getElementById('path-format-hint');
        const { cleanAuthor, cleanTitle, cleanSeries, active } = this.dataset;
        const isActive = active === "true";

        if (!isActive) {
            input.value = `${cleanAuthor}/${cleanSeries}/${cleanTitle}`;
            if (hintEl) hintEl.textContent = "Format: Author / Series / Title";
            this.innerHTML = '<i class="bi bi-dash-lg"></i> Series';
            this.classList.replace('btn-outline-secondary', 'btn-secondary');
            this.classList.add('text-white');
            this.dataset.active = "true";
        } else {
            input.value = `${cleanAuthor}/${cleanTitle}`;
            if (hintEl) hintEl.textContent = "Format: Author / Title";
            this.innerHTML = '<i class="bi bi-plus-lg"></i> Series';
            this.classList.replace('btn-secondary', 'btn-outline-secondary');
            this.classList.remove('text-white');
            this.dataset.active = "false";
        }
        input.dispatchEvent(new Event('input'));
    });

    function performDownload(downloadData, button) {
        if (button) button.disabled = true;

        fetch('/client/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(downloadData),
        })
            .then(response => response.json())
            .then(async data => {
                // 1. Handle Insufficient Buffer
                if (data.status === 'insufficient_buffer') {
                    // ... (Population of buffer modal fields) ...
                    document.getElementById('modal-buffer-gb').textContent = data.buffer_gb || 0;
                    document.getElementById('modal-torrent-size').textContent = data.torrent_size_gb || 0;
                    document.getElementById('modal-needed-gb').textContent = data.needed_gb || 0;
                    document.getElementById('modal-recommended-amount').textContent = data.recommended_amount || 0;
                    document.getElementById('modal-recommended-cost').textContent = (data.recommended_cost || 0).toLocaleString();
                    const buyBtn = document.getElementById('modal-buy-recommended');
                    if (buyBtn) buyBtn.dataset.amount = data.recommended_amount || 0;

                    window.pendingDownload = downloadData;
                    new bootstrap.Modal(document.getElementById('insufficientBufferModal')).show();

                    if (button) button.disabled = false;
                    return;
                }

                // 2. Show Server Message
                showToast(data.message || data.error, data.message ? 'success' : 'danger');

                // 3. Update UI on Success
                if (data.message) {
                    if (button) button.textContent = 'Added!';

                    // Find the row
                    let resultItem = button.closest ? button.closest('.result-item') : null;
                    if (!resultItem && downloadData.id) {
                        resultItem = document.querySelector(`.result-item[data-torrent-id="${downloadData.id}"]`);
                    }

                    if (resultItem) {
                        // FIX: Select ALL status containers (Desktop & Mobile)
                        const statusContainers = resultItem.querySelectorAll('.torrent-status-container');

                        // FIX: Loop through them to update both
                        statusContainers.forEach(el => {
                            el.innerHTML = `<span class="badge bg-info text-wrap">Resolving torrent...</span>`;
                        });

                        // Start Polling
                        let attempts = 0;
                        const pollInterval = setInterval(async () => {
                            attempts++;
                            const hash = await getTorrentHashByMID(downloadData.id);

                            if (hash) {
                                clearInterval(pollInterval);
                                pollTorrentStatus(hash, resultItem);
                                fetchAndUpdateTorrentStatus(hash, resultItem);
                            } else if (attempts >= 15) {
                                clearInterval(pollInterval);
                                // Update all containers again
                                statusContainers.forEach(el => {
                                    el.innerHTML = `<span class="badge bg-warning">Added (pending)</span>`;
                                });
                            }
                        }, 2000);
                    }
                } else if (button) {
                    // If message is missing (error state), re-enable button
                    button.disabled = false;
                }
            })
            .catch(error => {
                console.error("Download Logic Error:", error);
                showToast("Error adding torrent.", 'danger');
                if (button) button.disabled = false;
            });
    }

    // Modal: Buy Recommended Buffer Action
    document.getElementById('modal-buy-recommended')?.addEventListener('click', function () {
        const amount = parseFloat(this.dataset.amount);
        this.disabled = true;
        this.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Buying...';
        fetch('/mam/buy_upload', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ amount: amount })
        })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showToast(`Purchased ${amount} GB`, 'success');
                    loadMamUserData();
                    bootstrap.Modal.getInstance(document.getElementById('insufficientBufferModal'))?.hide();
                    if (window.pendingDownload) {
                        performDownload(window.pendingDownload, null);
                        window.pendingDownload = null;
                    }
                } else { showToast(data.error || 'Failed', 'danger'); }
            })
            .finally(() => { this.disabled = false; this.innerHTML = `Buy ${amount} GB`; });
    });
});