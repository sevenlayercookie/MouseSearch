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
let lastPerformedQuery = null;
window.currentVipUntil = null;
window.currentBonusPoints = 0;
window.isVipActive = false;
window.appliedPersonalFreeleechIds = window.appliedPersonalFreeleechIds || new Set();
let langTomSelect = null;
let catTomSelect = null;
let mainCatPrimaryTomSelect = null;
let mainCatFilterTomSelect = null;
let legacyCategoryData = null;
let legacyCategoryPromise = null;
let categoryMainCatMap = new Map();
let mainCatSelectSyncing = false;
let categoryAllowedMainCats = null;
const AUTOSUGGEST_CACHE_MAX_ENTRIES = 300;
const AUTOSUGGEST_CACHE_TTL_MS = 60 * 60 * 1000;
const autosuggestCache = window.__autosuggestCache instanceof Map ? window.__autosuggestCache : new Map();
window.__autosuggestCache = autosuggestCache;
const UPLOAD_AMOUNT_STEP = 50;
const UPLOAD_AMOUNT_MIN = 50;
const UPLOAD_AMOUNT_MAX = 200;
const UPLOAD_COST_PER_GB = 500;
// Validation for upload purchase amounts
if (!window.VALID_UPLOAD_AMOUNTS || window.VALID_UPLOAD_AMOUNTS.length === 0) {
    window.VALID_UPLOAD_AMOUNTS = [50, 100, 150, 200];
}

const HAPTIC_PATTERNS = Object.freeze({
    tap: 15,
    light: 10,
    search: [20, 35, 20],
    accordion: 12,
    menu: 20,
    tab: 12,
    save: [20, 45, 20],
    modal: [25],
    download: [30, 40, 20]
});
let hapticsEnabled = true;

function setHapticsEnabled(value) {
    hapticsEnabled = !!value;
}

function canVibrate() {
    return typeof navigator !== 'undefined' && typeof navigator.vibrate === 'function';
}

function triggerFallbackHaptic() {
    if (!document.body) return;

    const wrapper = document.createElement('div');
    const inputId = `haptic-fallback-${Math.random().toString(36).slice(2)}`;

    wrapper.innerHTML = `<input type="checkbox" id="${inputId}" switch /><label for="${inputId}"></label>`;
    wrapper.setAttribute('style', 'display:none !important;opacity:0 !important;visibility:hidden !important;position:absolute !important;pointer-events:none !important;');
    document.body.appendChild(wrapper);

    const label = wrapper.querySelector('label');
    if (label) label.click();

    setTimeout(() => {
        wrapper.remove();
    }, 300);
}

function triggerHaptic(pattern = 'tap') {
    if (!hapticsEnabled) return;

    const selectedPattern = HAPTIC_PATTERNS[pattern] ?? pattern ?? HAPTIC_PATTERNS.tap;
    try {
        if (canVibrate()) {
            navigator.vibrate(selectedPattern);
        } else {
            triggerFallbackHaptic();
        }
    } catch (_) {
        // Ignore haptic errors so UI interactions are never blocked.
    }
}

function getTomSelectValues(instance) {
    if (!instance) return [];
    const value = instance.getValue();
    if (Array.isArray(value)) return value;
    if (!value) return [];
    if (typeof value === 'string') {
        const delimiter = instance.settings?.delimiter || ',';
        return value.split(delimiter).filter(Boolean);
    }
    return [String(value)];
}

function loadLegacyCategoryDefinitions() {
    if (legacyCategoryData) return Promise.resolve(legacyCategoryData);
    if (legacyCategoryPromise) return legacyCategoryPromise;

    const legacyUrl = window.LEGACY_CATEGORY_URL || '/static/categoryDefinitionsLegacy.json';
    legacyCategoryPromise = fetch(legacyUrl, { cache: 'no-store' })
        .then(response => {
            if (!response.ok) throw new Error('Failed to load legacy categories');
            return response.json();
        })
        .then(data => {
            legacyCategoryData = data;
            return data;
        })
        .catch(err => {
            console.error('Unable to load legacy category definitions', err);
            return null;
        });

    return legacyCategoryPromise;
}

let DEFAULT_MAIN_CATS = [];

function normalizeMainCatValues(values) {
    return [...new Set(values.map(String).filter(Boolean))];
}

function isMainCatAllowed(mainCat) {
    if (!mainCat) return true;
    if (!categoryAllowedMainCats || !categoryAllowedMainCats.size) return true;
    return categoryAllowedMainCats.has(String(mainCat));
}

function decorateCategoryOptions() {
    if (!catTomSelect?.dropdown_content) return;
    const optionNodes = catTomSelect.dropdown_content.querySelectorAll('.option');
    optionNodes.forEach(node => {
        const value = node.getAttribute('data-value');
        const mainCat = categoryMainCatMap.get(String(value));
        const allowed = isMainCatAllowed(mainCat);
        node.classList.toggle('ts-option-disabled', !allowed);
        node.removeAttribute('aria-disabled');
    });
}

function updateMaxUploadPurchaseDisplay() {
    const maxButton = document.getElementById('upload-max-button');
    const maxAmount = document.getElementById('upload-max-amount');
    const maxCost = document.getElementById('upload-max-cost');
    if (!maxButton || !maxAmount || !maxCost) return;

    const maxAffordableRaw = Math.floor(window.currentBonusPoints / UPLOAD_COST_PER_GB / UPLOAD_AMOUNT_MIN) * UPLOAD_AMOUNT_MIN;
    const maxAffordable = Math.min(maxAffordableRaw, UPLOAD_AMOUNT_MAX);
    if (maxAffordable >= UPLOAD_AMOUNT_MIN) {
        maxAmount.textContent = maxAffordable.toLocaleString();
        maxCost.textContent = (maxAffordable * UPLOAD_COST_PER_GB).toLocaleString();
        maxButton.disabled = false;
    } else {
        maxAmount.textContent = 'Need';
        maxCost.textContent = (UPLOAD_AMOUNT_MIN * UPLOAD_COST_PER_GB).toLocaleString();
        maxButton.disabled = true;
    }
}

/**
 * Global helper to toggle switch when header is clicked.
 * Exposed to window for HTML onclick attributes.
 */
window.toggleCardSwitch = function (checkboxId) {
    const checkbox = document.getElementById(checkboxId);
    if (checkbox) {
        triggerHaptic('accordion');
        checkbox.click();
    }
};

/**
 * Maps MAM poster_type mime to file extension.
 * Defaults to 'jpeg' if unknown.
 */
function getPosterExtension(mimeType) {
    if (!mimeType) return 'jpeg';
    const map = {
        'image/jpeg': 'jpeg',
        'image/jpg': 'jpg',
        'image/png': 'png',
        'image/gif': 'gif',
        'image/webp': 'webp'
    };
    return map[mimeType] || 'jpeg';
}

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

const DEFAULT_REL_PATH_TEMPLATE = "{Author}/{Title}";
let savedRelPathTemplate = DEFAULT_REL_PATH_TEMPLATE;

function setSavedRelPathTemplate(value) {
    const normalized = normalizeRelPathTemplate(value || DEFAULT_REL_PATH_TEMPLATE);
    savedRelPathTemplate = normalized || DEFAULT_REL_PATH_TEMPLATE;
}

function normalizeRelPathTemplate(template) {
    return String(template || "").replace(/\\/g, '/');
}

function stripSeriesTokenFromTemplate(template) {
    let cleaned = normalizeRelPathTemplate(template)
        .split('{Series}').join('')
        .split('{SeriesNumber}').join('');
    cleaned = cleaned.replace(/\/+/g, '/').replace(/^\/+|\/+$/g, '');
    return cleaned || DEFAULT_REL_PATH_TEMPLATE;
}

function insertSeriesTokenIntoTemplate(template) {
    const normalized = normalizeRelPathTemplate(template);
    if (!normalized) return "{Author}/{Series}/{Title}";
    if (normalized.includes('{Series}')) return normalized;
    if (normalized.includes('{Author}') && normalized.includes('{Title}')) {
        return normalized.replace('{Author}', '{Author}/{Series}');
    }
    return normalized.endsWith('/') ? `${normalized}{Series}` : `${normalized}/{Series}`;
}

function buildRelativePathFromTemplate(template, values) {
    let output = normalizeRelPathTemplate(template);
    const replacements = {
        '{Author}': values.author || '',
        '{Series}': values.series || '',
        '{SeriesNumber}': values.seriesNumber || '',
        '{Title}': values.title || ''
    };
    for (const [token, value] of Object.entries(replacements)) {
        output = output.split(token).join(value);
    }
    output = output.replace(/\/+/g, '/').replace(/^\/+|\/+$/g, '');
    return output;
}

function getRelPathTemplateValue() {
    return savedRelPathTemplate || DEFAULT_REL_PATH_TEMPLATE;
}

function setSeriesToggleButtonState(button, isActive) {
    if (!button) return;
    button.dataset.active = isActive ? "true" : "false";
    if (isActive) {
        button.innerHTML = '<i class="bi bi-dash-lg"></i> Series';
        button.classList.replace('btn-outline-secondary', 'btn-secondary');
        button.classList.add('text-white');
    } else {
        button.innerHTML = '<i class="bi bi-plus-lg"></i> Series';
        button.classList.replace('btn-secondary', 'btn-outline-secondary');
        button.classList.remove('text-white');
    }
}

function getSeriesName(seriesJsonStr) {
    const primarySeries = getPrimarySeriesInfo(seriesJsonStr);
    return primarySeries?.name || null;
}

function normalizeSeriesNumber(seriesNumber) {
    if (seriesNumber === null || seriesNumber === undefined) return '';
    if (typeof seriesNumber === 'number') {
        if (!Number.isFinite(seriesNumber) || seriesNumber < 0) return '';
        return Number.isInteger(seriesNumber) ? String(seriesNumber) : String(seriesNumber).replace(/\.?0+$/, '');
    }

    const numberText = String(seriesNumber).trim();
    if (!numberText) return '';

    const parsed = Number(numberText);
    if (!Number.isNaN(parsed) && Number.isFinite(parsed) && parsed >= 0) {
        return Number.isInteger(parsed) ? String(parsed) : String(parsed).replace(/\.?0+$/, '');
    }

    return numberText;
}

function getPrimarySeriesInfo(seriesInfo) {
    try {
        if (!seriesInfo) return null;
        const data = typeof seriesInfo === 'object' ? seriesInfo : JSON.parse(seriesInfo);
        const values = Object.values(data);
        if (values.length > 0 && Array.isArray(values[0])) {
            const [seriesName, seriesNumber] = values[0];
            const name = String(seriesName || '').trim();
            if (!name) return null;
            return {
                name,
                number: normalizeSeriesNumber(seriesNumber)
            };
        }
    } catch (e) {
        console.error("Error parsing series info:", e);
    }
    return null;
}

function formatPrimarySeriesLabel(primarySeries) {
    if (!primarySeries?.name) return '';
    return primarySeries.number ? `${primarySeries.name} #${primarySeries.number}` : primarySeries.name;
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
                    if (userData.seedbonus !== undefined) {
                        window.currentBonusPoints = parseFloat(userData.seedbonus || 0);
                        updateMaxUploadPurchaseDisplay();
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
                        const reason = data.reason === 'ratio'
                            ? 'low ratio'
                            : data.reason === 'buffer'
                                ? 'low buffer'
                                : data.reason === 'bonus'
                                    ? 'bonus points'
                                    : 'manual';
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

function renderJsonTree(data, containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    
    container.innerHTML = ''; // Clear previous

    // 1. Safety Check & Parsing
    let jsonData = data;
    
    // Handle "empty" cases
    if (!data || data === "{}" || (typeof data === 'string' && data.trim() === "{}")) {
        container.innerHTML = '<div class="text-secondary small fst-italic text-center py-2">No technical metadata available</div>';
        return;
    }

    // Parse stringified JSON if needed
    if (typeof data === 'string') {
        try {
            jsonData = JSON.parse(data);
        } catch (e) {
            console.error("JSON Parse Error:", e);
            container.innerHTML = '<div class="alert alert-warning py-1 small mb-0">Error loading MediaInfo</div>';
            return;
        }
    }

    // 2. Recursive Tree Builder
    function createTree(obj) {
        const root = document.createElement('div');
        
        for (const [key, value] of Object.entries(obj)) {
            // Case A: Value is an Object or Array (Accordion)
            if (value !== null && typeof value === 'object') {
                const details = document.createElement('details');
                
                // Auto-open "General" and "Audio" keys for better UX
                if (key === 'General' || key.startsWith('Audio')) details.open = true;

                const summary = document.createElement('summary');
                const sizeLabel = Array.isArray(value) ? ` [${value.length}]` : '';
                
                // Styling the summary text
                summary.innerHTML = `<span class="opacity-75">${key}</span><small class="text-muted ms-1">${sizeLabel}</small>`;
                
                details.appendChild(summary);
                details.appendChild(createTree(value)); // Recursion
                root.appendChild(details);
            } 
            // Case B: Value is Primitive (Row)
            else {
                const row = document.createElement('div');
                row.className = 'json-row';
                
                let displayValue = value;
                if (value === null) displayValue = 'null';
                
                row.innerHTML = `<span class="json-key">${key}:</span><span class="json-val">${displayValue}</span>`;
                root.appendChild(row);
            }
        }
        return root;
    }

    // 3. Render
    const treeRoot = createTree(jsonData);
    treeRoot.className = 'json-tree';
    container.appendChild(treeRoot);
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

const downloadedResultStates = new Set(['uploading', 'stalledUP', 'checkingUP', 'forcedUP', 'pausedUP', 'queuedUP']);

function isDownloadedResultItem(item) {
    if (!item) return false;
    if (String(item.dataset.snatched || '0') === '1') return true;

    const state = String(item.dataset.clientState || '').trim();
    if (downloadedResultStates.has(state)) return true;

    const progress = Number(item.dataset.clientProgress || 0);
    return Number.isFinite(progress) && progress >= 1;
}

function applyHideDownloadedResultsFilter() {
    const container = document.getElementById('results-container');
    if (!container) return;

    const hideDownloaded = !!document.getElementById('hide_downloaded')?.checked;
    const items = container.querySelectorAll('.result-item');
    let visibleCount = 0;

    items.forEach(item => {
        const shouldHide = hideDownloaded && isDownloadedResultItem(item);
        item.classList.toggle('d-none', shouldHide);
        if (!shouldHide) visibleCount++;
    });

    const titleEl = document.getElementById('results-title');
    if (titleEl) {
        titleEl.textContent = `Results (${visibleCount})`;
    }
}

function updateTorrentUI(hash, data, resultItem) {
    // 1. Generate the HTML (Same logic as before)
    const state = data.state || 'unknown';
    const progressPercent = Math.floor((data.progress || 0) * 100);
    const etaSeconds = data.eta || 0;
    const trackerError = String(data.tracker_error || '').trim();

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
        const errorText = trackerError || state;
        htmlContent = `<div class="alert alert-danger py-1 px-2 mb-0 small text-center"><i class="bi bi-exclamation-triangle-fill"></i> Error: ${errorText}</div>`;
    } else {
        htmlContent = `<div class="badge bg-secondary">State: ${state}</div>`;
    }

    if (trackerError && !errorStates.includes(state)) {
        htmlContent += `<div class="alert alert-warning py-1 px-2 mt-1 mb-0 small text-start"><i class="bi bi-exclamation-triangle"></i> Tracker: ${trackerError}</div>`;
    }

    // 2. Update the Search Result Row (Desktop & Mobile)
    if (resultItem) {
        resultItem.dataset.clientState = state;
        resultItem.dataset.clientProgress = String(data.progress || 0);
        const rowContainers = resultItem.querySelectorAll('.torrent-status-container');
        rowContainers.forEach(container => {
            container.innerHTML = htmlContent;
        });

        applyHideDownloadedResultsFilter();
    }

    // 3. [NEW] Update the Modal Footer if it's open and matches this book
    const modalBtn = document.getElementById('detail-download-btn');
    const modalContainer = document.querySelector('#details-footer .torrent-status-container');

    // We check if the modal is actually open (visible) to avoid errors
    const isModalOpen = document.getElementById('bookDetailsModal').classList.contains('show');

    if (isModalOpen && modalBtn && modalContainer && resultItem) {
        // Compare the ID stored in the result row with the ID stored in the modal button
        if (resultItem.dataset.torrentId === modalBtn.dataset.id) {
            modalContainer.innerHTML = htmlContent;
        }
    }
}

function pollTorrentStatus(hash, resultItem) {
    hashToElementMap.set(hash, resultItem);

    const waitingHtml = `<span class="badge bg-info text-wrap">Waiting for updates...</span>`;

    // 1. Update List Item Containers
    if (resultItem) {
        const statusContainers = resultItem.querySelectorAll('.torrent-status-container');
        statusContainers.forEach(container => {
            container.innerHTML = waitingHtml;
        });
    }

    // 2. [NEW] Update Modal Container if match
    const modalBtn = document.getElementById('detail-download-btn');
    const modalContainer = document.querySelector('#details-footer .torrent-status-container');

    if (modalBtn && modalContainer && resultItem) {
        if (resultItem.dataset.torrentId === modalBtn.dataset.id) {
            modalContainer.innerHTML = waitingHtml;
        }
    }
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
            updateMaxUploadPurchaseDisplay();

            // VIP status is used for VIP Freeleech (fl_vip) torrents.
            window.isVipActive = false;
            if (window.currentVipUntil) {
                const now = new Date();
                const vipDate = new Date(window.currentVipUntil.replace(' ', 'T'));
                window.isVipActive = !isNaN(vipDate) && vipDate > now;
            }

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

document.addEventListener("DOMContentLoaded", async function () {
    initializeEventStream();

    // Init Tooltips
    [...document.querySelectorAll('[data-bs-toggle="tooltip"]')].map(el => new bootstrap.Tooltip(el));

    localizeDates();

    // Initial Fetches
    fetchPublicIP();
    checkClientStatus();
    loadMamUserData();

    const hapticsToggle = document.getElementById('HAPTICS_ENABLED');
    const syncHapticsState = () => {
        const enabled = hapticsToggle ? !!hapticsToggle.checked : true;
        setHapticsEnabled(enabled);
    };

    syncHapticsState();

    if (hapticsToggle) {
        hapticsToggle.addEventListener('change', function () {
            setHapticsEnabled(this.checked);
            if (this.checked) {
                triggerHaptic('light');
            }
        });
    }

    const settingsMenuButton = document.querySelector('.navbar-toggler[data-bs-target="#settingsOffcanvas"]');
    if (settingsMenuButton) {
        settingsMenuButton.addEventListener('click', () => {
            triggerHaptic('menu');
        });
    }

    document.querySelectorAll('.accordion .accordion-button').forEach(btn => {
        btn.addEventListener('click', () => {
            triggerHaptic('accordion');
        });
    });

    document.addEventListener('click', (event) => {
        if (event.target.closest('#mediainfo-tree-container summary')) {
            triggerHaptic('accordion');
        }
    });

    document.querySelectorAll('#settingTabs [data-bs-toggle="tab"]').forEach(tabButton => {
        tabButton.addEventListener('click', () => {
            triggerHaptic('tab');
        });
    });

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
            { trigger: 'AUTO_BUY_UPLOAD_ON_BUFFER', target: ['AUTO_BUY_UPLOAD_BUFFER_THRESHOLD', 'AUTO_BUY_UPLOAD_BUFFER_AMOUNT'] },
            { trigger: 'AUTO_BUY_UPLOAD_ON_BONUS', target: ['AUTO_BUY_UPLOAD_BONUS_THRESHOLD', 'AUTO_BUY_UPLOAD_BONUS_AMOUNT'] }
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
        const bonusOn = isChecked('AUTO_BUY_UPLOAD_ON_BONUS');
        const uploadContainer = document.getElementById('upload-check-interval-container');
        const uploadInput = document.getElementById('AUTO_BUY_UPLOAD_CHECK_INTERVAL_HOURS');

        if (uploadContainer) {
            uploadContainer.classList.toggle('d-none', !ratioOn && !bufferOn && !bonusOn);
        }
        if (uploadInput) uploadInput.disabled = (!ratioOn && !bufferOn && !bonusOn);

        // Auto Organize Path Logic
        const organizeOnAdd = isChecked('AUTO_ORGANIZE_ON_ADD');
        const organizeOnSchedule = isChecked('AUTO_ORGANIZE_ON_SCHEDULE');
        const pathContainer = document.getElementById('path-configuration-container');
        const autoOrganizeAdvancedSection = document.getElementById('auto-organize-advanced-section');
        if (pathContainer) {
            pathContainer.classList.toggle('d-none', !organizeOnAdd && !organizeOnSchedule);
        }
        if (autoOrganizeAdvancedSection) {
            const shouldShowAutoOrganizeAdvanced = organizeOnAdd || organizeOnSchedule;
            const advancedSectionCollapse = bootstrap.Collapse.getOrCreateInstance(autoOrganizeAdvancedSection, { toggle: false });
            shouldShowAutoOrganizeAdvanced ? advancedSectionCollapse.show() : advancedSectionCollapse.hide();
        }
    }


    ['AUTO_ORGANIZE_ON_ADD', 'AUTO_ORGANIZE_ON_SCHEDULE'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', updateDependentFields);
    });

    updateDependentFields();

    // --- Settings Snapshot & Revert Logic ---
    const settingsForm = document.getElementById('settings-form');
    const settingsOffcanvasEl = document.getElementById('settingsOffcanvas');
    let settingsSnapshot = null;
    let settingsDirty = false;
    let isRestoringSettings = false;

    function captureSettingsSnapshot() {
        if (!settingsForm) return;
        settingsSnapshot = {};
        const arrayFields = {};
        settingsForm.querySelectorAll('input, select, textarea').forEach(el => {
            if (!el.name) return;
            if (el.name.endsWith('[]')) {
                if (!arrayFields[el.name]) arrayFields[el.name] = [];
                arrayFields[el.name].push(el.type === 'checkbox' ? el.checked : el.value);
                return;
            }
            if (el.type === 'checkbox') {
                settingsSnapshot[el.name] = el.checked;
            } else {
                settingsSnapshot[el.name] = el.value;
            }
        });
        Object.entries(arrayFields).forEach(([name, values]) => {
            settingsSnapshot[name] = values;
        });
        settingsDirty = false;
        setSavedRelPathTemplate(settingsSnapshot.REL_PATH_TEMPLATE || DEFAULT_REL_PATH_TEMPLATE);
    }

    function restoreSettingsSnapshot() {
        if (!settingsForm || !settingsSnapshot) return;
        isRestoringSettings = true;

        if (destinationPathsList) {
            const paths = Array.isArray(settingsSnapshot['extra_dest_paths[]']) ? settingsSnapshot['extra_dest_paths[]'] : [];
            const defaults = Array.isArray(settingsSnapshot['extra_dest_defaults[]']) ? settingsSnapshot['extra_dest_defaults[]'] : [];
            const rows = paths.map((path, idx) => ({
                path,
                default_main_cat: defaults[idx] || ''
            }));
            renderDestinationRows(rows);
        }

        settingsForm.querySelectorAll('input, select, textarea').forEach(el => {
            if (el.name?.endsWith('[]')) return;
            if (!el.name || !(el.name in settingsSnapshot)) return;
            if (el.type === 'checkbox') {
                el.checked = !!settingsSnapshot[el.name];
            } else {
                el.value = settingsSnapshot[el.name];
            }
        });
        isRestoringSettings = false;
        settingsDirty = false;
        updateDependentFields();
        setSavedRelPathTemplate(settingsSnapshot.REL_PATH_TEMPLATE || DEFAULT_REL_PATH_TEMPLATE);
        syncHapticsState();
        const relTemplateInput = document.getElementById('REL_PATH_TEMPLATE');
        if (relTemplateInput) relTemplateInput.dispatchEvent(new Event('input', { bubbles: true }));
        updateConfirmPathPreview();
    }

    if (settingsForm) {
        settingsForm.addEventListener('input', () => {
            if (isRestoringSettings) return;
            settingsDirty = true;
        });
        settingsForm.addEventListener('change', () => {
            if (isRestoringSettings) return;
            settingsDirty = true;
        });
    }

    if (settingsOffcanvasEl) {
        settingsOffcanvasEl.addEventListener('show.bs.offcanvas', () => {
            captureSettingsSnapshot();
        });
        settingsOffcanvasEl.addEventListener('hide.bs.offcanvas', () => {
            if (settingsDirty) {
                restoreSettingsSnapshot();
            }
        });
    }

    if (settingsForm) {
        captureSettingsSnapshot();
    }

    // --- Directory Structure Logic ---
    const relTemplateInput = document.getElementById('REL_PATH_TEMPLATE');
    const previewOutput = document.getElementById('structure-preview-output');
    const resetTemplateBtn = document.getElementById('reset-template-btn');

    if (relTemplateInput && previewOutput) {
        let relTemplateCaretRange = null;

        const captureRelTemplateCaretRange = () => {
            if (document.activeElement !== relTemplateInput) return;
            const start = relTemplateInput.selectionStart;
            const end = relTemplateInput.selectionEnd;
            if (typeof start === 'number' && typeof end === 'number') {
                relTemplateCaretRange = { start, end };
            }
        };

        const previewData = {
            '{Author}': 'J.K. Rowling',
            '{Series}': 'Harry Potter',
            '{SeriesNumber}': '1',
            '{Title}': "Harry Potter and the Sorcerer's Stone"
        };

        const updatePathPreview = () => {
            const template = relTemplateInput.value?.trim() || DEFAULT_REL_PATH_TEMPLATE;
            let preview = template;

            for (const [token, value] of Object.entries(previewData)) {
                preview = preview.split(token).join(value);
            }

            preview = preview.replace(/\/+/g, '/');
            previewOutput.textContent = preview;
        };

        relTemplateInput.addEventListener('input', updatePathPreview);
        relTemplateInput.addEventListener('keyup', captureRelTemplateCaretRange);
        relTemplateInput.addEventListener('click', captureRelTemplateCaretRange);
        relTemplateInput.addEventListener('select', captureRelTemplateCaretRange);
        relTemplateInput.addEventListener('focus', captureRelTemplateCaretRange);

        document.querySelectorAll('.insert-token-btn').forEach(btn => {
            btn.addEventListener('mousedown', () => {
                captureRelTemplateCaretRange();
            });

            btn.addEventListener('click', () => {
                const token = btn.dataset.token;
                if (!token) return;

                const hasCaretRange = relTemplateCaretRange
                    && typeof relTemplateCaretRange.start === 'number'
                    && typeof relTemplateCaretRange.end === 'number';

                if (hasCaretRange) {
                    const currentVal = relTemplateInput.value || '';
                    const boundedStart = Math.max(0, Math.min(relTemplateCaretRange.start, currentVal.length));
                    const boundedEnd = Math.max(boundedStart, Math.min(relTemplateCaretRange.end, currentVal.length));
                    const before = currentVal.slice(0, boundedStart);
                    const after = currentVal.slice(boundedEnd);
                    relTemplateInput.value = `${before}${token}${after}`;
                    const nextCaretPos = boundedStart + token.length;
                    relTemplateInput.focus();
                    relTemplateInput.setSelectionRange(nextCaretPos, nextCaretPos);
                    relTemplateCaretRange = { start: nextCaretPos, end: nextCaretPos };
                } else {
                    let currentVal = relTemplateInput.value || '';

                    if (currentVal.length > 0 && !currentVal.endsWith('/') && token !== '/') {
                        currentVal += '/';
                    }

                    relTemplateInput.value = currentVal + token;
                    const endPos = relTemplateInput.value.length;
                    relTemplateInput.focus();
                    relTemplateInput.setSelectionRange(endPos, endPos);
                    relTemplateCaretRange = { start: endPos, end: endPos };
                }

                relTemplateInput.dispatchEvent(new Event('input', { bubbles: true }));
            });
        });

        resetTemplateBtn?.addEventListener('click', () => {
            relTemplateInput.value = DEFAULT_REL_PATH_TEMPLATE;
            relTemplateInput.dispatchEvent(new Event('input', { bubbles: true }));
        });

        updatePathPreview();
    }

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
    function normalizeUploadAmount(value) {
        const numValue = parseFloat(value);
        if (isNaN(numValue)) return UPLOAD_AMOUNT_MIN;
        const rounded = Math.round(numValue / UPLOAD_AMOUNT_STEP) * UPLOAD_AMOUNT_STEP;
        return Math.min(UPLOAD_AMOUNT_MAX, Math.max(UPLOAD_AMOUNT_MIN, rounded));
    }
    document.querySelectorAll('.upload-amount-input').forEach(input => {
        input.addEventListener('blur', function () {
            const valid = normalizeUploadAmount(this.value);
            if (parseFloat(this.value) !== valid) this.value = valid;
        });
    });

    updateMaxUploadPurchaseDisplay();

    // --- B. Button Handlers (Save, VIP, Upload) ---

    // Save Settings
    document.getElementById('save-settings-button')?.addEventListener('click', function () {
        triggerHaptic('save');
        fetch('/update_settings', { method: 'POST', body: new FormData(document.getElementById('settings-form')) })
            .then(response => response.json())
            .then(data => {
                showToast(data.message, data.status === 'success' ? 'success' : 'danger');
                if (data.status === 'success') {
                    captureSettingsSnapshot();
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

            if (purchaseWeeks < 1) {
                maxTitle.textContent = "Top Up Max";
                maxSubtitle.textContent = "Minimum 1 week";
                maxCostBadge.textContent = `${VIP_COST_PER_WEEK.toLocaleString()} BP`;
                maxBtn.disabled = true;
                maxBtn.classList.remove('btn-success');
                maxBtn.classList.add('btn-secondary');
            } else {
                const purchaseCost = Math.ceil(purchaseWeeks * VIP_COST_PER_WEEK);
                maxTitle.textContent = `Top Up +${purchaseWeeks.toFixed(1)} Weeks`;
                maxSubtitle.textContent = weeksAffordable < weeksToCap ? "Limited by points" : "Reach 12.8 week limit";
                maxCostBadge.textContent = `${purchaseCost.toLocaleString()} BP`;
                maxBtn.classList.add('btn-success');
                maxBtn.disabled = false;
                maxBtn.classList.remove('btn-secondary');
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
    const appLogos = document.querySelectorAll('.app-logo');
    const STATIC_MOUSE_LOGO_SVG = `<svg
   viewBox="0 0 300 340"
   version="1.1"
   id="svg15"
   sodipodi:docname="mouse.svg"
   inkscape:version="1.4.3 (0d15f75, 2025-12-25)"
   xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
   xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
   xmlns="http://www.w3.org/2000/svg"
   xmlns:svg="http://www.w3.org/2000/svg">
  <sodipodi:namedview
     id="namedview15"
     pagecolor="#ffffff"
     bordercolor="#000000"
     borderopacity="0.25"
     inkscape:showpageshadow="2"
     inkscape:pageopacity="0.0"
     inkscape:pagecheckerboard="0"
     inkscape:deskcolor="#d1d1d1"
     inkscape:zoom="1.1113749"
     inkscape:cx="118.32191"
     inkscape:cy="130.01914"
     inkscape:window-width="1238"
     inkscape:window-height="626"
     inkscape:window-x="0"
     inkscape:window-y="34"
     inkscape:window-maximized="0"
     inkscape:current-layer="g15" />
  <defs
     id="defs7">
    <linearGradient
       id="headGrad"
       x1="0%"
       y1="0%"
       x2="0%"
       y2="100%">
      <stop
         offset="0%"
         stop-color="#ffffff"
         id="stop1" />
      <stop
         offset="100%"
         stop-color="#b095e6"
         id="stop2" />
    </linearGradient>
    <linearGradient
       id="earGrad"
       x1="0%"
       y1="0%"
       x2="0%"
       y2="100%">
      <stop
         offset="0%"
         stop-color="#a16ff2"
         id="stop3" />
      <stop
         offset="100%"
         stop-color="#763cd4"
         id="stop4" />
    </linearGradient>
    <radialGradient
       id="lensGrad"
       cx="175"
       cy="175"
       r="64.999998"
       fx="175"
       fy="175"
       gradientUnits="userSpaceOnUse">
      <stop
         offset="0%"
         stop-color="#d6b8ff"
         id="stop5" />
      <stop
         offset="50%"
         stop-color="#9f5ff0"
         id="stop6" />
      <stop
         offset="100%"
         stop-color="#6930c3"
         id="stop7" />
    </radialGradient>
  </defs>
  <g
     id="g15">
    <path
       d="M 90 200 C 20 280, 100 300, 160 275 C 200 260, 230 290, 220 320"
       fill="none"
       stroke="#100324"
       stroke-width="14"
       stroke-linecap="round"
       id="path7" />
    <circle
       cx="85"
       cy="80"
       r="48"
       fill="url(#earGrad)"
       stroke="#100324"
       stroke-width="12"
       id="circle7" />
    <circle
       cx="215"
       cy="80"
       r="48"
       fill="url(#earGrad)"
       stroke="#100324"
       stroke-width="12"
       id="circle8" />
    <ellipse
       cx="150"
       cy="150"
       rx="85"
       ry="90.34948"
       fill="url(#headGrad)"
       stroke="#100324"
       stroke-width="12"
       id="ellipse8" />
    <circle
       cx="117"
       cy="111"
       r="11"
       fill="#100324"
       id="circle9" />
    <circle
       cx="183"
       cy="111"
       r="11"
       fill="#100324"
       id="circle10" />
    <path
       d="M 91.197315,148.72121 H 124.22088"
       stroke="#100324"
       stroke-width="12.7207"
       stroke-linecap="round"
       id="path10" />
    <path
       d="m 90.013639,176.21869 30.205051,-5.43738"
       stroke="#100324"
       stroke-width="13.5626"
       stroke-linecap="round"
       id="path11" />
    <g
       id="g14"
       inkscape:label="g14"
       transform="translate(0,2)">
      <line
         x1="225"
         y1="225"
         x2="270"
         y2="280"
         stroke="#100324"
         stroke-width="28"
         stroke-linecap="round"
         id="line11" />
      <line
         x1="225"
         y1="225"
         x2="270"
         y2="280"
         stroke="#48188a"
         stroke-width="12"
         stroke-linecap="round"
         id="line12" />
      <circle
         cx="190"
         cy="190"
         r="60"
         fill="#ebe8f2"
         stroke="#100324"
         stroke-width="12"
         id="circle12" />
      <circle
         cx="190"
         cy="190"
         r="46"
         fill="url(#lensGrad)"
         stroke="#100324"
         stroke-width="8"
         id="circle13"
         style="fill:url(#lensGrad)" />
      <path
         d="m 162,173 a 30,30 0 0 1 35,-15"
         fill="none"
         stroke="#ffffff"
         stroke-width="8"
         stroke-linecap="round"
         id="path13" />
      <circle
         cx="158"
         cy="188"
         r="6"
         fill="#ffffff"
         id="circle14" />
    </g>
  </g>
</svg>`;
    const TAIL_ANIMATED_MOUSE_LOGO_SVG = `<svg
       viewBox="0 0 300 340"
       version="1.1"
       xmlns="http://www.w3.org/2000/svg"
       xmlns:svg="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient
           id="headGradTail"
           x1="0%"
           y1="0%"
           x2="0%"
           y2="100%">
          <stop offset="0%" stop-color="#ffffff" />
          <stop offset="100%" stop-color="#b095e6" />
        </linearGradient>
        <linearGradient
           id="earGradTail"
           x1="0%"
           y1="0%"
           x2="0%"
           y2="100%">
          <stop offset="0%" stop-color="#a16ff2" />
          <stop offset="100%" stop-color="#763cd4" />
        </linearGradient>
        <radialGradient
           id="lensGradTail"
           cx="35%"
           cy="35%"
           r="65%">
          <stop offset="0%" stop-color="#d6b8ff" />
          <stop offset="50%" stop-color="#9f5ff0" />
          <stop offset="100%" stop-color="#6930c3" />
        </radialGradient>
      </defs>
      <g>
        <!-- Tail -->
        <path
           fill="none"
           stroke="#100324"
           stroke-width="14"
           stroke-linecap="round">
          <animate
             attributeName="d"
             values="M 90 200 C 20 280, 100 300, 160 275 C 200 260, 230 290, 220 320;M 90 200 C 10 270, 80 310, 140 285 C 190 265, 210 280, 230 310;M 90 200 C 20 280, 100 300, 160 275 C 200 260, 230 290, 220 320"
             dur="1.5s"
             repeatCount="indefinite" />
        </path>
        
        <!-- Left Ear -->
        <circle
           cx="85"
           cy="80"
           r="48"
           fill="url(#earGradTail)"
           stroke="#100324"
           stroke-width="12">
          <animateTransform 
             attributeName="transform" 
             type="rotate" 
             values="0 120 120; -6 120 120; 0 120 120" 
             dur="1.6s" 
             repeatCount="indefinite" />
        </circle>
        
        <!-- Right Ear -->
        <circle
           cx="215"
           cy="80"
           r="48"
           fill="url(#earGradTail)"
           stroke="#100324"
           stroke-width="12">
          <animateTransform 
             attributeName="transform" 
             type="rotate" 
             values="0 180 120; 6 180 120; 0 180 120" 
             dur="1.6s" 
             repeatCount="indefinite" />
        </circle>
        
        <!-- Head -->
        <ellipse
           cx="150"
           cy="150"
           rx="85"
           ry="90.34948"
           fill="url(#headGradTail)"
           stroke="#100324"
           stroke-width="12" />
           
        <!-- Left Eye -->
        <circle
           cx="117"
           cy="111"
           r="11"
           fill="#100324" />
           
        <!-- Right Eye -->
        <circle
           cx="183"
           cy="111"
           r="11"
           fill="#100324" />
           
        <!-- Whiskers / Nose elements -->
        <path
           d="M 91.197315,148.72121 H 124.22088"
           stroke="#100324"
           stroke-width="12.7207"
           stroke-linecap="round" />
        <path
           d="m 90.013639,176.21869 30.205051,-5.43738"
           stroke="#100324"
           stroke-width="13.5626"
           stroke-linecap="round" />
           
        <!-- Magnifying Glass -->
        <g transform="translate(0,2)">
          <line
             x1="225"
             y1="225"
             x2="270"
             y2="280"
             stroke="#100324"
             stroke-width="28"
             stroke-linecap="round" />
          <line
             x1="225"
             y1="225"
             x2="270"
             y2="280"
             stroke="#48188a"
             stroke-width="12"
             stroke-linecap="round" />
          <circle
             cx="190"
             cy="190"
             r="60"
             fill="#ebe8f2"
             stroke="#100324"
             stroke-width="12" />
          <circle
             cx="190"
             cy="190"
             r="46"
             fill="url(#lensGradTail)"
             stroke="#100324"
             stroke-width="8" />
          <path
             d="m 162,173 a 30,30 0 0 1 35,-15"
             fill="none"
             stroke="#ffffff"
             stroke-width="8"
             stroke-linecap="round" />
          <circle
             cx="158"
             cy="188"
             r="6"
             fill="#ffffff" />
        </g>
      </g>
    </svg>`;
    const SEARCHING_MOUSE_LOGO_SVG = `<svg
   viewBox="0 0 300 340"
   version="1.1"
   id="svg16"
   sodipodi:docname="mouse-animated-glass-lower.svg"
   inkscape:version="1.4.3 (0d15f75, 2025-12-25)"
   xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
   xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
   xmlns="http://www.w3.org/2000/svg"
   xmlns:svg="http://www.w3.org/2000/svg">
  <sodipodi:namedview
     id="namedview16"
     pagecolor="#ffffff"
     bordercolor="#000000"
     borderopacity="0.25"
     inkscape:showpageshadow="2"
     inkscape:pageopacity="0.0"
     inkscape:pagecheckerboard="0"
     inkscape:deskcolor="#d1d1d1"
     inkscape:zoom="0.95559748"
     inkscape:cx="83.717257"
     inkscape:cy="134.47084"
     inkscape:window-width="1145"
     inkscape:window-height="782"
     inkscape:window-x="243"
     inkscape:window-y="34"
     inkscape:window-maximized="0"
     inkscape:current-layer="g16" />
  <defs
     id="defs7">
    <linearGradient
       id="headGradAnim"
       x1="0%"
       y1="0%"
       x2="0%"
       y2="100%">
      <stop
         offset="0%"
         stop-color="#ffffff"
         id="stop1" />
      <stop
         offset="100%"
         stop-color="#b095e6"
         id="stop2" />
    </linearGradient>
    <linearGradient
       id="earGradAnim"
       x1="0%"
       y1="0%"
       x2="0%"
       y2="100%">
      <stop
         offset="0%"
         stop-color="#a16ff2"
         id="stop3" />
      <stop
         offset="100%"
         stop-color="#763cd4"
         id="stop4" />
    </linearGradient>
    <radialGradient
       id="lensGradAnim"
       cx="175"
       cy="175"
       r="64.999998"
       fx="175"
       fy="175"
       gradientUnits="userSpaceOnUse">
      <stop
         offset="0%"
         stop-color="#d6b8ff"
         id="stop5" />
      <stop
         offset="50%"
         stop-color="#9f5ff0"
         id="stop6" />
      <stop
         offset="100%"
         stop-color="#6930c3"
         id="stop7" />
    </radialGradient>
  </defs>
  <!-- Main Group: Contains the breathing/bouncing animation -->
  <g
     id="g16">
    <animateTransform
       attributeName="transform"
       type="translate"
       values="0,0; 0,-10; 0,0"
       dur="1.5s"
       repeatCount="indefinite" />
    <!-- Tail (with swishing path animation) -->
    <path
       fill="none"
       stroke="#100324"
       stroke-width="14"
       stroke-linecap="round"
       id="path7">
      <animate
         attributeName="d"
         values="                     M 90 200 C 20 280, 100 300, 160 275 C 200 260, 230 290, 220 320;                     M 90 200 C 10 270, 80 310, 140 285 C 190 265, 210 280, 230 310;                     M 90 200 C 20 280, 100 300, 160 275 C 200 260, 230 290, 220 320"
         dur="1.5s"
         repeatCount="indefinite" />
    </path>
    <!-- Left Ear -->
    <circle
       cx="85"
       cy="80"
       r="48"
       fill="url(#earGradAnim)"
       stroke="#100324"
       stroke-width="12"
         id="circle7">
        <animateTransform
            attributeName="transform"
            type="rotate"
            values="0 120 120; -6 120 120; 0 120 120"
            dur="1.6s"
            repeatCount="indefinite" />
     </circle>
    <!-- Right Ear -->
    <circle
       cx="215"
       cy="80"
       r="48"
       fill="url(#earGradAnim)"
       stroke="#100324"
       stroke-width="12"
         id="circle8">
        <animateTransform
            attributeName="transform"
            type="rotate"
            values="0 180 120; 6 180 120; 0 180 120"
            dur="1.6s"
            repeatCount="indefinite" />
     </circle>
    <!-- Head Base -->
    <ellipse
       cx="150"
       cy="150"
       rx="85"
       ry="90.34948"
       fill="url(#headGradAnim)"
       stroke="#100324"
       stroke-width="12"
       id="ellipse8" />
    <!-- Left Eye -->
    <circle
       cx="117"
       cy="111"
       r="11"
       fill="#100324"
       id="circle9" />
    <!-- Right Eye -->
    <circle
       cx="183"
       cy="111"
       r="11"
       fill="#100324"
       id="circle10" />
    <!-- Left Whiskers -->
    <path
       d="M 91.197315,148.72121 H 124.22088"
       stroke="#100324"
       stroke-width="12.7207"
       stroke-linecap="round"
       id="path10" />
    <path
       d="m 90.013639,176.21869 30.205051,-5.43738"
       stroke="#100324"
       stroke-width="13.5626"
       stroke-linecap="round"
       id="path11" />
    <!-- Magnifying Glass (nested groups to combine translation and rotation cleanly) -->
    <g
       id="g15"
       transform="translate(0,2)">
      <!-- Translation: Scanning left and right -->
      <animateTransform
         attributeName="transform"
         type="translate"
         values="0,0; -15,5; 10,-3; 0,0"
         dur="2s"
         repeatCount="indefinite" />
      <g
         id="g14">
        <!-- Rotation: Subtle twisting as if searching -->
        <animateTransform
           attributeName="transform"
           type="rotate"
           values="-8 190 190; 5 190 190; -8 190 190"
           dur="2s"
           repeatCount="indefinite" />
        <!-- Handle Base -->
        <line
           x1="225"
           y1="225"
           x2="270"
           y2="280"
           stroke="#100324"
           stroke-width="28"
           stroke-linecap="round"
           id="line11" />
        <!-- Handle Core -->
        <line
           x1="225"
           y1="225"
           x2="270"
           y2="280"
           stroke="#48188a"
           stroke-width="12"
           stroke-linecap="round"
           id="line12" />
        <!-- Glass Outer Rim -->
        <circle
           cx="190"
           cy="190"
           r="60"
           fill="#ebe8f2"
           stroke="#100324"
           stroke-width="12"
           id="circle12" />
        <!-- Glass Lens -->
        <circle
           cx="190"
           cy="190"
           r="46"
           fill="url(#lensGradAnim)"
           stroke="#100324"
           stroke-width="8"
           id="circle13"
           style="fill:url(#lensGradAnim)" />
        <!-- Lens Glare Arc -->
        <path
           d="m 162,173 a 30,30 0 0 1 35,-15"
           fill="none"
           stroke="#ffffff"
           stroke-width="8"
           stroke-linecap="round"
           id="path13" />
        <!-- Lens Glare Dot -->
        <circle
           cx="158"
           cy="188"
           r="6"
           fill="#ffffff"
           id="circle14" />
      </g>
    </g>
  </g>
</svg>`;
    function renderInlineSvg(svgMarkup, className = '') {
        return String(svgMarkup || '').replace(
            '<svg',
            `<svg class="${className}" aria-hidden="true" focusable="false"`
        );
    }

    const STATIC_LOGO_MARKUP = renderInlineSvg(STATIC_MOUSE_LOGO_SVG, 'app-logo-svg');
    const TAIL_ANIMATED_LOGO_MARKUP = renderInlineSvg(TAIL_ANIMATED_MOUSE_LOGO_SVG, 'app-logo-svg');
    const ANIMATED_LOGO_MARKUP = renderInlineSvg(SEARCHING_MOUSE_LOGO_SVG, 'app-logo-svg');
    let activeSearchRequests = 0;
    let highlightedLogoCount = 0;
    const SEARCH_BUTTON_DEFAULT_HTML = searchButton ? searchButton.innerHTML : 'Search';
    const SEARCH_BUTTON_LOADING_HTML = `<span class="d-inline-flex align-items-center"><span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Searching...</span>`;
    const wrapper = document.getElementById('results-container-wrapper');
    const resultsTitle = document.getElementById('results-title');
    const resultsSortCurrent = document.getElementById('results-sort-current');
    const resultsSortOptions = document.querySelectorAll('.results-sort-option');
    const resultDisplayOptions = document.querySelectorAll('.result-display-option');
    const advancedOffcanvasEl = document.getElementById('advancedSearchOffcanvas');
    const filterBadge = document.getElementById('filter-count');
    const sectionFilterBadges = {
        fields: document.getElementById('filter-count-fields'),
        language: document.getElementById('filter-count-language'),
        status: document.getElementById('filter-count-status'),
        categories: document.getElementById('filter-count-cats'),
        flags: document.getElementById('filter-count-flags'),
        ranges: document.getElementById('filter-count-ranges')
    };

    const DEFAULT_LANGUAGE_ID = window.DEFAULT_LANGUAGE_ID ? String(window.DEFAULT_LANGUAGE_ID) : '1';
    let DEFAULT_SEARCH_TYPE = 'all';
    let DEFAULT_SEARCH_SCOPE = 'torrents';
    let DEFAULT_HIDE_DOWNLOADED = false;
    let DEFAULT_FLAGS_MODE = '0';
    let DEFAULT_LANGUAGE_VALUES = [DEFAULT_LANGUAGE_ID];
    let DEFAULT_LANGUAGE_SET = new Set(DEFAULT_LANGUAGE_VALUES);
    let DEFAULT_CATEGORY_IDS = [];
    let DEFAULT_FLAG_IDS = [];
    let DEFAULT_RANGE_FILTERS = {
        start_date: '',
        end_date: '',
        min_size: '',
        max_size: '',
        size_unit: '1048576',
        min_seeders: '',
        max_seeders: '',
        min_leechers: '',
        max_leechers: '',
        min_snatched: '',
        max_snatched: ''
    };
    let DEFAULT_SEARCH_FIELDS = {
        search_in_title: true,
        search_in_author: true,
        search_in_series: true,
        search_in_narrator: false,
        search_in_description: false,
        search_in_tags: false,
        search_in_filenames: false
    };

    const FILTER_TEXT_FIELDS = [
        'start_date',
        'end_date',
        'min_size',
        'max_size',
        'min_seeders',
        'max_seeders',
        'min_leechers',
        'max_leechers',
        'min_snatched',
        'max_snatched'
    ];

    const DEFAULT_RESULTS_SORT = 'quality_desc';
    let currentResultsSort = DEFAULT_RESULTS_SORT;
    const resultJsonCache = new WeakMap();

    function updateSortMenuUI() {
        if (resultsSortCurrent) {
            const activeOption = [...resultsSortOptions].find(option => option.dataset.sortMode === currentResultsSort);
            const activeLabel = activeOption?.dataset.sortLabel || 'Quality';
            resultsSortCurrent.textContent = `Sort: ${activeLabel}`;
        }

        resultsSortOptions.forEach(option => {
            const isActive = option.dataset.sortMode === currentResultsSort;
            option.classList.toggle('active', isActive);
            const checkIcon = option.querySelector('.sort-check-icon');
            if (checkIcon) {
                checkIcon.classList.toggle('d-none', !isActive);
            }
        });
    }

    function parseResultJson(resultItem) {
        if (!resultItem) return {};
        const cached = resultJsonCache.get(resultItem);
        if (cached) return cached;

        let parsed = {};
        const rawJson = resultItem.dataset.json;
        if (rawJson) {
            try {
                parsed = JSON.parse(rawJson);
            } catch (_) {
                parsed = {};
            }
        }
        resultJsonCache.set(resultItem, parsed);
        return parsed;
    }

    function parseSizeToBytes(sizeValue) {
        if (typeof sizeValue !== 'string') return 0;
        const match = sizeValue.trim().match(/^([\d.,]+)\s*([A-Za-z]+)$/);
        if (!match) return 0;

        const number = Number.parseFloat(match[1].replace(/,/g, ''));
        if (!Number.isFinite(number)) return 0;

        const unit = match[2].toUpperCase();
        const multipliers = {
            B: 1,
            KB: 1000,
            MB: 1000 ** 2,
            GB: 1000 ** 3,
            TB: 1000 ** 4,
            KIB: 1024,
            MIB: 1024 ** 2,
            GIB: 1024 ** 3,
            TIB: 1024 ** 4
        };
        return number * (multipliers[unit] || 1);
    }

    function parseSortableDate(dateValue) {
        if (!dateValue) return 0;
        const normalized = String(dateValue).trim().replace(' ', 'T');
        const timestamp = Date.parse(normalized);
        return Number.isFinite(timestamp) ? timestamp : 0;
    }

    function compareStrings(a, b) {
        return String(a || '').localeCompare(String(b || ''), undefined, {
            sensitivity: 'base',
            numeric: true
        });
    }

    function compareNumbers(a, b) {
        const left = Number(a);
        const right = Number(b);
        const safeLeft = Number.isFinite(left) ? left : 0;
        const safeRight = Number.isFinite(right) ? right : 0;
        return safeLeft - safeRight;
    }

    function compareResultItems(leftItem, rightItem, sortMode) {
        const left = parseResultJson(leftItem);
        const right = parseResultJson(rightItem);

        switch (sortMode) {
            case 'date_uploaded_desc': {
                const diff = compareNumbers(parseSortableDate(right.added), parseSortableDate(left.added));
                if (diff !== 0) return diff;
                return compareStrings(left.title, right.title);
            }
            case 'date_uploaded_asc': {
                const diff = compareNumbers(parseSortableDate(left.added), parseSortableDate(right.added));
                if (diff !== 0) return diff;
                return compareStrings(left.title, right.title);
            }
            case 'author_asc': {
                const diff = compareStrings(left.author_info, right.author_info);
                if (diff !== 0) return diff;
                return compareStrings(left.title, right.title);
            }
            case 'author_desc': {
                const diff = compareStrings(right.author_info, left.author_info);
                if (diff !== 0) return diff;
                return compareStrings(left.title, right.title);
            }
            case 'title_asc':
                return compareStrings(left.title, right.title);
            case 'title_desc':
                return compareStrings(right.title, left.title);
            case 'seeders_desc': {
                const diff = compareNumbers(right.seeders, left.seeders);
                if (diff !== 0) return diff;
                return compareStrings(left.title, right.title);
            }
            case 'seeders_asc': {
                const diff = compareNumbers(left.seeders, right.seeders);
                if (diff !== 0) return diff;
                return compareStrings(left.title, right.title);
            }
            case 'snatches_desc': {
                const diff = compareNumbers(right.times_completed, left.times_completed);
                if (diff !== 0) return diff;
                return compareStrings(left.title, right.title);
            }
            case 'snatches_asc': {
                const diff = compareNumbers(left.times_completed, right.times_completed);
                if (diff !== 0) return diff;
                return compareStrings(left.title, right.title);
            }
            case 'size_desc': {
                const diff = compareNumbers(parseSizeToBytes(right.size), parseSizeToBytes(left.size));
                if (diff !== 0) return diff;
                return compareStrings(left.title, right.title);
            }
            case 'size_asc': {
                const diff = compareNumbers(parseSizeToBytes(left.size), parseSizeToBytes(right.size));
                if (diff !== 0) return diff;
                return compareStrings(left.title, right.title);
            }
            case 'quality_desc':
            default: {
                const diff = compareNumbers(right.score, left.score);
                if (diff !== 0) return diff;
                return compareStrings(left.title, right.title);
            }
        }
    }

    function scrollToResultsWrapper() {
        if (!wrapper) return;
        const navbar = document.getElementById('main-navbar');
        const navbarHeight = navbar ? navbar.getBoundingClientRect().height : 0;
        const extraSpacing = 0;
        const top = window.scrollY + wrapper.getBoundingClientRect().top - navbarHeight - extraSpacing;

        window.scrollTo({
            top: Math.max(0, top),
            behavior: 'smooth'
        });
    }

    function applyCurrentResultsSort(container = resultsContainer) {
        if (!container) return;
        const items = [...container.querySelectorAll('.result-item')];
        if (items.length <= 1) return;

        items.sort((left, right) => compareResultItems(left, right, currentResultsSort));
        const fragment = document.createDocumentFragment();
        items.forEach(item => fragment.appendChild(item));
        container.appendChild(fragment);
    }

    function refreshAppLogoState() {
    let targetState = 'static';
    let targetMarkup = STATIC_LOGO_MARKUP;
    if (activeSearchRequests > 0) {
        targetState = 'animated';
        targetMarkup = ANIMATED_LOGO_MARKUP;
    } else if (highlightedLogoCount > 0) {
        targetState = 'tail';
        targetMarkup = TAIL_ANIMATED_LOGO_MARKUP;
    }

    appLogos.forEach(logo => {
        if (logo.dataset.logoState !== targetState) {
            logo.innerHTML = targetMarkup;
            logo.dataset.logoState = targetState;

            // --- The Safari "Kick in the Pants" Hack ---
            // 1. Hide the logo
            logo.style.display = 'none';
            // 2. Read a layout property to force the browser to immediately recalculate the DOM
            void logo.offsetWidth; 
            // 3. Show it again
            logo.style.display = '';
        }
    });
}

    appLogos.forEach(logo => {
        logo.addEventListener('mouseenter', () => {
            highlightedLogoCount += 1;
            refreshAppLogoState();
        });
        logo.addEventListener('mouseleave', () => {
            highlightedLogoCount = Math.max(0, highlightedLogoCount - 1);
            refreshAppLogoState();
        });
    });

    refreshAppLogoState();

    function uniqueStringValues(value) {
        if (Array.isArray(value)) {
            return [...new Set(value.map(item => String(item).trim()).filter(Boolean))];
        }
        if (typeof value === 'string') {
            const trimmed = value.trim();
            return trimmed ? [trimmed] : [];
        }
        return [];
    }

    function normalizeSearchFilterDefaults(rawDefaults) {
        const defaults = {
            searchType: 'all',
            search_scope: 'torrents',
            hide_downloaded: false,
            search_in_title: true,
            search_in_author: true,
            search_in_series: true,
            search_in_narrator: false,
            search_in_description: false,
            search_in_tags: false,
            search_in_filenames: false,
            language_ids: [DEFAULT_LANGUAGE_ID],
            main_cat: [],
            category_ids: [],
            flags_mode: '0',
            flag_ids: [],
            start_date: '',
            end_date: '',
            min_size: '',
            max_size: '',
            size_unit: '1048576',
            min_seeders: '',
            max_seeders: '',
            min_leechers: '',
            max_leechers: '',
            min_snatched: '',
            max_snatched: ''
        };
        if (!rawDefaults || typeof rawDefaults !== 'object') {
            return defaults;
        }

        const boolFields = [
            'hide_downloaded',
            'search_in_title',
            'search_in_author',
            'search_in_series',
            'search_in_narrator',
            'search_in_description',
            'search_in_tags',
            'search_in_filenames'
        ];
        boolFields.forEach(field => {
            if (typeof rawDefaults[field] === 'boolean') {
                defaults[field] = rawDefaults[field];
            }
        });

        const searchType = String(rawDefaults.searchType || '').trim();
        if (searchType) defaults.searchType = searchType;

        const searchScope = String(rawDefaults.search_scope || '').trim();
        if (searchScope) defaults.search_scope = searchScope;

        const flagsMode = String(rawDefaults.flags_mode || '').trim();
        if (flagsMode === '0' || flagsMode === '1') {
            defaults.flags_mode = flagsMode;
        }

        const languageIds = uniqueStringValues(rawDefaults.language_ids);
        defaults.language_ids = languageIds.length ? languageIds : [DEFAULT_LANGUAGE_ID];

        const mainCats = normalizeMainCatValues(uniqueStringValues(rawDefaults.main_cat));
        defaults.main_cat = mainCats.includes('all') ? ['all'] : mainCats;
        defaults.category_ids = uniqueStringValues(rawDefaults.category_ids);
        defaults.flag_ids = uniqueStringValues(rawDefaults.flag_ids);

        FILTER_TEXT_FIELDS.forEach(field => {
            if (rawDefaults[field] !== undefined && rawDefaults[field] !== null) {
                defaults[field] = String(rawDefaults[field]).trim();
            }
        });

        if (rawDefaults.size_unit !== undefined && rawDefaults.size_unit !== null) {
            const unit = String(rawDefaults.size_unit).trim();
            defaults.size_unit = unit || defaults.size_unit;
        }

        return defaults;
    }

    function applySearchFilterDefaults(rawDefaults) {
        const normalized = normalizeSearchFilterDefaults(rawDefaults);

        DEFAULT_SEARCH_TYPE = normalized.searchType;
        DEFAULT_SEARCH_SCOPE = normalized.search_scope;
        DEFAULT_HIDE_DOWNLOADED = normalized.hide_downloaded === true;
        DEFAULT_FLAGS_MODE = normalized.flags_mode;
        DEFAULT_SEARCH_FIELDS = {
            search_in_title: normalized.search_in_title,
            search_in_author: normalized.search_in_author,
            search_in_series: normalized.search_in_series,
            search_in_narrator: normalized.search_in_narrator,
            search_in_description: normalized.search_in_description,
            search_in_tags: normalized.search_in_tags,
            search_in_filenames: normalized.search_in_filenames
        };
        DEFAULT_LANGUAGE_VALUES = normalized.language_ids.length ? normalized.language_ids : [DEFAULT_LANGUAGE_ID];
        DEFAULT_LANGUAGE_SET = new Set(DEFAULT_LANGUAGE_VALUES);
        DEFAULT_MAIN_CATS = normalizeMainCatValues(normalized.main_cat);
        DEFAULT_CATEGORY_IDS = [...normalized.category_ids];
        DEFAULT_FLAG_IDS = [...normalized.flag_ids];
        DEFAULT_RANGE_FILTERS = {
            start_date: normalized.start_date,
            end_date: normalized.end_date,
            min_size: normalized.min_size,
            max_size: normalized.max_size,
            size_unit: normalized.size_unit,
            min_seeders: normalized.min_seeders,
            max_seeders: normalized.max_seeders,
            min_leechers: normalized.min_leechers,
            max_leechers: normalized.max_leechers,
            min_snatched: normalized.min_snatched,
            max_snatched: normalized.max_snatched
        };
        return normalized;
    }

    applySearchFilterDefaults(window.DEFAULT_SEARCH_FILTERS || {});

    const setsEqual = (a, b) => {
        if (a.size !== b.size) return false;
        for (const val of a) {
            if (!b.has(val)) return false;
        }
        return true;
    };

    function updateMirroredCheckboxes() {
        const mirrors = document.querySelectorAll('[data-sync-target]');
        mirrors.forEach(mirror => {
            const targetId = mirror.dataset.syncTarget;
            const target = document.getElementById(targetId);
            if (!target) return;
            mirror.checked = target.checked;
        });
    }

    function setupSyncedCheckboxes() {
        const mirrors = document.querySelectorAll('[data-sync-target]');
        mirrors.forEach(mirror => {
            const targetId = mirror.dataset.syncTarget;
            const target = document.getElementById(targetId);
            if (!target) return;

            mirror.checked = target.checked;

            mirror.addEventListener('change', () => {
                target.checked = mirror.checked;
                target.dispatchEvent(new Event('change', { bubbles: true }));
            });

            target.addEventListener('change', () => {
                mirror.checked = target.checked;
            });
        });
    }

    const getSelectedMainCats = () => {
        if (mainCatPrimaryTomSelect) {
            return normalizeMainCatValues(getTomSelectValues(mainCatPrimaryTomSelect));
        }
        if (mainCatFilterTomSelect) {
            return normalizeMainCatValues(getTomSelectValues(mainCatFilterTomSelect));
        }
        const selectEl = document.getElementById('main_cat');
        if (!selectEl) return [];
        return normalizeMainCatValues([...selectEl.selectedOptions].map(opt => opt.value));
    };

    function applyCategoryMainCatFilter() {
        if (!catTomSelect || !categoryMainCatMap.size) return;
        const selectedMainCats = getSelectedMainCats();
        const allowAll = !selectedMainCats.length || selectedMainCats.includes('all');
        const allowedSet = allowAll ? null : new Set(selectedMainCats);
        categoryAllowedMainCats = allowedSet;

        if (!allowAll) {
            const selectedCats = getTomSelectValues(catTomSelect);
            selectedCats.forEach(catId => {
                const mainCat = categoryMainCatMap.get(catId);
                if (mainCat && !allowedSet.has(mainCat)) {
                    catTomSelect.removeItem(catId, true);
                }
            });
        }

        catTomSelect.refreshOptions(false);
        decorateCategoryOptions();
    }

    function handleMainCatSelectChange(source) {
        if (mainCatSelectSyncing) return;
        mainCatSelectSyncing = true;

        const rawValues = source ? getTomSelectValues(source) : [];
        let normalized = normalizeMainCatValues(rawValues);
        if (normalized.includes('all') && normalized.length > 1) {
            const lastAdded = source?._lastAddedMainCat;
            if (lastAdded === 'all') {
                normalized = ['all'];
            } else {
                normalized = normalized.filter(val => val !== 'all');
            }
        }
        if (source) {
            source._lastAddedMainCat = null;
        }

        if (source) {
            const rawSet = new Set(rawValues.map(String));
            const normalizedSet = new Set(normalized);
            const needsUpdate = rawSet.size !== normalizedSet.size || [...normalizedSet].some(val => !rawSet.has(val));
            if (needsUpdate) {
                source.setValue(normalized, true);
            }
        }

        if (mainCatPrimaryTomSelect && source !== mainCatPrimaryTomSelect) {
            mainCatPrimaryTomSelect.setValue(normalized, true);
        }
        if (mainCatFilterTomSelect && source !== mainCatFilterTomSelect) {
            mainCatFilterTomSelect.setValue(normalized, true);
        }

        mainCatSelectSyncing = false;
        applyCategoryMainCatFilter();
        updateFilterBadge();
    }

    function handleMainCatItemAdd(source, value) {
        if (!source) return;
        const addedValue = String(value);
        source._lastAddedMainCat = addedValue;
        handleMainCatSelectChange(source);
    }

    function setMainCatSelection(values, silent = true) {
        const normalized = normalizeMainCatValues(values);
        const finalValues = normalized;

        if (mainCatPrimaryTomSelect) {
            mainCatPrimaryTomSelect.setValue(finalValues, silent);
        }
        if (mainCatFilterTomSelect) {
            mainCatFilterTomSelect.setValue(finalValues, silent);
        }
        applyCategoryMainCatFilter();
        updateFilterBadge();
        return finalValues;
    }

    function ensureMainCatsForSelectedSubcategories(selectedSubcategories = null) {
        if (!categoryMainCatMap.size) return false;

        const selectedCats = (selectedSubcategories || (catTomSelect ? getTomSelectValues(catTomSelect) : []))
            .map(String)
            .filter(Boolean);
        if (!selectedCats.length) return false;

        const selectedMainCats = new Set(getSelectedMainCats().map(String));
        if (selectedMainCats.has('all')) return false;

        let changed = false;
        selectedCats.forEach(catId => {
            const requiredMainCat = categoryMainCatMap.get(catId);
            if (requiredMainCat && !selectedMainCats.has(requiredMainCat)) {
                selectedMainCats.add(requiredMainCat);
                changed = true;
            }
        });

        if (changed) {
            setMainCatSelection([...selectedMainCats], true);
        }

        return changed;
    }

    async function initTomSelects() {
        const langSelect = document.getElementById('langSelect');
        if (langSelect) {
            langTomSelect = new TomSelect(langSelect, {
                plugins: ['remove_button', 'checkbox_options'],
                create: false,
                maxItems: null,
                maxOptions: 1000,
                hidePlaceholder: true
            });

            if (!getTomSelectValues(langTomSelect).length && DEFAULT_LANGUAGE_VALUES.length) {
                langTomSelect.setValue(DEFAULT_LANGUAGE_VALUES, true);
            }

            langTomSelect.on('change', () => {
                if (!getTomSelectValues(langTomSelect).length && DEFAULT_LANGUAGE_VALUES.length) {
                    langTomSelect.setValue(DEFAULT_LANGUAGE_VALUES, true);
                }
                updateFilterBadge();
            });
        }

        const mainCatSelectPrimary = document.getElementById('main_cat');
        if (mainCatSelectPrimary) {
            mainCatPrimaryTomSelect = new TomSelect(mainCatSelectPrimary, {
                plugins: ['remove_button', 'checkbox_options', 'clear_button'],
                create: false,
                maxItems: null
            });
            mainCatPrimaryTomSelect.on('change', () => handleMainCatSelectChange(mainCatPrimaryTomSelect));
            mainCatPrimaryTomSelect.on('item_add', (value) => handleMainCatItemAdd(mainCatPrimaryTomSelect, value));
        }

        const mainCatSelect = document.getElementById('mainCatSelect');
        if (mainCatSelect) {
            mainCatFilterTomSelect = new TomSelect(mainCatSelect, {
                plugins: ['remove_button', 'checkbox_options', 'clear_button'],
                create: false,
                maxItems: null
            });
            mainCatFilterTomSelect.on('change', () => handleMainCatSelectChange(mainCatFilterTomSelect));
            mainCatFilterTomSelect.on('item_add', (value) => handleMainCatItemAdd(mainCatFilterTomSelect, value));
        }

        if (mainCatPrimaryTomSelect || mainCatFilterTomSelect) {
            const initialValues = mainCatPrimaryTomSelect
                ? getTomSelectValues(mainCatPrimaryTomSelect)
                : getTomSelectValues(mainCatFilterTomSelect);
            setMainCatSelection(initialValues, true);
        }

        const legacyDefinitions = await loadLegacyCategoryDefinitions();
        const catSelect = document.getElementById('catSelect');
        if (catSelect && legacyDefinitions?.categories?.length) {
            categoryMainCatMap = new Map();
            catSelect.innerHTML = '';

            legacyDefinitions.categories.forEach(mainCat => {
                const group = document.createElement('optgroup');
                group.label = mainCat.name;

                (mainCat.subcategories || []).forEach(subcat => {
                    const option = document.createElement('option');
                    option.value = String(subcat.category);
                    option.textContent = subcat.name;
                    group.appendChild(option);
                    categoryMainCatMap.set(String(subcat.category), String(mainCat.main_cat));
                });

                catSelect.appendChild(group);
            });

            catTomSelect = new TomSelect(catSelect, {
                plugins: ['remove_button', 'checkbox_options', 'clear_button'],
                create: false,
                maxItems: null,
                maxOptions: 1000,
                hidePlaceholder: true
            });

            Object.entries(catTomSelect.options).forEach(([value, option]) => {
                option.mainCat = categoryMainCatMap.get(value);
            });

            catTomSelect.on('change', updateFilterBadge);
            catTomSelect.on('dropdown_open', decorateCategoryOptions);
            catTomSelect.on('type', decorateCategoryOptions);
            catTomSelect.on('item_add', (value) => {
                const mainCat = categoryMainCatMap.get(String(value));
                if (!isMainCatAllowed(mainCat)) {
                    ensureMainCatsForSelectedSubcategories([String(value)]);
                }
            });
            applyCategoryMainCatFilter();
        }
    }

    function updateFilterBadge() {
        const setSectionBadge = (badgeEl, count) => {
            if (!badgeEl) return;
            badgeEl.textContent = String(count);
            badgeEl.style.display = count ? 'inline-block' : 'none';
        };

        const searchType = document.querySelector('input[name="searchType"]:checked')?.value || DEFAULT_SEARCH_TYPE;
        const searchScope = document.querySelector('input[name="search_scope"]:checked')?.value || DEFAULT_SEARCH_SCOPE;
        const hideDownloaded = !!document.getElementById('hide_downloaded')?.checked;
        const statusCount =
            (searchType !== DEFAULT_SEARCH_TYPE ? 1 : 0)
            + (searchScope !== DEFAULT_SEARCH_SCOPE ? 1 : 0)
            + (hideDownloaded !== DEFAULT_HIDE_DOWNLOADED ? 1 : 0);

        let fieldCount = 0;
        Object.entries(DEFAULT_SEARCH_FIELDS).forEach(([id, defVal]) => {
            const el = document.getElementById(id);
            if (el && el.checked !== defVal) {
                fieldCount++;
            }
        });

        let languageCount = 0;
        if (langTomSelect) {
            const selectedValues = getTomSelectValues(langTomSelect).map(String).filter(Boolean);
            const selectedSet = new Set(selectedValues);
            if (!setsEqual(selectedSet, DEFAULT_LANGUAGE_SET)) {
                languageCount = selectedValues.length || 1;
            }
        }

        let categoriesCount = 0;
        const selectedMainCatValues = getSelectedMainCats();
        const selectedMainCats = new Set(selectedMainCatValues);
        const defaultMainCats = new Set(DEFAULT_MAIN_CATS);
        if (!setsEqual(selectedMainCats, defaultMainCats)) {
            categoriesCount += selectedMainCats.has('all') ? 0 : selectedMainCats.size;
        }

        const selectedSubcategoryValues = catTomSelect ? getTomSelectValues(catTomSelect).map(String).filter(Boolean) : [];
        const selectedSubcategorySet = new Set(selectedSubcategoryValues);
        const defaultSubcategorySet = new Set(DEFAULT_CATEGORY_IDS);
        if (!setsEqual(selectedSubcategorySet, defaultSubcategorySet)) {
            categoriesCount += selectedSubcategoryValues.length;
        }

        const selectedFlags = [...document.querySelectorAll('.flag-checkbox:checked')].map(cb => String(cb.value));
        const selectedFlagSet = new Set(selectedFlags);
        const defaultFlagSet = new Set(DEFAULT_FLAG_IDS);
        const selectedFlagsCount = selectedFlags.length;
        const flagsMode = document.querySelector('input[name="flags_mode"]:checked')?.value || '0';
        let flagsCount = 0;
        if (!setsEqual(selectedFlagSet, defaultFlagSet)) {
            flagsCount += selectedFlagsCount;
        }
        if (flagsMode !== DEFAULT_FLAGS_MODE) {
            flagsCount++;
        }

        const getValue = (selector) => document.querySelector(selector)?.value?.trim();
        let rangesCount = FILTER_TEXT_FIELDS.reduce((sum, name) => {
            const currentValue = getValue(`input[name="${name}"]`) || '';
            const defaultValue = DEFAULT_RANGE_FILTERS[name] || '';
            return currentValue !== defaultValue ? sum + 1 : sum;
        }, 0);
        const sizeUnitValue = document.querySelector('select[name="size_unit"]')?.value || DEFAULT_RANGE_FILTERS.size_unit;
        if (sizeUnitValue !== DEFAULT_RANGE_FILTERS.size_unit) {
            rangesCount++;
        }

        const count = statusCount + fieldCount + languageCount + categoriesCount + flagsCount + rangesCount;

        setSectionBadge(sectionFilterBadges.status, statusCount);
        setSectionBadge(sectionFilterBadges.fields, fieldCount);
        setSectionBadge(sectionFilterBadges.language, languageCount);
        setSectionBadge(sectionFilterBadges.categories, categoriesCount);
        setSectionBadge(sectionFilterBadges.flags, flagsCount);
        setSectionBadge(sectionFilterBadges.ranges, rangesCount);

        if (filterBadge) {
            filterBadge.textContent = count;
            filterBadge.style.display = count ? 'inline-block' : 'none';
        }
    }

    function collectCurrentSearchFilterDefaults() {
        const defaults = {
            searchType: document.querySelector('input[name="searchType"]:checked')?.value || DEFAULT_SEARCH_TYPE,
            search_scope: document.querySelector('input[name="search_scope"]:checked')?.value || DEFAULT_SEARCH_SCOPE,
            hide_downloaded: !!document.getElementById('hide_downloaded')?.checked,
            flags_mode: document.querySelector('input[name="flags_mode"]:checked')?.value || DEFAULT_FLAGS_MODE,
            language_ids: langTomSelect ? getTomSelectValues(langTomSelect).map(String).filter(Boolean) : [],
            main_cat: getSelectedMainCats().map(String),
            category_ids: catTomSelect ? getTomSelectValues(catTomSelect).map(String).filter(Boolean) : [],
            flag_ids: [...document.querySelectorAll('.flag-checkbox:checked')].map(cb => String(cb.value)),
            size_unit: document.querySelector('select[name="size_unit"]')?.value || DEFAULT_RANGE_FILTERS.size_unit
        };

        Object.keys(DEFAULT_SEARCH_FIELDS).forEach(field => {
            const el = document.getElementById(field);
            defaults[field] = !!el?.checked;
        });

        FILTER_TEXT_FIELDS.forEach(field => {
            defaults[field] = document.querySelector(`[name="${field}"]`)?.value?.trim() || '';
        });

        return defaults;
    }

    if (searchForm) {
        setupSyncedCheckboxes();
        try {
            await initTomSelects();
        } catch (err) {
            console.error('Tom Select initialization failed', err);
        }
        updateFilterBadge();

        searchForm.addEventListener('change', updateFilterBadge);
        searchForm.addEventListener('input', updateFilterBadge);

        if (advancedOffcanvasEl) {
            advancedOffcanvasEl.querySelectorAll('input, select, textarea').forEach(el => {
                el.addEventListener('change', updateFilterBadge);
                el.addEventListener('input', updateFilterBadge);
            });
        }

        searchForm.addEventListener('reset', () => {
            setTimeout(() => {
                restoreFormFromURL(new URLSearchParams());
            }, 0);
        });
    }

    document.getElementById('save-default-filters-button')?.addEventListener('click', async function () {
        const button = this;
        const originalHtml = button.innerHTML;
        button.disabled = true;
        button.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Saving...';

        try {
            const payload = { filters: collectCurrentSearchFilterDefaults() };
            const response = await fetch('/update_default_search_filters', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await response.json();
            if (!response.ok || data.status !== 'success') {
                throw new Error(data.message || 'Failed to save default filters.');
            }

            applySearchFilterDefaults(data.filters || payload.filters);
            updateFilterBadge();
            showToast(data.message || 'Default filters saved.', 'success');
        } catch (error) {
            showToast(error.message || 'Error saving default filters.', 'danger');
        } finally {
            button.disabled = false;
            button.innerHTML = originalHtml;
        }
    });

    const allowedResultFields = new Set([
        'date_uploaded',
        'file_type',
        'file_size',
        'snatches',
        'seeders',
        'category',
        'language',
        'narrator',
        'series'
    ]);

    function getSelectedResultFields() {
        return [...resultDisplayOptions]
            .filter(option => option.checked && allowedResultFields.has(option.value))
            .map(option => option.value);
    }

    if (resultsSortOptions.length) {
        const initiallyActive = [...resultsSortOptions].find(option => option.classList.contains('active'));
        currentResultsSort = initiallyActive?.dataset.sortMode || DEFAULT_RESULTS_SORT;
        updateSortMenuUI();

        resultsSortOptions.forEach(option => {
            option.addEventListener('click', () => {
                currentResultsSort = option.dataset.sortMode || DEFAULT_RESULTS_SORT;
                updateSortMenuUI();
            applyCurrentResultsSort(resultsContainer);
            applyHideDownloadedResultsFilter();
            });
        });
    }

    function applyResultDisplayFields(fields, scope = document) {
        const active = new Set(fields);
        scope.querySelectorAll('[data-result-field]').forEach(el => {
            const field = el.dataset.resultField;
            el.classList.toggle('d-none', !active.has(field));
        });
    }

    function saveResultDisplayFields(fields) {
        return fetch('/update_result_display_fields', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ fields })
        })
            .then(response => response.json())
            .then(data => {
                if (data.status !== 'success') {
                    throw new Error(data.message || 'Failed to save display settings.');
                }
                return data.fields;
            });
    }

    const initialResultFields = Array.isArray(window.resultDisplayFields)
        ? window.resultDisplayFields.filter(field => allowedResultFields.has(field))
        : getSelectedResultFields();
    window.resultDisplayFields = initialResultFields;

    if (resultDisplayOptions.length) {
        resultDisplayOptions.forEach(option => {
            option.addEventListener('change', () => {
                const fields = getSelectedResultFields();
                window.resultDisplayFields = fields;
                applyResultDisplayFields(fields, resultsContainer || document);
                saveResultDisplayFields(fields).catch(() => {
                    showToast('Failed to save result display settings.', 'danger');
                });
            });
        });
    }

    // Download Confirmation & Modal Variables
    let pendingDownloadData = null;
    let pendingButton = null;
    const confirmModalEl = document.getElementById('downloadConfirmModal');
    const confirmModal = confirmModalEl ? new bootstrap.Modal(confirmModalEl) : null;
    const confirmInput = document.getElementById('confirm-path-input');
    const previewSpan = document.getElementById('full-path-preview');
    const confirmDestinationSelect = document.getElementById('confirm-destination-select');
    const confirmDestinationRoot = document.getElementById('confirm-destination-root');
    const defaultOrganizedPathInput = document.getElementById('ORGANIZED_PATH');
    const autoOrganizeSection = document.getElementById('auto-organize-section');
    const confirmDownloadOnly = document.getElementById('confirm-download-only');

    const personalFlBtn = document.getElementById('use-personal-fl-btn');
    const freeleechIndicator = document.getElementById('confirm-freeleech-indicator');

    const destinationPathsList = document.getElementById('destination-paths-list');
    const addDestinationPathBtn = document.getElementById('add-destination-path-btn');
    const mediaTypeOptions = Array.isArray(window.AUTO_ORGANIZE_MEDIA_TYPES) && window.AUTO_ORGANIZE_MEDIA_TYPES.length
        ? window.AUTO_ORGANIZE_MEDIA_TYPES.map(item => ({ id: String(item.id), label: String(item.label) }))
        : [
            { id: '13', label: 'Audiobooks' },
            { id: '14', label: 'E-Books' },
            { id: '15', label: 'Musicology' },
            { id: '16', label: 'Radio' },
        ];
    const allowedDestinationDefaults = new Set(mediaTypeOptions.map(item => item.id));
    let destinationPathEntries = [];

    function normalizeDestinationEntries(entries, fallbackPath = '/downloads/organized', requireFallback = true) {
        const normalized = [];
        const seenDefaults = new Set();

        (Array.isArray(entries) ? entries : []).forEach(entry => {
            const path = String(entry?.path || '').trim();
            if (!path) return;

            let defaultMainCat = String(entry?.default_main_cat || '').trim();
            if (!allowedDestinationDefaults.has(defaultMainCat)) {
                defaultMainCat = '';
            }
            if (defaultMainCat && seenDefaults.has(defaultMainCat)) {
                defaultMainCat = '';
            }
            if (defaultMainCat) {
                seenDefaults.add(defaultMainCat);
            }

            normalized.push({ path, default_main_cat: defaultMainCat });
        });

        if (requireFallback && !normalized.length) {
            normalized.push({ path: String(fallbackPath || '/downloads/organized').trim() || '/downloads/organized', default_main_cat: '' });
        }

        return normalized;
    }

    function getDefaultDestinationPath() {
        const configured = String(defaultOrganizedPathInput?.value || '').trim();
        if (configured) return configured;
        const fromWindow = String(window.DESTINATION_PATHS?.[0]?.path || '').trim();
        return fromWindow || '/downloads/organized';
    }

    function buildConfiguredDestinationEntries() {
        const defaultPath = getDefaultDestinationPath();
        const extras = normalizeDestinationEntries(readDestinationRows(), defaultPath, false)
            .filter(entry => entry.path !== defaultPath);
        return [{ path: defaultPath, default_main_cat: '' }, ...extras];
    }

    function readDestinationRows() {
        if (!destinationPathsList) return [];
        return Array.from(destinationPathsList.querySelectorAll('.destination-path-row')).map(row => ({
            path: row.querySelector('.destination-path-input')?.value || '',
            default_main_cat: row.querySelector('.destination-default-select')?.value || ''
        }));
    }

    function buildDestinationRow(path = '', defaultMainCat = '') {
        const row = document.createElement('div');
        row.className = 'destination-path-row d-flex gap-2 mb-2 align-items-center';

        const pathWrap = document.createElement('div');
        pathWrap.className = 'form-floating flex-grow-1';
        const pathInput = document.createElement('input');
        pathInput.type = 'text';
        pathInput.className = 'form-control destination-path-input';
        pathInput.name = 'extra_dest_paths[]';
        pathInput.placeholder = '/data/media';
        pathInput.required = true;
        pathInput.value = path;
        const pathLabel = document.createElement('label');
        pathLabel.textContent = 'Path';
        pathWrap.append(pathInput, pathLabel);

        const defaultWrap = document.createElement('div');
        defaultWrap.className = 'form-floating';
        defaultWrap.style.minWidth = '140px';
        defaultWrap.style.maxWidth = '180px';
        const defaultSelect = document.createElement('select');
        defaultSelect.className = 'form-select destination-default-select';
        defaultSelect.name = 'extra_dest_defaults[]';

        const noneOption = document.createElement('option');
        noneOption.value = '';
        noneOption.textContent = 'None (Global)';
        defaultSelect.appendChild(noneOption);

        mediaTypeOptions.forEach(item => {
            const option = document.createElement('option');
            option.value = item.id;
            option.textContent = item.label;
            if (item.id === defaultMainCat) {
                option.selected = true;
            }
            defaultSelect.appendChild(option);
        });

        const defaultLabel = document.createElement('label');
        defaultLabel.textContent = 'Default For';
        defaultWrap.append(defaultSelect, defaultLabel);

        const removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.className = 'btn btn-outline-danger remove-path-btn';
        removeBtn.title = 'Remove Path';
        removeBtn.style.height = '58px';
        removeBtn.innerHTML = '<i class="bi bi-trash"></i>';

        row.append(pathWrap, defaultWrap, removeBtn);
        return row;
    }

    function updateDestinationRowUI() {
        if (!destinationPathsList) return;

        const rows = Array.from(destinationPathsList.querySelectorAll('.destination-path-row'));
        const selectedDefaults = rows.map(row => row.querySelector('.destination-default-select')?.value || '');

        rows.forEach((row, index) => {
            const select = row.querySelector('.destination-default-select');
            if (!select) return;
            const ownValue = selectedDefaults[index];
            Array.from(select.options).forEach(option => {
                if (!option.value) {
                    option.disabled = false;
                    return;
                }
                option.disabled = selectedDefaults.includes(option.value) && ownValue !== option.value;
            });
        });

        rows.forEach(row => {
            const removeBtn = row.querySelector('.remove-path-btn');
            if (removeBtn) {
                removeBtn.disabled = false;
            }
        });
    }

    function syncConfirmDestinationOptions(mainCat = '', preferCategoryDefault = false) {
        if (!confirmDestinationSelect) return;

        const liveEntries = buildConfiguredDestinationEntries();
        destinationPathEntries = liveEntries;

        const existingValue = confirmDestinationSelect.value;
        confirmDestinationSelect.innerHTML = '';

        liveEntries.forEach(entry => {
            const option = document.createElement('option');
            option.value = entry.path;
            option.textContent = entry.path;
            confirmDestinationSelect.appendChild(option);
        });

        const mappedDefault = liveEntries.find(entry => entry.default_main_cat === String(mainCat || ''));
        const fallbackDefault = liveEntries[0];
        const preferredValue = (mappedDefault || fallbackDefault)?.path || '';
        const hasExisting = liveEntries.some(entry => entry.path === existingValue);

        if (preferCategoryDefault) {
            confirmDestinationSelect.value = preferredValue;
        } else if (hasExisting) {
            confirmDestinationSelect.value = existingValue;
        } else {
            confirmDestinationSelect.value = preferredValue;
        }
    }

    function renderDestinationRows(entries) {
        if (!destinationPathsList) return;
        const fallbackPath = getDefaultDestinationPath();
        const normalized = normalizeDestinationEntries(entries, fallbackPath, false)
            .filter(entry => entry.path !== fallbackPath);
        destinationPathEntries = normalized;

        destinationPathsList.innerHTML = '';
        normalized.forEach(entry => {
            destinationPathsList.appendChild(buildDestinationRow(entry.path, entry.default_main_cat));
        });

        updateDestinationRowUI();
        syncConfirmDestinationOptions();
    }

    function updateConfirmPathPreview() {
        if (previewSpan && confirmInput) {
            previewSpan.textContent = confirmInput.value || '';
        }
        if (confirmDestinationRoot) {
            const root = String(confirmDestinationSelect?.value || '').trim();
            confirmDestinationRoot.textContent = root ? `${root}/` : '';
        }
    }

    if (destinationPathsList) {
        const initialRows = readDestinationRows();
        if (initialRows.length) {
            renderDestinationRows(initialRows);
        } else {
            renderDestinationRows((window.DESTINATION_PATHS || []).slice(1));
        }

        addDestinationPathBtn?.addEventListener('click', () => {
            destinationPathsList.appendChild(buildDestinationRow('', ''));
            updateDestinationRowUI();
            syncConfirmDestinationOptions();
            updateConfirmPathPreview();
            if (!isRestoringSettings) settingsDirty = true;
        });

        destinationPathsList.addEventListener('click', (event) => {
            const removeBtn = event.target.closest('.remove-path-btn');
            if (!removeBtn) return;

            const row = removeBtn.closest('.destination-path-row');
            if (!row) return;

            row.remove();
            updateDestinationRowUI();
            syncConfirmDestinationOptions();
            updateConfirmPathPreview();
            if (!isRestoringSettings) settingsDirty = true;
        });

        destinationPathsList.addEventListener('change', (event) => {
            const target = event.target;
            if (!(target instanceof HTMLElement)) return;
            if (target.classList.contains('destination-default-select')) {
                updateDestinationRowUI();
            }
            syncConfirmDestinationOptions();
            updateConfirmPathPreview();
            if (!isRestoringSettings) settingsDirty = true;
        });

        destinationPathsList.addEventListener('input', (event) => {
            const target = event.target;
            if (!(target instanceof HTMLElement)) return;
            if (target.classList.contains('destination-path-input')) {
                syncConfirmDestinationOptions();
                updateConfirmPathPreview();
                if (!isRestoringSettings) settingsDirty = true;
            }
        });
    }

    defaultOrganizedPathInput?.addEventListener('input', () => {
        syncConfirmDestinationOptions();
        updateConfirmPathPreview();
    });

    if (confirmDestinationSelect) {
        confirmDestinationSelect.addEventListener('change', updateConfirmPathPreview);
    }

    updateConfirmPathPreview();

    // Used to prevent repeated MID resolve/info calls while the confirm modal is open.
    // mid(string) -> { inClient: boolean, hash?: string, progress?: number, state?: string, isComplete?: boolean, isStarted?: boolean }
    const torrentClientStatusByMid = new Map();
    const torrentClientCheckInFlight = new Set();

    function classifyClientTorrentInfo(info) {
        const state = String(info?.state || 'unknown');
        const progress = Number(info?.progress ?? 0);

        // These align with the normalized states emitted by our client adapters.
        const seedingStates = ['uploading', 'stalledUP', 'checkingUP', 'forcedUP', 'pausedUP', 'queuedUP'];
        const downloadingStates = ['downloading', 'metaDL', 'stalledDL', 'checkingDL', 'forcedDL', 'allocating', 'moving', 'checkingResumeData', 'queuedDL', 'pausedDL'];

        const isComplete = progress >= 1 || seedingStates.includes(state);
        const isStarted = isComplete || progress > 0 || downloadingStates.includes(state);

        return { state, progress, isComplete, isStarted };
    }

    async function primeTorrentClientStatusForMid(mid) {
        if (!mid) return;
        if (torrentClientStatusByMid.has(mid)) return;
        if (torrentClientCheckInFlight.has(mid)) return;

        torrentClientCheckInFlight.add(mid);
        try {
            const hash = await getTorrentHashByMID(mid);
            if (!hash) {
                torrentClientStatusByMid.set(mid, { inClient: false });
                return;
            }

            let info = null;
            try {
                const response = await fetch(`/client/info/${hash}`, { cache: 'no-store' });
                if (response.ok) info = await response.json();
            } catch (_) {
                // If info fails, still treat as in-client; we just won't know complete vs downloading.
            }

            const classified = classifyClientTorrentInfo(info || {});
            torrentClientStatusByMid.set(mid, { inClient: true, hash, ...classified });
        } finally {
            torrentClientCheckInFlight.delete(mid);
            updateConfirmModalFreeleechUI();
        }
    }

    function markTorrentPersonalFreeleech(torrentId) {
        try {
            if (!torrentId) return;
            const tid = String(torrentId);
            window.appliedPersonalFreeleechIds.add(tid);

            // 1) Update search result row (dataset + badge) if present
            // Torrent IDs are numeric, so no special CSS escaping should be needed.
            const resultItem = document.querySelector(`.result-item[data-torrent-id="${tid}"]`);
            if (resultItem) {
                // Update embedded JSON used by row-click modal open
                try {
                    const rawJson = resultItem.dataset.json;
                    if (rawJson) {
                        const obj = JSON.parse(rawJson);
                        obj.personal_freeleech = 1;
                        resultItem.dataset.json = JSON.stringify(obj);
                    }
                } catch (e) {
                    // Ignore parse errors; UI still updates via set + badges below
                }

                // Update download button dataset so confirm modal reopen reflects freeleech
                const dlBtn = resultItem.querySelector('.add-to-client-button');
                if (dlBtn) {
                    dlBtn.dataset.personalFreeleech = '1';
                }

                // Ensure a Freeleech badge exists and reflects Personal Freeleech
                const containers = resultItem.querySelectorAll('.badge-container');
                containers.forEach(container => {
                    const isAbbrev = container.classList.contains('d-sm-none');
                    const label = isAbbrev ? 'FL' : 'Freeleech';
                    let badge = Array.from(container.querySelectorAll('span.badge')).find(s => (s.textContent || '').trim() === label);

                    if (!badge) {
                        badge = document.createElement('span');
                        badge.className = 'badge bg-info text-dark';
                        badge.style.fontSize = '0.6rem';
                        badge.textContent = label;
                        container.appendChild(badge);
                    }

                    badge.setAttribute('data-bs-toggle', 'tooltip');
                    badge.setAttribute('data-bs-placement', 'left');
                    badge.setAttribute('title', 'Personal Freeleech');
                    badge.setAttribute('data-bs-original-title', 'Personal Freeleech');
                    refreshTooltip(badge);
                });
            }

            // 2) Update book details modal badge if currently showing this torrent
            const bookModalEl = document.getElementById('bookDetailsModal');
            const detailBtn = document.getElementById('detail-download-btn');
            if (bookModalEl && bookModalEl.classList.contains('show') && detailBtn && String(detailBtn.dataset.id) === tid) {
                const bFree = document.getElementById('badge-freeleech');
                if (bFree) {
                    bFree.classList.remove('d-none');
                    bFree.setAttribute('title', 'Personal Freeleech');
                    bFree.setAttribute('data-bs-original-title', 'Personal Freeleech');
                    const existing = bootstrap.Tooltip.getInstance(bFree);
                    if (existing) existing.dispose();
                    new bootstrap.Tooltip(bFree);
                }
            }
        } catch (e) {
            console.error('markTorrentPersonalFreeleech failed', e);
        }
    }

    function refreshTooltip(el) {
        if (!el) return;
        const existing = bootstrap.Tooltip.getInstance(el);
        if (existing) existing.dispose();
        new bootstrap.Tooltip(el);
    }

    function computeTorrentFreeleechState(data) {
        const free = parseInt(data?.free ?? 0) === 1;
        const forcedPersonal = data?.id && window.appliedPersonalFreeleechIds?.has(String(data.id));
        const personal = forcedPersonal || parseInt(data?.personal_freeleech ?? 0) === 1;
        const flVip = parseInt(data?.fl_vip ?? 0) === 1;
        const vipFree = flVip && window.isVipActive === true;

        if (free) return { isFreeleech: true, reason: 'Public Freeleech' };
        if (personal) return { isFreeleech: true, reason: 'Personal Freeleech' };
        if (vipFree) return { isFreeleech: true, reason: 'VIP Freeleech' };
        return { isFreeleech: false, reason: null };
    }

    function updateConfirmModalFreeleechUI() {
        if (!confirmModalEl) return;

        const state = computeTorrentFreeleechState(pendingDownloadData);
        const downloadBtn = document.getElementById('confirm-download-btn');

        // 1. Handle Start Download Button Tooltip
        if (downloadBtn) {
            if (state.isFreeleech) {
                // Set the tooltip text and refresh
                downloadBtn.setAttribute('title', 'This download will be Freeleech');
                // We ensure the data-bs-toggle is present
                downloadBtn.setAttribute('data-bs-toggle', 'tooltip');
                refreshTooltip(downloadBtn);
            } else {
                // Remove tooltip entirely if not freeleech
                const existing = bootstrap.Tooltip.getInstance(downloadBtn);
                if (existing) {
                    existing.dispose();
                }
                downloadBtn.removeAttribute('title');
                downloadBtn.removeAttribute('data-bs-toggle');
            }
        }

        // 2. Handle Personal FL Button (Wedge)
        if (personalFlBtn) {
            const personalFlTooltipWrapper = document.getElementById('use-personal-fl-tooltip-wrapper');
            const tooltipTarget = personalFlTooltipWrapper || personalFlBtn;
            const mid = pendingDownloadData?.id ? String(pendingDownloadData.id) : null;
            const hasTorrentId = !!mid;

            // Disable reasons (in priority order)
            let disabledReason = null;

            if (!hasTorrentId) {
                disabledReason = 'Select a torrent first';
            } else if (state.isFreeleech) {
                disabledReason = state.reason
                    ? `This torrent is already Freeleech (${state.reason})`
                    : 'This torrent is already Freeleech';
            } else if (parseInt(pendingDownloadData?.my_snatched ?? 0) === 1) {
                disabledReason = 'You have already downloaded this torrent';
            } else if (torrentClientStatusByMid.get(mid)?.inClient === true) {
                const status = torrentClientStatusByMid.get(mid);
                if (status?.isComplete) {
                    disabledReason = 'You have already snatched this torrent.';
                } else {
                    disabledReason = 'This torrent is already in your torrent client';
                }
            } else if (torrentClientStatusByMid.get(mid)?.inClient === false) {
                disabledReason = null;
            } else {
                // Unknown yet: kick off a resolve/info check once, and disable while checking.
                primeTorrentClientStatusForMid(mid);
                disabledReason = 'Checking torrent status…';
            }

            personalFlBtn.disabled = !!disabledReason;
            const tooltip = disabledReason || 'Spend one Freeleech Wedge on this torrent';
            tooltipTarget.setAttribute('title', tooltip);
            tooltipTarget.setAttribute('data-bs-original-title', tooltip);
            tooltipTarget.setAttribute('data-bs-toggle', 'tooltip');
            refreshTooltip(tooltipTarget);
        }
    }

    personalFlBtn?.addEventListener('click', function () {
        if (!pendingDownloadData?.id) return;
        const originalHtml = this.innerHTML;
        this.disabled = true;
        this.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';

        fetch('/mam/buy_personal_fl', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ torrentid: pendingDownloadData.id })
        })
            .then(r => r.json())
            .then(data => {
                if (data && data.success) {
                    pendingDownloadData.personal_freeleech = 1;
                    markTorrentPersonalFreeleech(pendingDownloadData.id);
                    showToast(`Freeleech Wedge applied. FL left: ${data.FLleft ?? 'N/A'}`, 'success');
                    loadMamUserData();
                    updateConfirmModalFreeleechUI();
                } else {
                    showToast(data?.error || data?.message || 'Failed to apply Freeleech Wedge', 'danger');
                }
            })
            .catch(() => showToast('Connection error', 'danger'))
            .finally(() => {
                this.innerHTML = originalHtml;
                // Recompute enabled state after completion
                updateConfirmModalFreeleechUI();
            });
    });

    if (confirmInput) {
        confirmInput.addEventListener('input', updateConfirmPathPreview);
    }

    function performSearch(queryString, isHistoryNavigation = false) {
        activeSearchRequests += 1;
        refreshAppLogoState();
        searchButton.disabled = true;
        searchButton.innerHTML = SEARCH_BUTTON_LOADING_HTML;
        if (resultsTitle) resultsTitle.textContent = 'Results';
        hashToElementMap.clear();
        const searchUrl = queryString ? `/mam/search?${queryString}` : '/mam/search';

        return fetch(searchUrl)
            .then(response => response.text())
            .then(html => {
                wrapper.style.display = 'block';
                resultsContainer.innerHTML = html;
                applyCurrentResultsSort(resultsContainer);
                lastPerformedQuery = queryString;
                localizeDates(resultsContainer);
                applyResultDisplayFields(window.resultDisplayFields || getSelectedResultFields(), resultsContainer);

                // Tooltips are created on DOMContentLoaded, but search results are injected later.
                [...resultsContainer.querySelectorAll('[data-bs-toggle="tooltip"]')]
                    .forEach(el => {
                        const existing = bootstrap.Tooltip.getInstance(el);
                        if (existing) existing.dispose();
                        new bootstrap.Tooltip(el);
                    });

                // If we've applied any personal FL wedges this session, the server-rendered
                // results won't know about them. Re-apply to the newly-rendered rows.
                if (window.appliedPersonalFreeleechIds && window.appliedPersonalFreeleechIds.size) {
                    for (const tid of window.appliedPersonalFreeleechIds) {
                        markTorrentPersonalFreeleech(tid);
                    }
                }
                if (!isHistoryNavigation) {
                    scrollToResultsWrapper();
                }
                refreshCategories();
                initializeSnatchedTorrents();
                applyHideDownloadedResultsFilter();
            })
            .catch(error => {
                wrapper.style.display = 'block';
                resultsContainer.innerHTML = `<div class="alert alert-danger">Search failed.</div>`;
            })
            .finally(() => {
                activeSearchRequests = Math.max(0, activeSearchRequests - 1);
                refreshAppLogoState();
                searchButton.disabled = false;
                searchButton.innerHTML = SEARCH_BUTTON_DEFAULT_HTML;
            });
    }

    function restoreFormFromURL(params) {
        const queryInput = document.getElementById('query');
        if (queryInput) queryInput.value = params.get('query') || '';

        const searchFields = [
            'search_in_title',
            'search_in_author',
            'search_in_narrator',
            'search_in_series',
            'search_in_description',
            'search_in_tags',
            'search_in_filenames'
        ];
        searchFields.forEach(id => {
            const el = document.getElementById(id);
            if (!el) return;
            if (params.has(id)) {
                el.checked = true;
            } else {
                el.checked = DEFAULT_SEARCH_FIELDS[id] ?? false;
            }
        });

        let mainCatValues = params.getAll('main_cat');
        if (!mainCatValues.length) {
            mainCatValues = params.getAll('media_type');
        }
        setMainCatSelection(mainCatValues.length ? mainCatValues : DEFAULT_MAIN_CATS, true);

        const setRadioValue = (name, value, fallback) => {
            const radios = document.querySelectorAll(`input[name="${name}"]`);
            if (!radios.length) return;
            if (value) {
                radios.forEach(radio => {
                    radio.checked = radio.value === value;
                });
            } else if (fallback) {
                radios.forEach(radio => {
                    radio.checked = radio.value === fallback;
                });
            }
        };

        setRadioValue('searchType', params.get('searchType'), DEFAULT_SEARCH_TYPE);
        setRadioValue('search_scope', params.get('search_scope'), DEFAULT_SEARCH_SCOPE);
        setRadioValue('flags_mode', params.get('flags_mode'), DEFAULT_FLAGS_MODE);

        const hideDownloadedToggle = document.getElementById('hide_downloaded');
        if (hideDownloadedToggle) {
            hideDownloadedToggle.checked = params.has('hide_downloaded') ? true : DEFAULT_HIDE_DOWNLOADED;
        }

        if (langTomSelect) {
            let langValues = params.getAll('language_ids');
            const legacyLang = params.get('language');
            if (!langValues.length && legacyLang) {
                if (/^\d+$/.test(legacyLang)) {
                    langValues = [legacyLang];
                } else if (window.LANGUAGE_MAP && window.LANGUAGE_MAP[legacyLang]) {
                    langValues = [String(window.LANGUAGE_MAP[legacyLang])];
                }
            }

            if (langValues.length) {
                langTomSelect.setValue(langValues, true);
            } else if (DEFAULT_LANGUAGE_VALUES.length) {
                langTomSelect.setValue(DEFAULT_LANGUAGE_VALUES, true);
            }
        }

        if (catTomSelect) {
            const paramCatValues = params.getAll('category_ids');
            const catValues = paramCatValues.length ? paramCatValues : DEFAULT_CATEGORY_IDS;
            if (catValues.length) {
                catTomSelect.setValue(catValues, true);
                ensureMainCatsForSelectedSubcategories(catValues);
            } else {
                catTomSelect.clear(true);
            }
        }
        applyCategoryMainCatFilter();

        const paramFlagValues = params.getAll('flag_ids');
        const flagValues = new Set((paramFlagValues.length ? paramFlagValues : DEFAULT_FLAG_IDS).map(String));
        document.querySelectorAll('.flag-checkbox').forEach(cb => {
            cb.checked = flagValues.has(cb.value);
        });

        const textFields = [
            'start_date',
            'end_date',
            'min_size',
            'max_size',
            'min_seeders',
            'max_seeders',
            'min_leechers',
            'max_leechers',
            'min_snatched',
            'max_snatched'
        ];
        textFields.forEach(name => {
            const el = document.querySelector(`[name="${name}"]`);
            if (!el) return;
            if (params.has(name)) {
                el.value = params.get(name) || '';
            } else {
                el.value = DEFAULT_RANGE_FILTERS[name] || '';
            }
        });

        const sizeUnit = params.get('size_unit') || DEFAULT_RANGE_FILTERS.size_unit;
        const sizeUnitSelect = document.querySelector('select[name="size_unit"]');
        if (sizeUnit && sizeUnitSelect) sizeUnitSelect.value = sizeUnit;

        updateMirroredCheckboxes();
        updateFilterBadge();
        applyHideDownloadedResultsFilter();
    }

    if (searchForm) {
        document.getElementById('hide_downloaded')?.addEventListener('change', () => {
            applyHideDownloadedResultsFilter();
        });

        searchForm.addEventListener("submit", function (e) {
            e.preventDefault();
            triggerHaptic('search');
            document.getElementById('query').blur();
            if (advancedOffcanvasEl && advancedOffcanvasEl.classList.contains('show')) {
                bootstrap.Offcanvas.getOrCreateInstance(advancedOffcanvasEl).hide();
            }
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

                // Only perform search if the query has changed or results are missing
                const resultsEmpty = !document.getElementById('results-container').innerHTML.trim();

                if (event.state.query !== lastPerformedQuery || resultsEmpty) {
                    performSearch(event.state.query, true);
                } else {
                    // Results are already there (we just closed a modal), so just ensure they are visible
                    document.getElementById('results-container-wrapper').style.display = 'block';
                }
            }
        } else {
            // --- MODIFIED LANDING PAGE CHECK ---
            const urlParams = new URLSearchParams(window.location.search);
            restoreFormFromURL(urlParams);
            if (urlParams.has('query')) {
                const queryStr = urlParams.toString();
                const resultsEmpty = !document.getElementById('results-container').innerHTML.trim();

                if (queryStr !== lastPerformedQuery || resultsEmpty) {
                    performSearch(queryStr, true);
                }
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
    restoreFormFromURL(initialParams);

    // Check if we have a book hash (#book=12345)
    const hash = window.location.hash;
    const deepLinkID = hash.startsWith('#book=') ? hash.split('=')[1] : null;

    if (initialParams.has('query')) {
        // SCENARIO 1: We have a search query (Standard Refresh)
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

    // Result Click Handling (Download/Series)
    if (resultsContainer) {
        resultsContainer.addEventListener('click', function (event) {

            // CASE A: Clicked the "Download" button
            const button = event.target.closest('.add-to-client-button');
            if (button) {
                event.preventDefault();
                event.stopPropagation(); // Prevent opening the details modal
                triggerHaptic('download');

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
                        triggerHaptic('modal');
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
        const primarySeries = getPrimarySeriesInfo(rawSeries);
        const seriesName = primarySeries?.name || null;
        const seriesNumber = primarySeries?.number || "";

        // 1. Construct the download payload from the button's data attributes
        const downloadData = {
            torrent_url: button.dataset.torrentUrl,
            // Try to find the dropdown in the resultItem; default to empty if not found
            category: resultItem ? (resultItem.querySelector('.category-dropdown')?.value || '') : (document.getElementById('detail-cat-select')?.value || ''),
            id: button.dataset.id,
            author: button.dataset.author || "Unknown",
            title: button.dataset.title || "Unknown",
            size: button.dataset.size || '0 GiB',
            main_cat: button.dataset.mainCat || '',
            series_info: rawSeries,

            // Freeleech flags (used by confirm modal UI)
            free: button.dataset.free ?? 0,
            personal_freeleech: button.dataset.personalFreeleech ?? 0,
            fl_vip: button.dataset.flVip ?? 0,
        };

        // If we've applied a wedge in this session, prefer that state.
        if (downloadData.id && window.appliedPersonalFreeleechIds?.has(String(downloadData.id))) {
            downloadData.personal_freeleech = 1;
        }

        // 2. Check if Auto-Organize is enabled
        const autoOrganizeEnabled = document.getElementById('AUTO_ORGANIZE_ON_ADD')?.checked;

        // Save data to global vars for the "Confirm" button to use later
        pendingDownloadData = downloadData;
        pendingButton = button;

        if (confirmModal) {
            if (autoOrganizeSection) {
                autoOrganizeSection.classList.toggle('d-none', !autoOrganizeEnabled);
            }
            if (confirmDownloadOnly) {
                confirmDownloadOnly.classList.toggle('d-none', autoOrganizeEnabled);
            }

            if (autoOrganizeEnabled) {
                // --- Auto-Organize Logic (Populate Confirm Modal) ---
                const cleanAuthor = sanitizeFilename(downloadData.author);
                const cleanTitle = sanitizeFilename(downloadData.title);
                const cleanSeries = seriesName ? sanitizeFilename(seriesName) : "";
                const cleanSeriesNumber = seriesNumber ? sanitizeFilename(seriesNumber) : "";
                const relTemplate = normalizeRelPathTemplate(getRelPathTemplateValue());
                const templateHasSeries = relTemplate.includes('{Series}') || relTemplate.includes('{SeriesNumber}');

                syncConfirmDestinationOptions(downloadData.main_cat, true);

                // Set default path from template
                const relativePath = buildRelativePathFromTemplate(relTemplate, {
                    author: cleanAuthor,
                    series: cleanSeries,
                    seriesNumber: cleanSeriesNumber,
                    title: cleanTitle
                });
                if (confirmInput) confirmInput.value = relativePath;
                updateConfirmPathPreview();
                const pathHintEl = document.getElementById('path-format-hint');
                if (pathHintEl) pathHintEl.textContent = `Template: ${relTemplate}`;

                // Logic for the "+ Series" button inside the modal
                const addSeriesBtn = document.getElementById('add-series-btn');
                const seriesPreviewEl = document.getElementById('series-name-preview');

                if (addSeriesBtn) {
                    // Reset button state
                    addSeriesBtn.dataset.cleanAuthor = cleanAuthor;
                    addSeriesBtn.dataset.cleanTitle = cleanTitle;
                    addSeriesBtn.dataset.cleanSeries = cleanSeries;
                    addSeriesBtn.dataset.cleanSeriesNumber = cleanSeriesNumber;
                    addSeriesBtn.dataset.templateWithSeries = templateHasSeries
                        ? relTemplate
                        : insertSeriesTokenIntoTemplate(relTemplate);
                    addSeriesBtn.dataset.templateWithoutSeries = stripSeriesTokenFromTemplate(relTemplate);
                    setSeriesToggleButtonState(addSeriesBtn, templateHasSeries);

                    if (seriesName) {
                        addSeriesBtn.disabled = false;
                        if (seriesPreviewEl) {
                            seriesPreviewEl.textContent = `"${cleanSeries}"`;
                            seriesPreviewEl.style.display = 'inline';
                        }
                    } else {
                        addSeriesBtn.disabled = true;
                        if (seriesPreviewEl) seriesPreviewEl.style.display = 'none';
                    }
                }
            } else {
                if (confirmInput) confirmInput.value = '';
                syncConfirmDestinationOptions('', false);
                updateConfirmPathPreview();
                const pathHintEl = document.getElementById('path-format-hint');
                if (pathHintEl) pathHintEl.textContent = 'Format: Author / Title';
            }

            // Sync Freeleech UI
            updateConfirmModalFreeleechUI();

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
        // 1. Calculate Extensions and URLs
        const ext = getPosterExtension(data.poster_type);

        // Use '0' as timestamp to force CDN redirect to latest version
        const highResUrl = `https://cdn.myanonamouse.net/t/p/0/large/${data.id}.${ext}`;
        const lowResUrl = `https://cdn.myanonamouse.net/t/p/small/${data.id}.webp`;

        // 2. Prepare Proxy URLs
        const highResProxy = `/proxy_thumbnail?url=${encodeURIComponent(highResUrl)}`;
        const lowResProxy = `/proxy_thumbnail?url=${encodeURIComponent(lowResUrl)}`;

        // Push History State
        const newUrl = window.location.pathname + window.location.search + `#book=${data.id}`;
        history.pushState({
            type: 'book_details',
            bookData: data,
            // Store both so we can restore them on popstate if needed
            hiResSrc: highResProxy,
            lowResSrc: lowResProxy
        }, '', newUrl);

        // Render & Show
        renderBookDetails(data, highResProxy, lowResProxy);
        const modal = bootstrap.Modal.getOrCreateInstance(document.getElementById('bookDetailsModal'));
        modal.show();
    }

    /**
     * 2. RENDER FUNCTION
     * Called by openBookDetailsModal AND by the History Manager (popstate).
     * Updates the DOM elements inside the modal.
     */
    function renderBookDetails(data, hiResSrc, lowResSrc) {
        // Fallback calculation if called via history popstate
        if (!hiResSrc || !lowResSrc) {
            const ext = getPosterExtension(data.poster_type);
            const rawHi = `https://cdn.myanonamouse.net/t/p/0/large/${data.id}.${ext}`;
            const rawLow = `https://cdn.myanonamouse.net/t/p/small/${data.id}.webp`;
            hiResSrc = `/proxy_thumbnail?url=${encodeURIComponent(rawHi)}`;
            lowResSrc = `/proxy_thumbnail?url=${encodeURIComponent(rawLow)}`;
        }

        // --- Standard Metadata Rendering ---
        const authors = parseMamJson(data.author_info);
        const narrators = parseMamJson(data.narrator_info) || "N/A";
        const primarySeries = getPrimarySeriesInfo(data.series_info);
        const fallbackSeries = parseMamJson(data.series_info);
        const seriesLabel = formatPrimarySeriesLabel(primarySeries) || fallbackSeries || '';

        const titleLinkEl = document.getElementById('detail-title-link');
        if (titleLinkEl) {
            titleLinkEl.textContent = data.title || '';
            titleLinkEl.href = data?.id ? `https://www.myanonamouse.net/t/${data.id}` : '#';
        } else {
            // Fallback for older templates
            document.getElementById('detail-title').textContent = data.title || '';
        }
        renderJsonTree(data.mediainfo, 'mediainfo-tree-container');
        document.getElementById('detail-subtitle').innerHTML = seriesLabel ? `<span class="badge bg-secondary opacity-75">Series</span> ${seriesLabel}` : '';
        document.getElementById('detail-authors').textContent = authors;
        document.getElementById('detail-narrators').textContent = narrators;
        document.getElementById('detail-description').innerHTML = data.description || "No description available.";

        // ============================================================
        // NEW: BADGE LOGIC START
        // ============================================================
        const bDownloaded = document.getElementById('badge-downloaded');
        const bVip = document.getElementById('badge-vip');
        const bFree = document.getElementById('badge-freeleech');

        // 1. Reset all to hidden
        bDownloaded.classList.add('d-none');
        bVip.classList.add('d-none');
        bFree.classList.add('d-none');

        // Reset tooltip content/state (Bootstrap caches title in data-bs-original-title)
        if (bFree) {
            const existing = bootstrap.Tooltip.getInstance(bFree);
            if (existing) existing.dispose();
            bFree.setAttribute('title', 'Freeleech');
            bFree.setAttribute('data-bs-original-title', 'Freeleech');
        }

        // 2. Show if data matches (coercing to int just in case)
        if (parseInt(data.my_snatched) === 1) {
            bDownloaded.classList.remove('d-none');
        }
        if (parseInt(data.vip) === 1) {
            bVip.classList.remove('d-none');
        }
        const isPublicFree = parseInt(data.free) === 1;
        const isPersonalFree = (data?.id && window.appliedPersonalFreeleechIds?.has(String(data.id))) || (parseInt(data.personal_freeleech) === 1);
        const isVipFree = parseInt(data.fl_vip) === 1 && window.isVipActive === true;
        const isFreeleech = isPublicFree || isPersonalFree || isVipFree;

        if (isFreeleech) {
            bFree.classList.remove('d-none');
            const reason = isPublicFree ? 'Public Freeleech' : (isPersonalFree ? 'Personal Freeleech' : 'VIP Freeleech');
            bFree.setAttribute('title', reason);
            bFree.setAttribute('data-bs-original-title', reason);
            new bootstrap.Tooltip(bFree);
        }
        // ============================================================
        // NEW: BADGE LOGIC END
        // ============================================================


        // --- PROGRESSIVE IMAGE LOGIC START ---
        const imgEl = document.getElementById('detail-cover');
        const heroBg = document.getElementById('detail-hero-bg');

        // ... (The rest of your function remains exactly the same) ...

        // 1. ADD VISUAL CUE & CLICK HANDLER (New Code)
        imgEl.style.cursor = 'zoom-in';

        // Remove old listeners to prevent stacking if function runs multiple times
        const newImgEl = imgEl.cloneNode(true);
        imgEl.parentNode.replaceChild(newImgEl, imgEl);

        newImgEl.onclick = function () {
            const lightboxImg = document.getElementById('lightbox-img');
            // Use the hiResSrc we calculated for the modal
            lightboxImg.src = hiResSrc;

            const lightboxModal = new bootstrap.Modal(document.getElementById('coverLightboxModal'));
            lightboxModal.show();
        };

        // Reset reference for the rest of the logic
        const activeImgEl = newImgEl;

        // 2. Reset Background Styles
        heroBg.style.filter = 'blur(50px)';
        heroBg.style.transform = 'scale(1.2)';
        heroBg.style.opacity = '0.5';

        // 3. Attach Error Handler
        activeImgEl.onerror = function () { handleBookCoverError(this); };

        // 4. Set Initial State
        activeImgEl.src = lowResSrc;
        heroBg.style.backgroundImage = `url('${lowResSrc}')`;

        // 5. Spin up High Res Loader
        const hiResLoader = new Image();
        hiResLoader.src = hiResSrc;

        hiResLoader.onload = function () {
            const currentSrc = activeImgEl.src;
            if (activeImgEl && (currentSrc.includes(lowResSrc) || currentSrc.includes('no_cover.png'))) {
                activeImgEl.src = hiResSrc;
                heroBg.style.backgroundImage = `url('${hiResSrc}')`;

                if (currentSrc.includes('no_cover.png')) {
                    heroBg.style.filter = 'blur(50px)';
                    heroBg.style.transform = 'scale(1.2)';
                    heroBg.style.opacity = '0.5';
                }
            }
        };

        // --- Rest of Rendering (Metadata, Tags, etc.) ---
        document.getElementById('detail-category').innerHTML = data.catname;
        document.getElementById('detail-series').textContent = seriesLabel || '---';
        document.getElementById('detail-language').textContent = getLanguageName(data.lang_code);
        document.getElementById('detail-filetype').textContent = data.filetype;
        document.getElementById('detail-size').textContent = data.size.replace('iB', 'B');
        document.getElementById('detail-added').textContent = new Date(data.added).toLocaleDateString();
        document.getElementById('detail-seeders').textContent = data.seeders;
        document.getElementById('detail-leechers').textContent = data.leechers;

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

        const dlBtn = document.getElementById('detail-download-btn');

        // Reset button state in case a previous book download changed it (e.g. "Added!" + disabled)
        if (dlBtn) {
            dlBtn.disabled = false;
            dlBtn.innerHTML = '<i class="bi bi-play-fill me-1"></i> Download';
        }

        dlBtn.dataset.torrentUrl = data.download_link;
        dlBtn.dataset.id = data.id;
        dlBtn.dataset.author = authors;
        dlBtn.dataset.title = data.title;
        dlBtn.dataset.size = data.size;
        dlBtn.dataset.mainCat = data.main_cat;
        dlBtn.dataset.seriesInfo = data.series_info;
        dlBtn.dataset.free = data.free;
        dlBtn.dataset.personalFreeleech = data.personal_freeleech;
        dlBtn.dataset.flVip = data.fl_vip;

        const newDlBtn = dlBtn.cloneNode(true);
        dlBtn.parentNode.replaceChild(newDlBtn, dlBtn);

        newDlBtn.addEventListener('click', function () {
            triggerHaptic('download');
            initiateDownloadFlow(this, null);
        });

        document.getElementById('detail-torrent-link').href = data.download_link;

        // ============================================================
        // NEW: SYNC PROGRESS BAR ON OPEN
        // ============================================================
        const modalStatusContainer = document.querySelector('#details-footer .torrent-status-container');

        // 1. Clear previous status (in case we opened a different book)
        if (modalStatusContainer) modalStatusContainer.innerHTML = '';

        // 2. Find the row in the background list
        const backgroundRow = document.querySelector(`.result-item[data-torrent-id="${data.id}"]`);

        if (backgroundRow) {
            const rowStatus = backgroundRow.querySelector('.torrent-status-container');

            // 3. If the row has a progress bar or badge, copy it to the modal immediately
            if (rowStatus && rowStatus.innerHTML.trim() !== "") {
                modalStatusContainer.innerHTML = rowStatus.innerHTML;
            }
        }
    }

    // Confirm Download Modal Action
    document.getElementById('confirm-download-btn')?.addEventListener('click', function () {
        if (!pendingDownloadData) return;
        triggerHaptic('download');
        const autoOrganizeEnabled = document.getElementById('AUTO_ORGANIZE_ON_ADD')?.checked;
        if (autoOrganizeEnabled && confirmInput) {
            pendingDownloadData.custom_relative_path = confirmInput.value;
            pendingDownloadData.custom_destination_path = confirmDestinationSelect?.value || '';
        } else {
            delete pendingDownloadData.custom_relative_path;
            delete pendingDownloadData.custom_destination_path;
        }
        confirmModal.hide();
        performDownload(pendingDownloadData, pendingButton);
    });

    // Toggle Series in Path Button
    document.getElementById('add-series-btn')?.addEventListener('click', function () {
        const input = document.getElementById('confirm-path-input');
        const hintEl = document.getElementById('path-format-hint');
        const { cleanAuthor, cleanTitle, cleanSeries, cleanSeriesNumber, active, templateWithSeries, templateWithoutSeries } = this.dataset;
        const isActive = active === "true";
        const nextTemplate = isActive
            ? (templateWithoutSeries || DEFAULT_REL_PATH_TEMPLATE)
            : (templateWithSeries || templateWithoutSeries || DEFAULT_REL_PATH_TEMPLATE);

        input.value = buildRelativePathFromTemplate(nextTemplate, {
            author: cleanAuthor || '',
            series: cleanSeries || '',
            seriesNumber: cleanSeriesNumber || '',
            title: cleanTitle || ''
        });
        if (hintEl) hintEl.textContent = `Template: ${nextTemplate}`;
        setSeriesToggleButtonState(this, !isActive);
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

                    const responseHash = String(data.hash || '').trim().toLowerCase();
                    if (responseHash && downloadData.id) {
                        torrentHashMap[String(downloadData.id)] = responseHash;
                    }

                    // Find the row
                    let resultItem = button && button.closest ? button.closest('.result-item') : null;
                    if (!resultItem && downloadData.id) {
                        resultItem = document.querySelector(`.result-item[data-torrent-id="${downloadData.id}"]`);
                    }

                    const resolvingHtml = `<span class="badge bg-info text-wrap">Resolving torrent...</span>`;

                    // Update List Item
                    if (resultItem) {
                        resultItem.querySelectorAll('.torrent-status-container').forEach(el => {
                            el.innerHTML = resolvingHtml;
                        });
                    }

                    // [NEW] Update Modal Footer immediately
                    const modalBtn = document.getElementById('detail-download-btn');
                    const modalContainer = document.querySelector('#details-footer .torrent-status-container');
                    // Check if the download we just started matches the open modal
                    if (modalBtn && modalContainer && String(modalBtn.dataset.id) === String(downloadData.id)) {
                        modalContainer.innerHTML = resolvingHtml;
                    }

                    if (responseHash) {
                        pollTorrentStatus(responseHash, resultItem);
                        fetchAndUpdateTorrentStatus(responseHash, resultItem);
                        return;
                    }

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
                            const pendingHtml = `<span class="badge bg-warning">Added (pending)</span>`;

                            if (resultItem) {
                                resultItem.querySelectorAll('.torrent-status-container').forEach(el => el.innerHTML = pendingHtml);
                            }
                            if (modalBtn && modalContainer && String(modalBtn.dataset.id) === String(downloadData.id)) {
                                modalContainer.innerHTML = pendingHtml;
                            }
                        }
                    }, 2000);

                } else if (button) {
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

    // --- D. Sticky Header Search Logic ---
    // Add this inside the document.addEventListener("DOMContentLoaded", function () { ... }) block

    const navElement = document.getElementById('main-navbar');
    const navSearchContainer = document.getElementById('nav-search-container');
    const navSearchInput = document.getElementById('nav-search-input');
    const navSearchForm = document.getElementById('nav-search-form');
    const navbarBrand = document.getElementById('navbar-brand');

    // 1. Scroll Listener (Show/Hide)
    window.addEventListener('scroll', () => {
        if (!searchForm) return;

        // Calculate when the main search form leaves the screen
        const mainFormBottom = searchForm.getBoundingClientRect().bottom;
        const shouldShow = mainFormBottom < 0; // Negative means it scrolled up past the viewport top

        if (shouldShow) {
            navElement.classList.add('navbar-scrolled');
            navSearchContainer.classList.remove('opacity-0');
            navSearchContainer.style.pointerEvents = 'auto';

            navbarBrand.classList.remove('opacity-0');
            navbarBrand.style.pointerEvents = 'auto';
        } else {
            navElement.classList.remove('navbar-scrolled');
            navSearchContainer.classList.add('opacity-0');
            navSearchContainer.style.pointerEvents = 'none';

            navbarBrand.classList.add('opacity-0');
            navbarBrand.style.pointerEvents = 'none';
            // Optional: Blur to hide mobile keyboard if they scroll back up quickly
            if (document.activeElement === navSearchInput) navSearchInput.blur();
        }
    });

    // 2. Submit Listener (Sync & Search)
    if (navSearchForm) {
        navSearchForm.addEventListener('submit', (e) => {
            e.preventDefault();
            const val = navSearchInput.value.trim();
            if (val) {
                // Copy value to main search box
                const mainInput = document.getElementById('query');
                if (mainInput) {
                    mainInput.value = val;
                    // Trigger the main search button click to reuse all existing logic (filters, etc)
                    searchButton.click();

                    // Optional: Scroll slightly up so results aren't hidden behind the sticky header
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                }
            }
        });
    }

    // 3. Optional: Sync typing (If you want the main box to update as you type in the header)
    if (navSearchInput) {
        navSearchInput.addEventListener('input', function () {
            const mainInput = document.getElementById('query');
            if (mainInput) mainInput.value = this.value;
        });
    }
});

// ============================================================
//  4. AUTOSUGGEST LOGIC
// ============================================================

const debounce = (func, wait) => {
    let timeout;
    return (...args) => {
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(this, args), wait);
    };
};

function initAutosuggest(inputId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    const isMainSearchInput = inputId === 'query';
    const inputAnchor = input.closest('.form-floating, .input-group') || input.parentNode;

    // Create results dropdown container
    const container = document.createElement('div');
    container.className = 'autosuggest-results list-group shadow-sm';
    if (isMainSearchInput) {
        container.classList.add('autosuggest-results--above');
    }
    input.parentNode.appendChild(container);

    // State management for cancellation
    let debounceTimer = null;
    let abortController = null;
    let cacheProbeController = null;
    let hasIssuedInitialSearch = false;
    const MIN_AUTOSUGGEST_LENGTH = 3;
    const INITIAL_AUTOSUGGEST_TRIGGER_LENGTH = 5;

    const updateContainerGeometry = () => {
        if (!isMainSearchInput) return;
        const rect = input.getBoundingClientRect();
        const topPageMargin = 8;
        const availableHeight = Math.max(0, Math.floor(rect.top + window.scrollY - topPageMargin));
        container.style.height = 'auto';
        container.style.maxHeight = `${availableHeight}px`;
    };

    const escapeHtml = (value) => String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');

    const highlightQueryMatch = (text, query) => {
        const rawText = String(text || '');
        const normalizedQuery = String(query || '')
            .replace(/\*/g, ' ')
            .trim()
            .replace(/\s+/g, ' ');

        if (!rawText) return escapeHtml(rawText);

        // Prefer highlighting the full phrase when it exists.
        if (normalizedQuery.length > 1) {
            const phraseIdx = rawText.toLowerCase().indexOf(normalizedQuery.toLowerCase());
            if (phraseIdx !== -1) {
                const start = rawText.slice(0, phraseIdx);
                const match = rawText.slice(phraseIdx, phraseIdx + normalizedQuery.length);
                const end = rawText.slice(phraseIdx + normalizedQuery.length);
                return `${escapeHtml(start)}<strong>${escapeHtml(match)}</strong>${escapeHtml(end)}`;
            }
        }

        const cleanedWords = normalizedQuery
            .split(/\s+/)
            .map(word => word.replace(/\*/g, '').trim())
            .filter(word => word.length > 1)
            .sort((a, b) => b.length - a.length);

        if (!cleanedWords.length) return escapeHtml(rawText);

        const lowerText = rawText.toLowerCase();
        for (const word of cleanedWords) {
            const idx = lowerText.indexOf(word.toLowerCase());
            if (idx === -1) continue;
            const start = rawText.slice(0, idx);
            const match = rawText.slice(idx, idx + word.length);
            const end = rawText.slice(idx + word.length);
            return `${escapeHtml(start)}<strong>${escapeHtml(match)}</strong>${escapeHtml(end)}`;
        }

        return escapeHtml(rawText);
    };

    const getTypeBadgeClass = (type) => {
        if (type === 'author') return 'bg-info-subtle text-info-emphasis';
        if (type === 'series') return 'bg-warning-subtle text-warning-emphasis';
        if (type === 'narrator') return 'bg-success-subtle text-success-emphasis';
        return 'bg-primary-subtle text-primary-emphasis';
    };

    const getTypeLabel = (type) => {
        if (type === 'author') return 'Author';
        if (type === 'series') return 'Series';
        if (type === 'narrator') return 'Narrator';
        return 'Title';
    };

    const searchFilterIds = new Set([
        'search_in_title',
        'search_in_author',
        'search_in_series',
        'search_in_narrator',
        'advTitle',
        'advAuthor',
        'advSeries',
        'advNarrator'
    ]);
    const searchFilterSelector = [
        '#search_in_title',
        '#search_in_author',
        '#search_in_series',
        '#search_in_narrator',
        '#advTitle',
        '#advAuthor',
        '#advSeries',
        '#advNarrator',
        '[data-sync-target="search_in_title"]',
        '[data-sync-target="search_in_author"]',
        '[data-sync-target="search_in_series"]',
        '[data-sync-target="search_in_narrator"]'
    ].join(', ');
    const searchFilterElements = Array.from(document.querySelectorAll(searchFilterSelector));
    const mainCatSelectEl = document.getElementById('main_cat');

    const isSearchFieldToggleTarget = (target) => {
        if (!(target instanceof Element)) return false;
        if (target.closest(searchFilterSelector)) return true;
        const label = target.closest('label[for]');
        if (!label) return false;
        return searchFilterIds.has(label.getAttribute('for'));
    };

    const isMainCatTomSelectTarget = (target) => {
        if (!(target instanceof Element)) return false;
        if (!mainCatSelectEl) return false;
        if (mainCatSelectEl.contains(target)) return true;
        if (target.closest('#main_cat-ts-control') || target.closest('#main_cat-ts-dropdown')) return true;

        const tsInstance = mainCatPrimaryTomSelect || mainCatSelectEl.tomselect || null;
        if (!tsInstance) return false;
        if (tsInstance.wrapper?.contains(target)) return true;
        if (tsInstance.control?.contains(target)) return true;
        if (tsInstance.dropdown?.contains(target)) return true;
        return false;
    };

    const refreshVisibleSuggestions = () => {
        if (container.style.display === 'none') return;
        const val = input.value.trim();
        if (val.length < 3) {
            container.style.display = 'none';
            return;
        }
        clearTimeout(debounceTimer);
        performSearch(val);
    };

    const hideAllAutosuggestContainers = () => {
        document.querySelectorAll('.autosuggest-results').forEach((el) => {
            el.style.display = 'none';
        });
    };

    const isContainerVisibleInViewport = () => {
        if (container.style.display === 'none') return false;
        const computed = window.getComputedStyle(container);
        if (computed.display === 'none' || computed.visibility === 'hidden') return false;
        const rect = container.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return false;
        return rect.bottom > 0 && rect.top < window.innerHeight;
    };

    const getCachedSuggestions = (key) => {
        const cached = autosuggestCache.get(key);
        if (!cached) return null;
        if (cached.expiresAt <= Date.now()) {
            autosuggestCache.delete(key);
            return null;
        }
        // Promote to most recently used.
        autosuggestCache.delete(key);
        autosuggestCache.set(key, cached);
        return Array.isArray(cached.data) ? cached.data : null;
    };

    const setCachedSuggestions = (key, data) => {
        if (!Array.isArray(data) || data.length === 0) {
            autosuggestCache.delete(key);
            return;
        }
        autosuggestCache.delete(key);
        autosuggestCache.set(key, {
            data,
            expiresAt: Date.now() + AUTOSUGGEST_CACHE_TTL_MS
        });
        while (autosuggestCache.size > AUTOSUGGEST_CACHE_MAX_ENTRIES) {
            const oldestKey = autosuggestCache.keys().next().value;
            if (oldestKey === undefined) break;
            autosuggestCache.delete(oldestKey);
        }
    };

    const renderSuggestions = (data, val) => {
        container.innerHTML = '';

        if (!Array.isArray(data) || data.length === 0) {
            container.style.display = 'none';
            return;
        }

        data.forEach(item => {
            const a = document.createElement('a');
            a.className = 'list-group-item list-group-item-action py-2';
            a.href = '#';

            const primaryType = item.primary_type || 'title';
            const primaryText = item.primary_text || item.title || item.author || item.series || '';
            const badgeClass = getTypeBadgeClass(primaryType);
            const badgeLabel = getTypeLabel(primaryType);
            a.innerHTML = `
                <div class="d-flex align-items-center justify-content-between gap-2 w-100">
                    <div class="text-truncate text-sm" style="min-width: 0;">${highlightQueryMatch(primaryText, val)}</div>
                    <span class="badge rounded-pill ${badgeClass}" style="font-size: 0.65rem; flex-shrink: 0;">${badgeLabel}</span>
                </div>
            `;

            a.addEventListener('click', (e) => {
                e.preventDefault();
                if (abortController) abortController.abort();
                clearTimeout(debounceTimer);

                input.value = primaryText;

                const mainQuery = document.getElementById('query');
                if (mainQuery && input.id !== 'query') {
                    mainQuery.value = input.value;
                }

                container.style.display = 'none';
                document.getElementById('searchButton').click();
            });

            container.appendChild(a);
        });

        updateContainerGeometry();
        container.style.display = 'block';
    };

    const probeServerCache = async (queryString, val) => {
        if (!queryString) return;
        if (cacheProbeController) {
            cacheProbeController.abort();
        }
        cacheProbeController = new AbortController();

        try {
            const res = await fetch(`/mam/autosuggest?${queryString}&cache_only=true`, {
                signal: cacheProbeController.signal
            });
            if (!res.ok) return;

            const cacheHeader = (res.headers.get('x-autosuggest-cache') || '').toLowerCase();
            if (cacheHeader !== 'hit') return;

            const data = await res.json();
            if (!Array.isArray(data) || data.length === 0) return;

            const currentVal = input.value.trim();
            const currentQueryString = buildAutosuggestQueryString(currentVal);
            if (currentVal !== val || currentQueryString !== queryString) {
                return;
            }

            setCachedSuggestions(queryString, data);
            renderSuggestions(data, val);
        } catch (e) {
            if (e.name !== 'AbortError') {
                console.error("Autosuggest cache probe error", e);
            }
        }
    };

    const buildAutosuggestQueryString = (val) => {
        if (!val || val.length < MIN_AUTOSUGGEST_LENGTH) return null;
        const getCheck = (id) => document.getElementById(id)?.checked ? 'true' : 'false';

        const params = new URLSearchParams({
            q: val,
            search_in_title: getCheck('search_in_title'),
            search_in_author: getCheck('search_in_author'),
            search_in_narrator: getCheck('search_in_narrator'),
            search_in_series: getCheck('search_in_series')
        });

        const mainCatValues = mainCatPrimaryTomSelect ? getTomSelectValues(mainCatPrimaryTomSelect) : [];
        const normalizedMainCats = normalizeMainCatValues(mainCatValues);
        if (normalizedMainCats.length) {
            normalizedMainCats.forEach(id => params.append('main_cat', id));
        }

        const langIds = langTomSelect ? getTomSelectValues(langTomSelect) : [];
        if (langIds.length) {
            langIds.forEach(id => params.append('language_ids', id));
        } else if (window.DEFAULT_LANGUAGE_ID) {
            params.append('language_ids', String(window.DEFAULT_LANGUAGE_ID));
        }

        return params.toString();
    };

    const performSearch = async (val) => {
        if (abortController) {
            abortController.abort();
        }
        abortController = new AbortController();

        if (val.length < MIN_AUTOSUGGEST_LENGTH) {
            container.style.display = 'none';
            return;
        }

        try {
            const queryString = buildAutosuggestQueryString(val);
            if (!queryString) {
                container.style.display = 'none';
                return;
            }
            const cachedData = getCachedSuggestions(queryString);
            if (cachedData) {
                renderSuggestions(cachedData, val);
                return;
            }
            if (!hasIssuedInitialSearch && val.length < INITIAL_AUTOSUGGEST_TRIGGER_LENGTH) {
                return;
            }
            hasIssuedInitialSearch = true;

            const res = await fetch(`/mam/autosuggest?${queryString}`, {
                signal: abortController.signal
            });
            if (!res.ok) {
                throw new Error(`Autosuggest HTTP ${res.status}`);
            }
            const data = await res.json();

            setCachedSuggestions(queryString, data);
            renderSuggestions(data, val);
        } catch (e) {
            if (e.name !== 'AbortError') {
                console.error("Autosuggest error", e);
            }
        }
    };

    // --- Event Listeners ---

    // 1. Input: Debounce the search
    input.addEventListener('input', (e) => {
        clearTimeout(debounceTimer); // Clear previous timer
        const val = e.target.value.trim();
        if (!val) {
            hasIssuedInitialSearch = false;
        }

        if (val.length < MIN_AUTOSUGGEST_LENGTH) {
            container.style.display = 'none';
            return;
        }

        const queryString = buildAutosuggestQueryString(val);
        if (queryString) {
            const cachedData = getCachedSuggestions(queryString);
            if (cachedData) {
                renderSuggestions(cachedData, val);
            } else {
                probeServerCache(queryString, val);
            }
        }

        if (!hasIssuedInitialSearch) {
            if (val.length >= INITIAL_AUTOSUGGEST_TRIGGER_LENGTH) {
                hasIssuedInitialSearch = true;
                performSearch(val);
            }
            return;
        }

        // Wait 300ms before searching
        debounceTimer = setTimeout(() => {
            performSearch(val);
        }, 300);
    });

    // 2. Keydown: Check for Enter or Escape
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            // STOP everything:
            clearTimeout(debounceTimer);      // 1. Stop the timer if it hasn't fired yet
            if (abortController) {
                abortController.abort();      // 2. Kill the fetch if it's currently running
            }
            container.style.display = 'none'; // 3. Hide the UI immediately
        }
        else if (e.key === 'Escape') {
            container.style.display = 'none';
        }
    });

    // 3. Click Shield
    // If autosuggest is open+visible, outside clicks should only close the list,
    // not activate underlying clickable UI (except allowed search parameter controls).
    document.addEventListener('click', (e) => {
        if (!isContainerVisibleInViewport()) return;
        if (isSearchFieldToggleTarget(e.target)) {
            return;
        }
        if (isMainCatTomSelectTarget(e.target)) {
            return;
        }
        if (inputAnchor.contains(e.target) || container.contains(e.target)) {
            return;
        }
        hideAllAutosuggestContainers();
        if (e.cancelable) e.preventDefault();
        e.stopImmediatePropagation();
    }, true);

    searchFilterElements.forEach((element) => {
        element.addEventListener('change', refreshVisibleSuggestions);
    });
    if (mainCatSelectEl) {
        mainCatSelectEl.addEventListener('change', refreshVisibleSuggestions);
    }
    if (mainCatPrimaryTomSelect) {
        mainCatPrimaryTomSelect.on('change', refreshVisibleSuggestions);
        mainCatPrimaryTomSelect.on('item_add', refreshVisibleSuggestions);
        mainCatPrimaryTomSelect.on('item_remove', refreshVisibleSuggestions);
    }

    if (isMainSearchInput) {
        window.addEventListener('resize', updateContainerGeometry);
        window.addEventListener('scroll', updateContainerGeometry, { passive: true });
    }
}

// Initialize
document.addEventListener("DOMContentLoaded", function () {
    initAutosuggest('query');             // Main page search
    initAutosuggest('nav-search-input');  // Navbar search
});
