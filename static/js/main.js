// main.js

// ============================================================
//  1. GLOBAL HELPERS & STATE
// ============================================================

// Base path support: set by the inline shim in index.html when the app is
// served under a URL prefix (hypercorn --root-path). Empty string at root.
const APP_BASE = (window.APP_BASE || '').replace(/\/+$/, '');

// Icon definitions
const greenCheckIcon = `<img src="${APP_BASE}/static/icons/check_circle.svg" alt="connected" style="height: 16px; width: 16px;">`;
const redXIcon = `<img src="${APP_BASE}/static/icons/x_circle.svg" alt="not connected" style="height: 16px; width: 16px;">`;

// Global State
const torrentHashMap = {};
const hashToElementMap = new Map();
const hardcoverEnrichmentPollers = new Map();
const hardcoverEnrichmentObservers = new Map();
const hardcoverEnrichmentQueueRequests = new Map();
const HARDCOVER_LAZY_BUFFER_ROWS = 2;
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
const AUTOSUGGEST_CACHE_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const autosuggestCache = window.__autosuggestCache instanceof Map ? window.__autosuggestCache : new Map();
window.__autosuggestCache = autosuggestCache;
const hardcoverSeriesCache = window.__hardcoverSeriesCache instanceof Map ? window.__hardcoverSeriesCache : new Map();
window.__hardcoverSeriesCache = hardcoverSeriesCache;
const hardcoverPendingStatusBookIds = window.__hardcoverPendingStatusBookIds instanceof Set ? window.__hardcoverPendingStatusBookIds : new Set();
window.__hardcoverPendingStatusBookIds = hardcoverPendingStatusBookIds;
const pendingHardcoverEnrichmentPayloads = window.__pendingHardcoverEnrichmentPayloads instanceof Map ? window.__pendingHardcoverEnrichmentPayloads : new Map();
window.__pendingHardcoverEnrichmentPayloads = pendingHardcoverEnrichmentPayloads;
let hardcoverStatusPickerOpenedAt = 0;
const MOUSESEARCH_LOGO_URL = `${APP_BASE}/static/icons/mouse.svg`;
const HARDCOVER_LOGO_URL = `${APP_BASE}/static/icons/hardcover.png`;
const HARDCOVER_STATUS_DEFINITIONS = Object.freeze([
    {
        statusId: 1,
        key: 'want-to-read',
        label: 'Want to Read',
        viewBox: '0 0 384 512',
        iconPaths: `
            <path d="M0 487.7V48C0 21.5 21.5 0 48 0h48v322.1c0 12.8 14.2 20.4 24.9 13.3L192 288l71.1 47.4c10.6 7.1 24.9-.5 24.9-13.3V0h48c26.5 0 48 21.5 48 48v439.7a24.33 24.33 0 0 1-38.3 19.9L192 400 38.3 507.6A24.33 24.33 0 0 1 0 487.7" fill="currentColor"></path>
            <path d="m192 288-71.1 47.4c-10.6 7.1-24.9-.5-24.9-13.3V0h192v322.1c0 12.8-14.2 20.4-24.9 13.3z" fill="currentColor" opacity="0.4"></path>`,
    },
    {
        statusId: 2,
        key: 'currently-reading',
        label: 'Reading',
        viewBox: '0 0 576 512',
        iconPaths: `
            <path d="M288 72v408s-92.8-32-144-32c-38.5 0-88.4 12.1-119.9 22.6C12.8 474.3 0 466 0 454.1V83.8c0-12.1 6.8-23.3 18.1-27.7C46.3 45.3 93.5 32 144 32c64 0 128 24 144 40" fill="currentColor"></path>
            <path d="M288 72v408s92.8-32 144-32c38.5 0 88.4 12.1 119.9 22.6 11.3 3.8 24.1-4.6 24.1-16.5V83.8c0-12.1-6.8-23.3-18.1-27.6C529.7 45.3 482.5 32 432 32c-64 0-128 24-144 40" fill="currentColor" opacity="0.4"></path>`,
    },
    {
        statusId: 3,
        key: 'read',
        label: 'Read',
        viewBox: '0 0 512 512',
        iconPaths: `
            <path d="M369 175a23.9 23.9 0 0 1 0 33.9L241 337a23.9 23.9 0 0 1-33.9 0l-64-64c-9.4-9.4-9.4-24.6 0-33.9s24.6-9.4 33.9 0l47 47L335 175a23.9 23.9 0 0 1 33.9 0z" fill="currentColor"></path>
            <path d="M256 0a96 96 0 0 1 84.9 51.1C373.8 41 411 49 437 75s34 63.3 23.9 96.1A96 96 0 0 1 512 256a96 96 0 0 1-51.1 84.9C471 373.8 463 411 437 437s-63.3 34-96.1 23.9A96 96 0 0 1 256 512a96 96 0 0 1-84.9-51.1C138.2 471 101 463 75 437s-34-63.3-23.9-96.1A96 96 0 0 1 0 256a96 96 0 0 1 51.1-84.9C41 138.2 49 101 75 75s63.3-34 96.1-23.9A96 96 0 0 1 256 0m113 209c9.4-9.4 9.4-24.6 0-33.9s-24.6-9.4-33.9 0l-111 111-47-47c-9.4-9.4-24.6-9.4-33.9 0s-9.4 24.6 0 33.9l64 64a23.9 23.9 0 0 0 33.9 0z" fill="currentColor" opacity="0.4"></path>`,
    },
    {
        statusId: 4,
        key: 'paused',
        label: 'Paused',
        viewBox: '0 0 512 512',
        iconPaths: `
            <path d="M256 512A256 256 0 1 0 256 0a256 256 0 1 0 0 512z" fill="currentColor" opacity="0.18"></path>
            <path d="M184 160c0-8.8 7.2-16 16-16h24c8.8 0 16 7.2 16 16v192c0 8.8-7.2 16-16 16h-24c-8.8 0-16-7.2-16-16V160zm104-16h24c8.8 0 16 7.2 16 16v192c0 8.8-7.2 16-16 16h-24c-8.8 0-16-7.2-16-16V160c0-8.8 7.2-16 16-16z" fill="currentColor"></path>`,
    },
    {
        statusId: 5,
        key: 'did-not-finish',
        label: 'Did Not Finish',
        viewBox: '0 0 512 512',
        iconPaths: `
            <path d="M256 512a256 256 0 1 0 0-512 256 256 0 1 0 0 512m-64-352h128c17.7 0 32 14.3 32 32v128c0 17.7-14.3 32-32 32H192c-17.7 0-32-14.3-32-32V192c0-17.7 14.3-32 32-32" fill="currentColor" opacity="0.4"></path>`,
    },
    {
        statusId: 6,
        key: 'ignored',
        label: 'Ignored',
        viewBox: '0 0 512 512',
        iconPaths: `
            <path d="M256 8C119 8 8 119 8 256s111 248 248 248 248-111 248-248S393 8 256 8z" fill="currentColor" opacity="0.18"></path>
            <path d="M363.3 148.7c9.4 9.4 9.4 24.6 0 33.9L182.6 363.3c-9.4 9.4-24.6 9.4-33.9 0s-9.4-24.6 0-33.9l180.7-180.7c9.4-9.4 24.6-9.4 33.9 0z" fill="currentColor"></path>`,
    },
]);
const HARDCOVER_STATUS_BY_ID = new Map(HARDCOVER_STATUS_DEFINITIONS.map(status => [status.statusId, status]));
const HARDCOVER_STATUS_OPTIONS = Object.freeze(HARDCOVER_STATUS_DEFINITIONS.filter(status =>
    [1, 2, 3, 5].includes(status.statusId)
));
const HARDCOVER_REMOVE_ACTION = Object.freeze({
    key: 'remove',
    label: 'Remove',
    viewBox: '0 0 448 512',
    iconPaths: `
        <path d="M163.8 0c-12.1 0-23.2 6.8-28.6 17.7L128 32H32C14.3 32 0 46.3 0 64s14.3 32 32 32h384c17.7 0 32-14.3 32-32s-14.3-32-32-32h-96l-7.2-14.3A31.9 31.9 0 0 0 284.2 0z" fill="currentColor"></path>
        <path d="M416 96H32v352c0 35.3 28.7 64 64 64h256c35.3 0 64-28.7 64-64zm-272 80v224c0 8.8-7.2 16-16 16s-16-7.2-16-16V176c0-8.8 7.2-16 16-16s16 7.2 16 16m96 0v224c0 8.8-7.2 16-16 16s-16-7.2-16-16V176c0-8.8 7.2-16 16-16s16 7.2 16 16m96 0v224c0 8.8-7.2 16-16 16s-16-7.2-16-16V176c0-8.8 7.2-16 16-16s16 7.2 16 16" fill="currentColor" opacity="0.4"></path>`,
});
const HARDCOVER_STATUS_PLACEHOLDER = Object.freeze({
    key: 'unset',
    label: '',
    viewBox: '0 0 448 512',
    iconPaths: `
        <path d="M96 0C60.7 0 32 28.7 32 64v384c0 35.3 28.7 64 64 64h256c35.3 0 64-28.7 64-64V160H288c-17.7 0-32-14.3-32-32V0zm144 0v128h128z" fill="currentColor" opacity="0.25"></path>
        <path d="M256 232c13.3 0 24 10.7 24 24v40h40c13.3 0 24 10.7 24 24s-10.7 24-24 24h-40v40c0 13.3-10.7 24-24 24s-24-10.7-24-24v-40h-40c-13.3 0-24-10.7-24-24s10.7-24 24-24h40v-40c0-13.3 10.7-24 24-24z" fill="currentColor"></path>`,
});
const UPLOAD_AMOUNT_STEP = 1;
const UPLOAD_AMOUNT_MIN = 50;
const UPLOAD_COST_PER_GB = 500;

const HAPTIC_PATTERNS = Object.freeze({
    tap: 15,
    light: 10,
    search: [20, 35, 20],
    accordion: 12,
    close: [12, 24, 12],
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

function bindHapticClick(selector, pattern = 'tap') {
    document.querySelectorAll(selector).forEach((element) => {
        element.addEventListener('click', () => {
            triggerHaptic(pattern);
        });
    });
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

    const legacyUrl = window.LEGACY_CATEGORY_URL || `${APP_BASE}/static/categoryDefinitionsLegacy.json`;
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

    const maxAffordable = Math.floor(window.currentBonusPoints / UPLOAD_COST_PER_GB);
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
    if (checkbox && checkbox.dataset.locked !== 'true') {
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
    imgElement.src = `${APP_BASE}/static/icons/no_cover.png`;

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

function setInlineConnectionStatus({
    alertId,
    iconId,
    messageId,
    state = 'idle',
    message = '',
}) {
    const alertEl = document.getElementById(alertId);
    const iconEl = document.getElementById(iconId);
    const messageEl = document.getElementById(messageId);
    if (!alertEl || !messageEl) return;

    const normalizedState = String(state || 'idle').toLowerCase();
    alertEl.className = 'alert small mt-3 mb-0';
    let iconMarkup = '';

    if (normalizedState === 'success') {
        alertEl.classList.add('alert-success');
        iconMarkup = greenCheckIcon;
    } else if (normalizedState === 'error') {
        alertEl.classList.add('alert-danger');
        iconMarkup = redXIcon;
    } else {
        alertEl.classList.add('alert-secondary');
    }

    if (iconEl) iconEl.innerHTML = iconMarkup;
    messageEl.textContent = message || '';
}

function setInlineActionStatus(alertId, state, message) {
    const alertEl = document.getElementById(alertId);
    if (!alertEl) return;

    const normalizedState = String(state || 'idle').toLowerCase();
    alertEl.className = 'alert small mt-3 mb-0';
    if (normalizedState === 'success') {
        alertEl.classList.add('alert-success');
    } else if (normalizedState === 'error') {
        alertEl.classList.add('alert-danger');
    } else {
        alertEl.classList.add('alert-secondary');
    }
    alertEl.textContent = message || '';
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

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[char]));
}

function arrayFromValue(value) {
    if (!value) return [];
    if (Array.isArray(value)) return value.filter(Boolean);
    if (typeof value === 'object') return Object.values(value).filter(Boolean);
    return [value];
}

function firstListedName(value) {
    const first = arrayFromValue(value).map(item => String(item).trim()).find(Boolean) || '';
    return first.split(',').map(part => part.trim()).find(Boolean) || '';
}

function updateResultJsonData(resultItem, patch) {
    if (!resultItem) return;
    let data = {};
    try {
        data = JSON.parse(resultItem.dataset.json || '{}');
    } catch (_) {
        data = {};
    }
    resultItem.dataset.json = JSON.stringify({ ...data, ...patch });
}

function hardcoverCoverUrlFromResultItem(resultItem) {
    if (!resultItem) return '';
    try {
        const data = JSON.parse(resultItem.dataset.json || '{}');
        return data?.hardcover_enrichment?.hardcover?.cover_image || '';
    } catch (_) {
        return '';
    }
}

function handleResultThumbnailError(imgElement) {
    if (!imgElement) return;
    const resultItem = imgElement.closest('.result-item');
    const hardcoverCoverUrl = hardcoverCoverUrlFromResultItem(resultItem);
    if (hardcoverCoverUrl && imgElement.dataset.triedHardcoverCover !== 'true') {
        imgElement.dataset.triedHardcoverCover = 'true';
        if (resultItem) resultItem.dataset.hasMamCover = 'false';
        imgElement.src = `${APP_BASE}/proxy_thumbnail?url=${encodeURIComponent(hardcoverCoverUrl)}`;
        return;
    }

    imgElement.onerror = null;
    imgElement.src = `${APP_BASE}/static/icons/no_cover.png`;
}

window.handleResultThumbnailError = handleResultThumbnailError;

function hardcoverUrl(metadata) {
    const slug = String(metadata?.slug || '').trim();
    if (!slug) return '';
    const rawPath = String(metadata?.url_path || '').trim().toLowerCase();
    const objectType = String(metadata?.object_type || '').trim().toLowerCase();
    const path = ['books', 'series', 'authors'].includes(rawPath)
        ? rawPath
        : objectType === 'series'
            ? 'series'
            : objectType === 'author'
                ? 'authors'
                : 'books';
    return `https://hardcover.app/${path}/${encodeURIComponent(slug)}`;
}

function normalizeHardcoverUserBook(userBook) {
    if (!userBook || typeof userBook !== 'object') return null;
    const id = Number(userBook.id);
    const statusId = Number(userBook.status_id ?? userBook.statusId);
    if (!Number.isFinite(id) || id <= 0 || !Number.isFinite(statusId) || statusId <= 0) return null;

    const editionId = Number(userBook.edition_id ?? userBook.editionId);
    const privacySettingId = Number(userBook.privacy_setting_id ?? userBook.privacySettingId);
    const userId = Number(userBook.user_id ?? userBook.userId);
    const rating = userBook.rating == null || userBook.rating === '' ? null : Number(userBook.rating);
    const bookId = Number(userBook.book_id ?? userBook.bookId);

    return {
        id,
        book_id: Number.isFinite(bookId) && bookId > 0 ? bookId : null,
        edition_id: Number.isFinite(editionId) && editionId > 0 ? editionId : null,
        user_id: Number.isFinite(userId) && userId > 0 ? userId : null,
        status_id: statusId,
        status: String(userBook.status || userBook.label || '').trim(),
        privacy_setting_id: Number.isFinite(privacySettingId) && privacySettingId > 0 ? privacySettingId : 1,
        rating: Number.isFinite(rating) ? rating : null,
        updated_at: String(userBook.updated_at ?? userBook.updatedAt ?? '').trim(),
    };
}

function hardcoverStatusDefinition(statusId) {
    return HARDCOVER_STATUS_BY_ID.get(Number(statusId)) || null;
}

function renderHardcoverStatusIcon(statusDef, extraClass = '') {
    if (!statusDef) return '';
    return `
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="${statusDef.viewBox}"
            class="hardcover-status-icon${extraClass ? ` ${extraClass}` : ''}" aria-hidden="true">
            ${statusDef.iconPaths}
        </svg>`;
}

function renderHardcoverStatusPicker(metadata, { torrentId = '', variant = 'compact' } = {}) {
    const userBook = normalizeHardcoverUserBook(metadata?.user_book);
    const userBookKnown = metadata?.user_book_known === true || metadata?.user_book_known === 'true' || !!userBook;
    const objectType = String(metadata?.object_type || '').trim().toLowerCase();
    const bookId = Number(metadata?.book_id);
    const currentStatusId = Number(userBook?.status_id || 0);
    const statusDef = hardcoverStatusDefinition(currentStatusId) || HARDCOVER_STATUS_PLACEHOLDER;
    const isPending = hardcoverPendingStatusBookIds.has(bookId);
    if (objectType !== 'book' || !Number.isFinite(bookId) || bookId <= 0) return '';

    const safeTorrentId = String(torrentId || '').trim();
    const safeTitle = String(metadata?.title || 'this book').trim() || 'this book';
    const statusLabel = String(statusDef.label || '').trim();
    const optionsHtml = HARDCOVER_STATUS_OPTIONS.map((option) => `
        <button type="button"
            class="hardcover-status-option${option.statusId === currentStatusId ? ' is-active' : ''}"
            data-hardcover-status-option="${option.statusId}" role="menuitemradio"
            aria-checked="${option.statusId === currentStatusId ? 'true' : 'false'}">
            <span class="hardcover-status-option__iconbox hardcover-status--${option.key}">
                ${renderHardcoverStatusIcon(option)}
            </span>
            <span class="hardcover-status-option__label">${escapeHtml(option.label)}</span>
            ${option.statusId === currentStatusId ? '<span class="hardcover-status-option__dot" aria-hidden="true"></span>' : ''}
        </button>`).join('');
    const removeHtml = userBook && currentStatusId > 0 ? `
        <div class="hardcover-status-menu__footer">
            <button type="button"
                class="hardcover-status-option hardcover-status-option--remove"
                data-hardcover-status-remove="true"
                role="menuitem">
                <span class="hardcover-status-option__iconbox hardcover-status--remove">
                    ${renderHardcoverStatusIcon(HARDCOVER_REMOVE_ACTION)}
                </span>
                <span class="hardcover-status-option__label">${escapeHtml(HARDCOVER_REMOVE_ACTION.label)}</span>
            </button>
        </div>` : '';

    return `
        <div class="hardcover-status-picker hardcover-status-picker--${escapeHtml(variant)}"
            data-hardcover-status-picker
            data-book-id="${bookId}"
            data-torrent-id="${escapeHtml(safeTorrentId)}"
            data-current-status-id="${currentStatusId}"
            data-user-book-known="${userBookKnown ? 'true' : 'false'}"
            data-busy="${isPending ? 'true' : 'false'}">
            <button type="button"
                class="hardcover-status-trigger hardcover-status--${escapeHtml(statusDef.key)}${isPending ? ' is-pending' : ''}"
                data-hardcover-status-toggle
                aria-haspopup="menu"
                aria-expanded="false"
                aria-label="Change Hardcover status for ${escapeHtml(safeTitle)}"
                aria-busy="${isPending ? 'true' : 'false'}"
                ${isPending ? 'disabled' : ''}>
                ${renderHardcoverStatusIcon(statusDef)}
                ${statusLabel ? `<span class="hardcover-status-trigger__label">${escapeHtml(statusLabel)}</span>` : ''}
                <i class="bi bi-chevron-down hardcover-status-trigger__caret" aria-hidden="true"></i>
            </button>
            <div class="hardcover-status-menu" role="menu">
                ${optionsHtml}
                ${removeHtml}
            </div>
        </div>`;
}

function getResultItemHardcoverData(resultItem) {
    if (!resultItem) return null;
    try {
        return JSON.parse(resultItem.dataset.json || '{}');
    } catch (_) {
        return null;
    }
}

function updateHardcoverUserBookStateForResultItem(resultItem, userBook, { userBookKnown = true } = {}) {
    const data = getResultItemHardcoverData(resultItem);
    const normalizedUserBook = normalizeHardcoverUserBook(userBook);
    const enrichment = data?.hardcover_enrichment;
    if (!data || !enrichment?.hardcover) return false;

    const hardcover = enrichment.hardcover;
    if (String(hardcover.object_type || '').trim().toLowerCase() !== 'book') return false;
    hardcover.user_book = normalizedUserBook;
    hardcover.user_book_known = !!userBookKnown;
    resultItem.dataset.json = JSON.stringify(data);

    const container = resultItem.querySelector('[data-hardcover-container]');
    if (container) {
        container.innerHTML = renderHardcoverMetadata(enrichment, {
            torrentId: String(resultItem.dataset.torrentId || ''),
        });
    }
    return true;
}

function syncHardcoverUserBookStatus(bookId, userBook, { userBookKnown = true } = {}) {
    const normalizedBookId = Number(bookId);
    const normalizedUserBook = normalizeHardcoverUserBook(userBook);
    if (!Number.isFinite(normalizedBookId) || normalizedBookId <= 0) return;

    document.querySelectorAll('.result-item[data-json]').forEach(resultItem => {
        const data = getResultItemHardcoverData(resultItem);
        const matchedBookId = Number(data?.hardcover_enrichment?.hardcover?.book_id);
        if (matchedBookId === normalizedBookId) {
            updateHardcoverUserBookStateForResultItem(resultItem, normalizedUserBook, { userBookKnown });
        }
    });

    const bookModalEl = document.getElementById('bookDetailsModal');
    const currentTorrentId = String(bookModalEl?.dataset.currentTorrentId || '').trim();
    if (!bookModalEl || !bookModalEl.classList.contains('show') || !currentTorrentId) return;

    const escapedTorrentId = window.CSS && CSS.escape
        ? CSS.escape(currentTorrentId)
        : currentTorrentId.replace(/["\\]/g, '\\$&');
    const activeRow = document.querySelector(`.result-item[data-torrent-id="${escapedTorrentId}"]`);
    const activeData = getResultItemHardcoverData(activeRow);
    const activeBookId = Number(activeData?.hardcover_enrichment?.hardcover?.book_id);
    if (activeBookId === normalizedBookId) {
        renderBookDetailsHardcover(activeData.hardcover_enrichment);
    }
}

function setHardcoverStatusPending(bookId, isPending) {
    const normalizedBookId = Number(bookId);
    if (!Number.isFinite(normalizedBookId) || normalizedBookId <= 0) return;
    if (isPending) {
        hardcoverPendingStatusBookIds.add(normalizedBookId);
    } else {
        hardcoverPendingStatusBookIds.delete(normalizedBookId);
    }

    document.querySelectorAll(`[data-hardcover-status-picker][data-book-id="${normalizedBookId}"]`).forEach((picker) => {
        picker.dataset.busy = isPending ? 'true' : 'false';
        const toggle = picker.querySelector('[data-hardcover-status-toggle]');
        if (!toggle) return;
        toggle.disabled = !!isPending;
        toggle.setAttribute('aria-busy', isPending ? 'true' : 'false');
        toggle.classList.toggle('is-pending', !!isPending);
    });
}

function renderStarRating(rating, ratingsCount, { hideCountOnMobile = false, countOverride = null, countLinkUrl = '', wrapWithLink = false } = {}) {
    if (!Number.isFinite(rating) || rating <= 0) return '';
    const clamped = Math.max(0, Math.min(5, rating));
    const stars = Array.from({ length: 5 }, (_, index) => {
        const fill = Math.max(0, Math.min(1, clamped - index)) * 100;
        return `
            <span class="hardcover-star position-relative d-inline-block" aria-hidden="true"
                style="width: 1em; height: 1em; line-height: 1;">
                <span class="text-body-secondary opacity-50">★</span>
                <span class="position-absolute top-0 start-0 overflow-hidden text-warning"
                    style="width: ${fill.toFixed(0)}%;">★</span>
            </span>`;
    }).join('');

    const count = Number(countOverride ?? ratingsCount);
    let countHtml = '';
    if (count > 0) {
        const countClasses = `opacity-50 fw-normal${hideCountOnMobile ? ' hardcover-review-count-mobile-hide' : ''}`;
        countHtml = countLinkUrl && !wrapWithLink
            ? `&emsp;<a href="${escapeHtml(countLinkUrl)}" target="_blank" rel="noopener noreferrer" class="${countClasses} link-secondary text-decoration-none hover-primary">(${count.toLocaleString()})</a>`
            : `&emsp;<span class="${countClasses}">(${count.toLocaleString()})</span>`;
    }

    const content = `
        <div class="hardcover-rating d-flex align-items-center gap-1 text-body-secondary">
            <span class="d-inline-flex" role="img" aria-label="${clamped.toFixed(1)} out of 5 stars">${stars}</span>
            <span class="fw-medium">${clamped.toFixed(1)}${countHtml}</span>
        </div>`;

    if (countLinkUrl && wrapWithLink) {
        return `<a href="${escapeHtml(countLinkUrl)}" target="_blank" rel="noopener noreferrer" class="text-decoration-none hover-primary d-inline-block">${content}</a>`;
    }

    return content;
}

function renderHardcoverTitleHtml(metadata) {
    const title = String(metadata?.title || '').trim();
    if (!title) return '';
    const url = hardcoverUrl(metadata);
    return url
        ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="link-body-emphasis text-decoration-none hover-primary">${escapeHtml(title)}</a>`
        : escapeHtml(title);
}

function renderHardcoverAuthorHtml(metadata) {
    const authors = arrayFromValue(metadata?.authors).map((value) => String(value || '').trim()).filter(Boolean);
    if (!authors.length) return '';

    const authorSlugs = Array.isArray(metadata?.author_slugs) ? metadata.author_slugs : [];
    return authors.map((authorName, index) => {
        const authorUrl = hardcoverAuthorLink(authorSlugs[index]);
        return authorUrl
            ? `<a href="${escapeHtml(authorUrl)}" target="_blank" rel="noopener noreferrer" class="link-body-emphasis text-decoration-none hover-primary">${escapeHtml(authorName)}</a>`
            : escapeHtml(authorName);
    }).join(', ');
}

function renderHardcoverSeriesHtml(metadata) {
    const featuredSeries = metadata?.featured_series;
    if (featuredSeries && typeof featuredSeries === 'object') {
        const name = String(featuredSeries.name || featuredSeries?.series?.name || '').trim();
        const position = Number(featuredSeries.position);
        if (name) {
            const label = Number.isFinite(position) && position > 0 ? `${name} #${position}` : name;
            const url = hardcoverSeriesLink(featuredSeries.slug || featuredSeries?.series?.slug);
            return url
                ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="link-body-emphasis text-decoration-none hover-primary">${escapeHtml(label)}</a>`
                : escapeHtml(label);
        }
    }

    const firstSeries = uniqueHardcoverStrings(metadata?.series_names)[0] || '';
    return firstSeries ? escapeHtml(firstSeries) : '';
}

function renderHardcoverMetadata(enrichment, { torrentId = '' } = {}) {
    const metadata = enrichment?.hardcover;

    if (!metadata) {
        const reason = enrichment?.failure_reason || 'unresolved';
        let displayMessage = 'No match';
        if (reason === 'http_401' || reason === 'http_403') {
            displayMessage = 'Auth error';
        }
        
        return `
            <div class="hardcover-match hardcover-match--static d-flex flex-column gap-1 text-decoration-none pe-none">
                <div class="d-flex align-items-center gap-1 text-body-secondary" style="font-size: 0.7rem;">
                    <img src="${HARDCOVER_LOGO_URL}" alt="" style="width: 0.8rem; height: 0.8rem; object-fit: contain;" loading="lazy">
                    <span class="text-uppercase fw-semibold" style="letter-spacing: 0.05em;">Hardcover</span>
                </div>
                <div style="font-size: 0.8rem;">${displayMessage}</div>
            </div>`;
    }

    const rating = Number(metadata.rating);
    const hasRating = Number.isFinite(rating) && rating > 0;
    const publishedText = formatHardcoverPublishedText(metadata, { preferYearOnly: true });
    const author = firstListedName(metadata.authors);
    const title = metadata.title || 'Unknown Title';
    const tooltipText = `<div class='text-start'><strong>Title:</strong> ${escapeHtml(title)}<br><strong>Author:</strong> ${escapeHtml(author)}</div>`;
    const url = hardcoverUrl(metadata);
    const tagName = url ? 'a' : 'div';
    const linkAttrs = (url ? `href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" ` : '') +
        `data-bs-toggle="tooltip" data-bs-html="true" title="${escapeHtml(tooltipText)}"`;
    const statusHtml = renderHardcoverStatusPicker(metadata, { torrentId, variant: 'compact' });

    return `
        <div class="hardcover-match hardcover-match--linked d-flex flex-column gap-2">
            <${tagName} class="hardcover-match__content text-decoration-none ${url ? '' : 'pe-none'}" ${linkAttrs}>
                <div class="d-flex align-items-center gap-1 text-body-secondary" style="font-size: 0.7rem;">
                    <img src="${HARDCOVER_LOGO_URL}" alt="" style="width: 0.8rem; height: 0.8rem; object-fit: contain;" loading="lazy">
                    <span class="text-uppercase fw-semibold" style="letter-spacing: 0.05em;">Hardcover</span>
                </div>
                ${hasRating ? `<div>${renderStarRating(rating, metadata.ratings_count, { hideCountOnMobile: true })}</div>` : ''}
                ${publishedText ? `<div class="text-body-secondary hardcover-mobile-hide" style="font-size: 0.8rem;">Published ${escapeHtml(publishedText)}</div>` : ''}
            </${tagName}>
            ${statusHtml}
        </div>`;
}

function formatHardcoverDate(value) {
    const text = String(value || '').trim();
    if (!text) return '';
    if (/^\d{4}$/.test(text)) return text;
    const parsed = /^\d{4}-\d{2}-\d{2}$/.test(text)
        ? new Date(`${text}T12:00:00`)
        : new Date(text);
    if (!Number.isNaN(parsed.getTime())) {
        return parsed.toLocaleDateString(undefined, {
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        });
    }
    return text;
}

function formatHardcoverPublishedText(metadata, { preferYearOnly = false } = {}) {
    const objectType = String(metadata?.object_type || '').trim().toLowerCase();
    const releaseDate = String(metadata?.release_date || '').trim();
    const hasYear = !!metadata?.release_year;

    if (objectType === 'series' && releaseDate) {
        const parts = releaseDate.split(/\s+to\s+/i).map((part) => {
            const yearMatch = String(part || '').trim().match(/^(\d{4})/);
            return yearMatch ? yearMatch[1] : formatHardcoverDate(part);
        }).filter(Boolean);
        if (parts.length >= 2) {
            return `${parts[0]} - ${parts[parts.length - 1]}`;
        }
        if (parts.length === 1) {
            return parts[0];
        }
    }

    if (preferYearOnly && hasYear) {
        return String(metadata.release_year);
    }

    return formatHardcoverDate(releaseDate) || (hasYear ? String(metadata.release_year) : '');
}

function formatHardcoverCount(value) {
    const count = Number(value);
    if (!Number.isFinite(count) || count < 0) return '';
    return count.toLocaleString();
}

function uniqueHardcoverStrings(values) {
    if (!Array.isArray(values)) return [];
    const seen = new Set();
    const items = [];
    values.forEach((value) => {
        const text = String(value || '').trim();
        if (!text || seen.has(text)) return;
        seen.add(text);
        items.push(text);
    });
    return items;
}

function hardcoverSeriesLabel(metadata) {
    const featuredSeries = metadata?.featured_series;
    if (featuredSeries && typeof featuredSeries === 'object') {
        const name = String(featuredSeries.name || featuredSeries?.series?.name || '').trim();
        const position = Number(featuredSeries.position);
        if (name) {
            return Number.isFinite(position) && position > 0 ? `${name} #${position}` : name;
        }
    }
    return uniqueHardcoverStrings(metadata?.series_names)[0] || '';
}

function renderHardcoverBadges(values, limit = 6) {
    return uniqueHardcoverStrings(values)
        .slice(0, limit)
        .map((value) => `<span class="badge rounded-pill bg-secondary-subtle text-secondary-emphasis border fw-normal">${escapeHtml(value)}</span>`)
        .join('');
}

function currentDetailItemSupportsPages() {
    const bookModalEl = document.getElementById('bookDetailsModal');
    const mainCat = String(bookModalEl?.dataset.currentMainCat || '').trim();
    const filetype = String(bookModalEl?.dataset.currentFiletype || '').trim().toLowerCase();

    if (mainCat === '13') return false;
    if (!filetype) return true;

    return !/\b(mp3|m4b|m4a|aac|flac|ogg|opus|aax|audio)\b/.test(filetype);
}

let detailColumnBalanceFrame = 0;

function normalizeCollapsedHeight(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function getExpandableDefaultCollapsedHeight(element, fallback = 0) {
    return normalizeCollapsedHeight(element?.dataset.collapsedHeight, fallback);
}

function getExpandableActiveCollapsedHeight(element) {
    return normalizeCollapsedHeight(
        element?.dataset.activeCollapsedHeight,
        normalizeCollapsedHeight(element?.dataset.defaultCollapsedHeight, 0)
    );
}

function syncExpandableBalanceState(element) {
    if (!element) return;
    const isBalancedOpen = element.classList.contains('is-collapsible')
        && !element.classList.contains('is-expanded')
        && element.scrollHeight <= getExpandableActiveCollapsedHeight(element) + 1;
    element.classList.toggle('is-balanced-open', isBalancedOpen);
    if (element.classList.contains('is-expanded') || isBalancedOpen || !element.classList.contains('is-collapsible')) {
        element.removeAttribute('role');
        element.tabIndex = -1;
        element.title = '';
    } else {
        element.setAttribute('role', 'button');
        element.tabIndex = 0;
        element.title = 'Click to expand';
    }
}

function setExpandableCollapsedHeight(element, height) {
    if (!element) return;
    const nextHeight = Math.max(0, Math.ceil(normalizeCollapsedHeight(height, 0)));
    element.dataset.activeCollapsedHeight = String(nextHeight);
    element.style.setProperty('--detail-collapsed-max-height', `${nextHeight}px`);
    syncExpandableBalanceState(element);
}

function initializeExpandableCollapsedHeight(element, fallback = 0) {
    if (!element) return 0;
    const defaultHeight = getExpandableDefaultCollapsedHeight(element, fallback);
    element.dataset.defaultCollapsedHeight = String(defaultHeight);
    setExpandableCollapsedHeight(element, defaultHeight);
    return defaultHeight;
}

function resetExpandableCollapsedHeight(element) {
    if (!element) return;
    const defaultHeight = normalizeCollapsedHeight(element.dataset.defaultCollapsedHeight, 0);
    if (!defaultHeight) return;
    setExpandableCollapsedHeight(element, defaultHeight);
}

function isBookDetailsDesktopColumnsVisible() {
    const leftColumn = document.getElementById('detail-primary-column');
    const rightColumn = document.getElementById('detail-secondary-column');
    const leftContent = document.getElementById('detail-primary-column-content');
    const rightContent = document.getElementById('detail-secondary-column-content');
    if (!leftColumn || !rightColumn || !leftContent || !rightContent) return false;
    if (window.matchMedia && !window.matchMedia('(min-width: 992px)').matches) return false;

    const leftRect = leftColumn.getBoundingClientRect();
    const rightRect = rightColumn.getBoundingClientRect();
    const leftContentRect = leftContent.getBoundingClientRect();
    const rightContentRect = rightContent.getBoundingClientRect();
    if (leftRect.width <= 0 || rightRect.width <= 0) return false;
    if (leftContentRect.width <= 0 || rightContentRect.width <= 0) return false;

    return Math.abs(leftRect.top - rightRect.top) < 8;
}

function balanceBookDetailsColumns() {
    const bookModalEl = document.getElementById('bookDetailsModal');
    const leftColumn = document.getElementById('detail-primary-column-content');
    const rightColumn = document.getElementById('detail-secondary-column-content');
    if (!bookModalEl || !leftColumn || !rightColumn) return;

    const candidates = [
        ...leftColumn.querySelectorAll('[data-balance-expandable="true"]'),
        ...rightColumn.querySelectorAll('[data-balance-expandable="true"]'),
    ];

    candidates.forEach((element) => {
        if (!element.classList.contains('is-expanded')) {
            resetExpandableCollapsedHeight(element);
        } else {
            syncExpandableBalanceState(element);
        }
    });

    if (!bookModalEl.classList.contains('show') || !isBookDetailsDesktopColumnsVisible()) {
        return;
    }

    const tolerance = 4;
    const leftHeight = leftColumn.getBoundingClientRect().height;
    const rightHeight = rightColumn.getBoundingClientRect().height;
    if (Math.abs(leftHeight - rightHeight) <= tolerance) return;

    const shorterColumn = leftHeight < rightHeight ? leftColumn : rightColumn;
    const tallerHeight = Math.max(leftHeight, rightHeight);
    const shorterCandidates = [...shorterColumn.querySelectorAll('[data-balance-expandable="true"]')]
        .filter((element) => (
            element.classList.contains('is-collapsible')
            && !element.classList.contains('is-expanded')
            && element.getClientRects().length
        ));

    shorterCandidates.forEach((element) => {
        const shortage = tallerHeight - shorterColumn.getBoundingClientRect().height;
        if (shortage <= tolerance) return;

        const currentHeight = getExpandableActiveCollapsedHeight(element);
        const availableGrowth = Math.max(0, element.scrollHeight - currentHeight);
        if (availableGrowth <= tolerance) {
            syncExpandableBalanceState(element);
            return;
        }

        setExpandableCollapsedHeight(element, currentHeight + Math.min(shortage, availableGrowth));
    });
}

function scheduleBookDetailsColumnBalance() {
    if (detailColumnBalanceFrame) cancelAnimationFrame(detailColumnBalanceFrame);
    detailColumnBalanceFrame = requestAnimationFrame(() => {
        detailColumnBalanceFrame = 0;
        balanceBookDetailsColumns();
    });
}

function configureExpandableTextBlock(element, {
    html,
    text,
    collapsedHeight,
    preserveExpanded = false,
    onExpand = null,
} = {}) {
    if (!element) return;

    if (html !== undefined) {
        element.dataset.expandableHtml = String(html || '');
        element.innerHTML = html;
    } else if (text !== undefined) {
        element.textContent = String(text || '');
    }

    const defaultCollapsedHeight = initializeExpandableCollapsedHeight(element, collapsedHeight);
    element.classList.remove('is-expanded', 'is-collapsible', 'is-balanced-open');
    element.setAttribute('aria-expanded', 'false');
    element.removeAttribute('role');
    element.tabIndex = -1;
    element.title = '';
    element.onclick = null;
    element.onkeydown = null;

    const hasContent = String(element.textContent || '').trim().length > 0;
    if (!hasContent) return;
    if (!element.getClientRects().length) return;

    const isCollapsible = element.scrollHeight > defaultCollapsedHeight + 1;
    if (!isCollapsible) return;

    element.classList.add('is-collapsible');
    if (preserveExpanded) {
        element.classList.add('is-expanded');
        element.setAttribute('aria-expanded', 'true');
        syncExpandableBalanceState(element);
        return;
    }

    element.setAttribute('role', 'button');
    element.setAttribute('aria-expanded', 'false');
    element.tabIndex = 0;
    syncExpandableBalanceState(element);
    element.onclick = (event) => {
        if (element.classList.contains('is-expanded') || element.classList.contains('is-balanced-open')) return;
        event.preventDefault();
        event.stopPropagation();
        element.classList.add('is-expanded');
        element.classList.remove('is-balanced-open');
        element.setAttribute('aria-expanded', 'true');
        element.title = '';
        if (typeof onExpand === 'function') onExpand(element);
    };
    element.onkeydown = (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        element.click();
    };
}

function configureExpandableDetailDescription(element, html) {
    configureExpandableTextBlock(element, {
        html,
        collapsedHeight: 350,
        onExpand: () => scheduleBookDetailsColumnBalance(),
    });
}

function configureExpandableHardcoverDescription(element, text, preserveExpanded = false) {
    configureExpandableTextBlock(element, {
        text,
        collapsedHeight: 96,
        preserveExpanded,
        onExpand: () => {
            const bookModalEl = document.getElementById('bookDetailsModal');
            if (bookModalEl) bookModalEl.dataset.hardcoverDescriptionExpanded = 'true';
            scheduleBookDetailsColumnBalance();
        },
    });
}

function clearHardcoverSeriesStrip() {
    const section = document.getElementById('detail-hc-series-strip-section');
    const meta = document.getElementById('detail-hc-series-strip-meta');
    const strip = document.getElementById('detail-hc-series-strip');
    if (meta) meta.textContent = '';
    if (strip) strip.innerHTML = '';
    if (section) section.classList.add('d-none');
}

function hardcoverSeriesLink(slug) {
    const normalized = String(slug || '').trim();
    return normalized ? `https://hardcover.app/series/${encodeURIComponent(normalized)}` : '';
}

function hardcoverBookLink(slug) {
    const normalized = String(slug || '').trim();
    return normalized ? `https://hardcover.app/books/${encodeURIComponent(normalized)}` : '';
}

function hardcoverReviewsLink(slug) {
    const normalized = String(slug || '').trim();
    return normalized ? `https://hardcover.app/books/${encodeURIComponent(normalized)}/reviews` : '';
}

function hardcoverAuthorLink(slug) {
    const normalized = String(slug || '').trim();
    return normalized ? `https://hardcover.app/authors/${encodeURIComponent(normalized)}` : '';
}

function buildMouseSearchUrlForTitle(title) {
    const normalizedTitle = String(title || '').trim();
    if (!normalizedTitle) {
        return window.location.pathname || '/';
    }

    const searchForm = document.getElementById('search-form');
    const params = searchForm
        ? new URLSearchParams(new FormData(searchForm))
        : new URLSearchParams(window.location.search);

    params.set('query', normalizedTitle);
    params.set('search_in_title', 'on');
    [
        'search_in_author',
        'search_in_series',
        'search_in_narrator',
        'search_in_description',
        'search_in_tags',
        'search_in_filenames'
    ].forEach((key) => params.delete(key));

    const queryString = params.toString();
    return queryString ? `${window.location.pathname}?${queryString}` : (window.location.pathname || '/');
}

function formatHardcoverSeriesPosition(value) {
    const position = Number(value);
    if (!Number.isFinite(position)) return '';
    return Number.isInteger(position) ? `#${position}` : `#${position.toString().replace(/\.0+$/, '')}`;
}

function normalizeHardcoverSeriesPosition(value) {
    const position = Number(value);
    return Number.isFinite(position) ? position : null;
}

function isPrimaryHardcoverSeriesPosition(value) {
    const position = normalizeHardcoverSeriesPosition(value);
    if (position === null) return false;
    const doubled = position * 2;
    return Math.abs(doubled - Math.round(doubled)) < 0.0001;
}

function filterHardcoverSeriesEntries(entries, currentBookId, currentPosition) {
    const list = Array.isArray(entries) ? entries : [];
    const bookIdKey = String(currentBookId || '').trim();
    const currentIsPrimaryLane = currentPosition === null || isPrimaryHardcoverSeriesPosition(currentPosition);
    if (!currentIsPrimaryLane) {
        return list;
    }

    const filtered = list.filter((entry) => {
        const entryBookId = String(entry?.book?.id || '').trim();
        if (bookIdKey && entryBookId === bookIdKey) {
            return true;
        }
        const position = normalizeHardcoverSeriesPosition(entry?.position);
        return position === null || isPrimaryHardcoverSeriesPosition(position);
    });

    return filtered.length ? filtered : list;
}

function scrollHardcoverSeriesStripToCurrent(strip) {
    if (!strip) return;
    const currentCard = strip.querySelector('.hardcover-series-card--current');
    if (!currentCard) return;

    const stripRect = strip.getBoundingClientRect();
    const cardRect = currentCard.getBoundingClientRect();
    const isFullyVisible = cardRect.left >= stripRect.left && cardRect.right <= stripRect.right;
    if (isFullyVisible) return;

    const centeredLeft = currentCard.offsetLeft - Math.max(0, (strip.clientWidth - currentCard.offsetWidth) / 2);
    strip.scrollTo({
        left: Math.max(0, centeredLeft),
        behavior: 'smooth',
    });
}

function renderHardcoverSeriesStrip(series, currentBookId, currentPosition) {
    const section = document.getElementById('detail-hc-series-strip-section');
    const meta = document.getElementById('detail-hc-series-strip-meta');
    const strip = document.getElementById('detail-hc-series-strip');
    if (!section || !meta || !strip) return;

    const entries = filterHardcoverSeriesEntries(series?.book_series, currentBookId, currentPosition);
    if (!entries.length) {
        clearHardcoverSeriesStrip();
        return;
    }

    const seriesName = String(series?.name || '').trim();
    const authorName = String(series?.author?.name || '').trim();
    const authorUrl = hardcoverAuthorLink(series?.author?.slug);
    const seriesUrl = hardcoverSeriesLink(series?.slug);
    if (seriesUrl && seriesName) {
        meta.innerHTML = `<a href="${escapeHtml(seriesUrl)}" target="_blank" rel="noopener noreferrer" class="link-secondary text-decoration-none hover-primary">${escapeHtml(seriesName)}</a>${authorName ? ` <span class="text-body-secondary">by </span>${authorUrl ? `<a href="${escapeHtml(authorUrl)}" target="_blank" rel="noopener noreferrer" class="link-secondary text-decoration-none hover-primary">${escapeHtml(authorName)}</a>` : `<span class="text-body-secondary">${escapeHtml(authorName)}</span>`}` : ''}`;
    } else {
        meta.innerHTML = authorName
            ? `${escapeHtml(seriesName)} <span class="text-body-secondary">by </span>${authorUrl ? `<a href="${escapeHtml(authorUrl)}" target="_blank" rel="noopener noreferrer" class="link-secondary text-decoration-none hover-primary">${escapeHtml(authorName)}</a>` : `<span class="text-body-secondary">${escapeHtml(authorName)}</span>`}`
            : escapeHtml(seriesName);
    }

    const currentBookKey = String(currentBookId || '').trim();
    strip.innerHTML = entries.map((entry) => {
        const book = entry?.book || {};
        const bookId = String(book.id || '').trim();
        const title = String(book.title || '').trim() || 'Unknown title';
        const slug = String(book.slug || '').trim();
        const hardcoverUrl = hardcoverBookLink(slug);
        const mouseSearchUrl = buildMouseSearchUrlForTitle(title);
        const coverUrl = String(book.image_url || '').trim();
        const proxyCoverUrl = coverUrl ? `${APP_BASE}/proxy_thumbnail?url=${encodeURIComponent(coverUrl)}` : `${APP_BASE}/static/icons/no_cover.png`;
        const positionLabel = formatHardcoverSeriesPosition(entry?.position);
        const releaseYear = String(book.release_year || '').trim();
        const isCurrent = currentBookKey && bookId === currentBookKey;

        return `
            <div class="hardcover-series-card${isCurrent ? ' hardcover-series-card--current' : ''}">
                <a class="hardcover-series-card__main-link hardcover-series-search-link text-decoration-none"
                    href="${escapeHtml(mouseSearchUrl)}"
                    data-search-title="${escapeHtml(title)}"
                    ${isCurrent ? 'aria-current="true"' : ''}>
                    <div class="hardcover-series-card__frame">
                        ${positionLabel ? `<span class="hardcover-series-card__position">${escapeHtml(positionLabel)}</span>` : ''}
                        ${isCurrent ? '<span class="hardcover-series-card__current">Current</span>' : ''}
                        <img class="hardcover-series-card__cover" src="${escapeHtml(proxyCoverUrl)}" alt="${escapeHtml(title)}" loading="lazy">
                    </div>
                </a>
                <div class="hardcover-series-card__actions">
                    <a class="hardcover-series-card__action hardcover-series-card__action--search hardcover-series-search-link"
                        href="${escapeHtml(mouseSearchUrl)}"
                        aria-label="Search MouseSearch for ${escapeHtml(title)}"
                        data-search-title="${escapeHtml(title)}">
                        <img src="${escapeHtml(MOUSESEARCH_LOGO_URL)}" alt="" loading="lazy">
                    </a>
                    ${hardcoverUrl ? `
                        <a class="hardcover-series-card__action hardcover-series-card__action--hardcover"
                            href="${escapeHtml(hardcoverUrl)}"
                            aria-label="Open ${escapeHtml(title)} on Hardcover"
                            target="_blank" rel="noopener noreferrer">
                            <img src="${escapeHtml(HARDCOVER_LOGO_URL)}" alt="" loading="lazy">
                        </a>` : ''}
                </div>
            </div>`;
    }).join('');

    section.classList.remove('d-none');
    scrollHardcoverSeriesStripToCurrent(strip);
}

async function loadHardcoverSeriesStrip(metadata) {
    const bookModalEl = document.getElementById('bookDetailsModal');
    const seriesId = Number(metadata?.series_id ?? metadata?.featured_series?.id);
    const currentBookId = String(metadata?.book_id || '').trim();
    const currentPosition = normalizeHardcoverSeriesPosition(metadata?.featured_series?.position);
    if (!bookModalEl || !Number.isFinite(seriesId) || seriesId <= 0) {
        clearHardcoverSeriesStrip();
        if (bookModalEl) bookModalEl.dataset.currentHardcoverSeriesId = '';
        return;
    }

    bookModalEl.dataset.currentHardcoverSeriesId = String(seriesId);
    const cached = hardcoverSeriesCache.get(String(seriesId));
    if (cached) {
        renderHardcoverSeriesStrip(cached, currentBookId, currentPosition);
        return;
    }

    const section = document.getElementById('detail-hc-series-strip-section');
    const strip = document.getElementById('detail-hc-series-strip');
    const meta = document.getElementById('detail-hc-series-strip-meta');
    if (meta) meta.textContent = 'Loading series...';
    if (strip) strip.innerHTML = '<div class="text-body-secondary small py-2">Loading series…</div>';
    if (section) section.classList.remove('d-none');

    try {
        const response = await fetch(`${APP_BASE}/hardcover/series/${encodeURIComponent(String(seriesId))}`);
        if (!response.ok) {
            throw new Error(`Hardcover series HTTP ${response.status}`);
        }
        const payload = await response.json();
        const series = Array.isArray(payload?.series) ? payload.series[0] : null;
        if (!series) {
            clearHardcoverSeriesStrip();
            return;
        }
        hardcoverSeriesCache.set(String(seriesId), series);
        if (bookModalEl.dataset.currentHardcoverSeriesId !== String(seriesId)) {
            return;
        }
        renderHardcoverSeriesStrip(series, currentBookId, currentPosition);
    } catch (error) {
        console.error('Hardcover series load error', error);
        clearHardcoverSeriesStrip();
    }
}

function renderBookDetailsHardcover(enrichment) {
    const card = document.getElementById('detail-hardcover-card');
    const heroInfo = document.getElementById('detail-hero-hc-info');
    const bookModalEl = document.getElementById('bookDetailsModal');

    const metadata = enrichment?.hardcover;
    if (!metadata) {
        if (card) card.style.display = 'none';
        if (heroInfo) heroInfo.classList.add('d-none');
        const heroActions = document.getElementById('detail-hero-hc-actions');
        if (heroActions) {
            heroActions.innerHTML = '';
            heroActions.classList.add('d-none');
        }
        clearHardcoverSeriesStrip();
        scheduleBookDetailsColumnBalance();
        return;
    }

    const rating = Number(metadata.rating);
    const hasRating = Number.isFinite(rating) && rating > 0;
    const objectType = String(metadata?.object_type || '').trim().toLowerCase();
    const isSeriesMatch = objectType === 'series';
    const publishedText = formatHardcoverPublishedText(metadata);
    const pagesText = currentDetailItemSupportsPages() ? formatHardcoverCount(metadata.pages) : '';
    const readersText = formatHardcoverCount(metadata.users_read_count ?? metadata.users_count);
    const titleHtml = renderHardcoverTitleHtml(metadata);
    const authorHtml = renderHardcoverAuthorHtml(metadata);
    const seriesHtml = renderHardcoverSeriesHtml(metadata);
    const subtitleText = String(metadata.subtitle || '').trim();
    const descriptionText = String(metadata.description || '').trim();
    const genreBadges = renderHardcoverBadges(metadata.genres, 6);
    const moodBadges = renderHardcoverBadges(metadata.moods, 5);
    const reviewsUrl = hardcoverReviewsLink(metadata.slug);

    // --- Sidebar card ---
    if (card) {
        card.style.display = '';
        const toggleDetailRow = (row, isVisible) => {
            if (!row) return;
            row.classList.toggle('d-none', !isVisible);
        };
        const setDetailRow = (rowId, valueId, value) => {
            const row = document.getElementById(rowId);
            const el = document.getElementById(valueId);
            const hasValue = String(value || '').trim().length > 0;
            if (el) el.textContent = hasValue ? value : '';
            toggleDetailRow(row, hasValue);
        };
        const setDetailRowHtml = (rowId, valueId, html) => {
            const row = document.getElementById(rowId);
            const el = document.getElementById(valueId);
            const hasContent = String(html || '').trim().length > 0;
            if (el) el.innerHTML = hasContent ? html : '';
            toggleDetailRow(row, hasContent);
        };
        const setDetailBlockHtml = (blockId, valueId, html) => {
            const block = document.getElementById(blockId);
            const el = document.getElementById(valueId);
            const hasContent = String(html || '').trim().length > 0;
            if (el) el.innerHTML = hasContent ? html : '';
            if (block) block.classList.toggle('d-none', !hasContent);
        };
        const subtitleBlock = document.getElementById('detail-hc-subtitle-block');
        const subtitleEl = document.getElementById('detail-hc-subtitle');
        const descriptionBlock = document.getElementById('detail-hc-description-block');
        const descriptionEl = document.getElementById('detail-hc-description');
        const titleLabel = document.getElementById('detail-hc-title-label');
        const preserveHardcoverDescription = bookModalEl?.dataset.hardcoverDescriptionExpanded === 'true';
        if (titleLabel) titleLabel.textContent = isSeriesMatch ? 'Series:' : 'Title:';
        setDetailRowHtml('detail-hc-title-row', 'detail-hc-title', titleHtml);
        setDetailRowHtml('detail-hc-authors-row', 'detail-hc-authors', authorHtml);
        const ratingRow = document.getElementById('detail-hc-rating-row');
        const ratingEl = document.getElementById('detail-hc-rating');
        if (hasRating) {
            ratingEl.innerHTML = renderStarRating(rating, metadata.ratings_count, {
                countLinkUrl: reviewsUrl,
                wrapWithLink: true,
            });
            toggleDetailRow(ratingRow, true);
        } else {
            toggleDetailRow(ratingRow, false);
        }

        const yearRow = document.getElementById('detail-hc-year-row');
        const yearEl = document.getElementById('detail-hc-year');
        if (publishedText) {
            yearEl.textContent = publishedText;
            toggleDetailRow(yearRow, true);
        } else {
            toggleDetailRow(yearRow, false);
        }

        setDetailRow('detail-hc-pages-row', 'detail-hc-pages', pagesText);
        setDetailRow('detail-hc-readers-row', 'detail-hc-readers', readersText);
        setDetailRowHtml('detail-hc-series-row', 'detail-hc-series', isSeriesMatch ? '' : seriesHtml);

        if (subtitleEl) subtitleEl.textContent = subtitleText;
        if (subtitleBlock) subtitleBlock.classList.toggle('d-none', !subtitleText);
        if (descriptionEl) {
            configureExpandableHardcoverDescription(descriptionEl, descriptionText, preserveHardcoverDescription);
        }
        if (descriptionBlock) descriptionBlock.classList.toggle('d-none', !descriptionText);

        setDetailBlockHtml('detail-hc-genres-block', 'detail-hc-genres', genreBadges);
        setDetailBlockHtml('detail-hc-moods-block', 'detail-hc-moods', moodBadges);

        const linkContainer = document.getElementById('detail-hc-link-container');
        if (linkContainer) {
            const url = hardcoverUrl(metadata);
            const statusHtml = renderHardcoverStatusPicker(metadata, {
                torrentId: String(bookModalEl?.dataset.currentTorrentId || ''),
                variant: 'full',
            });
            const actions = [];
            if (statusHtml) actions.push(statusHtml);
            if (url) {
                actions.push(`<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="btn btn-sm btn-outline-secondary">View on Hardcover <i class="bi bi-box-arrow-up-right ms-1"></i></a>`);
            }
            linkContainer.innerHTML = actions.length
                ? `<div class="hardcover-card-actions">${actions.join('')}</div>`
                : '';
        }

    }

    scheduleBookDetailsColumnBalance();
    loadHardcoverSeriesStrip(metadata);

    // --- Hero area ---
    if (heroInfo) {
        const heroCard = document.getElementById('detail-hero-hc-link');
        const heroLink = document.getElementById('detail-hero-hc-link-main');
        const heroRating = document.getElementById('detail-hero-hc-rating');
        const heroYear = document.getElementById('detail-hero-hc-year');
        const heroActions = document.getElementById('detail-hero-hc-actions');

        const url = hardcoverUrl(metadata);
        if (heroLink) {
            heroLink.href = url || '#';
            heroLink.style.pointerEvents = url ? '' : 'none';
            heroLink.style.cursor = url ? '' : 'default';
            
            const author = firstListedName(metadata.authors);
            const title = metadata.title || 'Unknown Title';
            const tooltipText = `<div class='text-start'><strong>Title:</strong> ${escapeHtml(title)}<br><strong>Author:</strong> ${escapeHtml(author)}</div>`;
            
            heroLink.setAttribute('data-bs-toggle', 'tooltip');
            heroLink.setAttribute('data-bs-html', 'true');
            heroLink.setAttribute('title', tooltipText);
            heroLink.setAttribute('data-bs-original-title', tooltipText);
            
            const existingTooltip = bootstrap.Tooltip.getInstance(heroLink);
            if (existingTooltip) {
                existingTooltip.dispose();
            }
            new bootstrap.Tooltip(heroLink);
        }

        let heroStatusHtml = '';
        if (heroActions) {
            heroStatusHtml = renderHardcoverStatusPicker(metadata, {
                torrentId: String(bookModalEl?.dataset.currentTorrentId || ''),
                variant: 'compact',
            });
            heroActions.innerHTML = heroStatusHtml || '';
            heroActions.classList.toggle('d-none', !heroStatusHtml);
        }

        if (heroRating) {
            heroRating.innerHTML = hasRating ? renderStarRating(rating, metadata.ratings_count) : '';
            heroRating.classList.toggle('d-none', !hasRating);
        }
        if (heroYear) {
            const heroPublishedText = formatHardcoverPublishedText(metadata, { preferYearOnly: true });
            heroYear.textContent = heroPublishedText ? `Published ${heroPublishedText}` : '';
            heroYear.classList.toggle('d-none', !heroPublishedText);
        }

        if (heroCard) {
            heroCard.classList.toggle('d-none', !(hasRating || formatHardcoverPublishedText(metadata, { preferYearOnly: true }) || heroStatusHtml));
        }
        heroInfo.classList.toggle('d-none', !(hasRating || formatHardcoverPublishedText(metadata, { preferYearOnly: true }) || heroStatusHtml));
    }
}

function hasOpenHardcoverStatusPicker() {
    return !!document.querySelector('[data-hardcover-status-picker].is-open');
}

function flushPendingHardcoverEnrichmentUpdates() {
    if (hasOpenHardcoverStatusPicker() || pendingHardcoverEnrichmentPayloads.size === 0) return;
    const queuedPayloads = [...pendingHardcoverEnrichmentPayloads.values()];
    pendingHardcoverEnrichmentPayloads.clear();
    queuedPayloads.forEach(applyHardcoverEnrichmentUpdate);
}

function applyHardcoverEnrichmentUpdate(payload) {
    const torrentId = String(payload?.torrent_id || '');
    const searchId = String(payload?.search_id || '');
    const enrichment = payload?.enrichment;
    if (!torrentId || !enrichment) return;

    const escapedTorrentId = window.CSS && CSS.escape
        ? CSS.escape(torrentId)
        : torrentId.replace(/["\\]/g, '\\$&');
    const resultItem = document.querySelector(`.result-item[data-torrent-id="${escapedTorrentId}"]`);
    if (!resultItem) return;
    if (searchId && resultItem.dataset.searchId && resultItem.dataset.searchId !== searchId) return;

    resultItem.dataset.hardcoverState = enrichment.hardcover ? 'matched' : 'unresolved';
    resultItem.dataset.hardcoverScore = String(enrichment.match_score || 0);
    resultItem.dataset.hardcoverPath = enrichment.query_path || '';
    const observer = hardcoverEnrichmentObservers.get(searchId);
    if (observer) observer.unobserve(resultItem);
    updateResultJsonData(resultItem, { hardcover_enrichment: enrichment });

    const container = resultItem.querySelector('[data-hardcover-container]');
    if (container) {
        // Destroy existing tooltip if any
        const existingTooltipEl = container.querySelector('[data-bs-toggle="tooltip"]');
        if (existingTooltipEl) {
            const instance = bootstrap.Tooltip.getInstance(existingTooltipEl);
            if (instance) instance.dispose();
        }
        
        container.innerHTML = renderHardcoverMetadata(enrichment, { torrentId });
        
        const newTooltipEl = container.querySelector('[data-bs-toggle="tooltip"]');
        if (newTooltipEl) {
            new bootstrap.Tooltip(newTooltipEl);
        }
    }

    const coverUrl = enrichment?.hardcover?.cover_image;
    const hasMamCover = resultItem.dataset.hasMamCover === 'true';
    if (coverUrl && !hasMamCover) {
        const thumb = resultItem.querySelector('.results-thumb');
        if (thumb) {
            thumb.dataset.triedHardcoverCover = 'true';
            thumb.src = `${APP_BASE}/proxy_thumbnail?url=${encodeURIComponent(coverUrl)}`;
        }
    }

    // If the book details modal is open for this torrent, update its Hardcover section live
    const bookModalEl = document.getElementById('bookDetailsModal');
    if (bookModalEl && bookModalEl.classList.contains('show') && bookModalEl.dataset.currentTorrentId === torrentId) {
        renderBookDetailsHardcover(enrichment);
    }
}

function updateHardcoverEnrichment(payload) {
    const torrentId = String(payload?.torrent_id || '');
    if (hasOpenHardcoverStatusPicker()) {
        if (torrentId) pendingHardcoverEnrichmentPayloads.set(torrentId, payload);
        return;
    }
    applyHardcoverEnrichmentUpdate(payload);
}

function escapeHardcoverSearchId(searchId) {
    return window.CSS && CSS.escape
        ? CSS.escape(searchId)
        : String(searchId).replace(/["\\]/g, '\\$&');
}

function hardcoverRowsForSearch(searchId, scope = document) {
    const escapedSearchId = escapeHardcoverSearchId(searchId);
    return [...scope.querySelectorAll(`.result-item[data-search-id="${escapedSearchId}"][data-hardcover-state]`)];
}

function hasPendingHardcoverRows(searchId) {
    const escapedSearchId = escapeHardcoverSearchId(searchId);
    return !!document.querySelector(`.result-item[data-search-id="${escapedSearchId}"][data-hardcover-state="pending"]`);
}

function markPendingHardcoverRows(searchId, reason = 'enrichment_unavailable') {
    const escapedSearchId = escapeHardcoverSearchId(searchId);
    document.querySelectorAll(`.result-item[data-search-id="${escapedSearchId}"][data-hardcover-state="pending"]`).forEach(item => {
        updateHardcoverEnrichment({
            search_id: searchId,
            torrent_id: item.dataset.torrentId,
            enrichment: {
                hardcover: null,
                match_score: 0,
                query_path: 'failed',
                failure_reason: reason
            }
        });
    });
}

function stopHardcoverEnrichmentPolling() {
    hardcoverEnrichmentPollers.forEach(timerId => clearInterval(timerId));
    hardcoverEnrichmentPollers.clear();
    hardcoverEnrichmentObservers.forEach(observer => observer.disconnect());
    hardcoverEnrichmentObservers.clear();
    hardcoverEnrichmentQueueRequests.clear();
}

async function pollHardcoverEnrichment(searchId) {
    try {
        const response = await fetch(`${APP_BASE}/hardcover/enrichment/${encodeURIComponent(searchId)}`, {
            cache: 'no-store'
        });
        if (!response.ok) throw new Error(`Hardcover poll HTTP ${response.status}`);

        const data = await response.json();
        const results = Array.isArray(data.results) ? data.results : [];
        results.forEach(item => {
            updateHardcoverEnrichment({
                event: 'hardcover-enrichment',
                search_id: data.search_id || searchId,
                torrent_id: item.torrent_id,
                index: item.index,
                enrichment: item.enrichment
            });
        });

        if (data.completed && hasPendingHardcoverRows(searchId)) {
            markPendingHardcoverRows(searchId, data.error || 'no_enrichment_result');
        }

        if (data.completed || !hasPendingHardcoverRows(searchId)) {
            const timerId = hardcoverEnrichmentPollers.get(searchId);
            if (timerId) clearInterval(timerId);
            hardcoverEnrichmentPollers.delete(searchId);
        }
    } catch (error) {
        console.warn('[HARDCOVER] Polling failed:', error);
    }
}

function ensureHardcoverEnrichmentPolling(searchId) {
    if (hardcoverEnrichmentPollers.has(searchId)) return;
    pollHardcoverEnrichment(searchId);
    const timerId = setInterval(() => pollHardcoverEnrichment(searchId), 1000);
    hardcoverEnrichmentPollers.set(searchId, timerId);
}

function observeDeferredHardcoverRows(searchId, scope = document) {
    const deferredRows = hardcoverRowsForSearch(searchId, scope).filter(item => item.dataset.hardcoverState === 'deferred');
    if (!deferredRows.length) {
        const existingObserver = hardcoverEnrichmentObservers.get(searchId);
        if (existingObserver) {
            existingObserver.disconnect();
            hardcoverEnrichmentObservers.delete(searchId);
        }
        return;
    }

    let observer = hardcoverEnrichmentObservers.get(searchId);
    if (!observer) {
        observer = new IntersectionObserver(entries => {
            entries.forEach(entry => {
                if (!entry.isIntersecting) return;
                const target = entry.target;
                const rows = hardcoverRowsForSearch(searchId);
                const index = rows.indexOf(target);
                if (index < 0) return;
                const start = Math.max(0, index - HARDCOVER_LAZY_BUFFER_ROWS);
                const end = Math.min(rows.length - 1, index + HARDCOVER_LAZY_BUFFER_ROWS);
                queueHardcoverEnrichmentRows(searchId, rows.slice(start, end + 1));
            });
        }, { threshold: 0.01 });
        hardcoverEnrichmentObservers.set(searchId, observer);
    }

    deferredRows.forEach(item => observer.observe(item));
}

async function queueHardcoverEnrichmentRows(searchId, rows) {
    const itemsToQueue = rows.filter(item => item && item.dataset.hardcoverState === 'deferred');
    if (!itemsToQueue.length) return;

    const torrentIds = [...new Set(itemsToQueue
        .map(item => String(item.dataset.torrentId || '').trim())
        .filter(Boolean)
    )];
    if (!torrentIds.length) return;

    itemsToQueue.forEach(item => {
        item.dataset.hardcoverState = 'pending';
    });

    const previousRequest = hardcoverEnrichmentQueueRequests.get(searchId) || Promise.resolve();
    const queueRequest = previousRequest
        .catch(() => undefined)
        .then(async () => {
            const response = await fetch(`${APP_BASE}/hardcover/enrichment/${encodeURIComponent(searchId)}/queue`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ torrent_ids: torrentIds }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.status !== 'success') {
                throw new Error(data.message || `Hardcover queue request failed (HTTP ${response.status})`);
            }
            ensureHardcoverEnrichmentPolling(searchId);
        })
        .catch(error => {
            console.warn('[HARDCOVER] Queueing failed:', error);
            itemsToQueue.forEach(item => {
                if (item.dataset.hardcoverState === 'pending') {
                    item.dataset.hardcoverState = 'deferred';
                }
            });
        })
        .finally(() => {
            if (hardcoverEnrichmentQueueRequests.get(searchId) === queueRequest) {
                hardcoverEnrichmentQueueRequests.delete(searchId);
            }
            observeDeferredHardcoverRows(searchId);
        });

    hardcoverEnrichmentQueueRequests.set(searchId, queueRequest);
    await queueRequest;
}

function queueInitialHardcoverRows(searchId, scope = document) {
    const rows = hardcoverRowsForSearch(searchId, scope);
    if (!rows.length) return;

    const visibleIndexes = [];
    rows.forEach((item, index) => {
        const rect = item.getBoundingClientRect();
        if (rect.bottom > 0 && rect.top < window.innerHeight) {
            visibleIndexes.push(index);
        }
    });

    const start = visibleIndexes.length
        ? Math.max(0, visibleIndexes[0] - HARDCOVER_LAZY_BUFFER_ROWS)
        : 0;
    const end = visibleIndexes.length
        ? Math.min(rows.length - 1, visibleIndexes[visibleIndexes.length - 1] + HARDCOVER_LAZY_BUFFER_ROWS)
        : Math.min(rows.length - 1, HARDCOVER_LAZY_BUFFER_ROWS);

    queueHardcoverEnrichmentRows(searchId, rows.slice(start, end + 1));
}

function startHardcoverEnrichmentLoading(scope = document) {
    const ids = new Set();
    scope.querySelectorAll('.result-item[data-search-id][data-hardcover-state]').forEach(item => {
        const searchId = String(item.dataset.searchId || '').trim();
        if (searchId && item.dataset.hardcoverState !== 'disabled') ids.add(searchId);
    });

    ids.forEach(searchId => {
        queueInitialHardcoverRows(searchId, scope);
        observeDeferredHardcoverRows(searchId, scope);
    });
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
    const eventSource = new EventSource(`${APP_BASE}/events`);

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
                    const statusMessage = document.getElementById("client-status-message");
                    const statusIconSpan = document.getElementById("client-status-icon");
                    const clientTypeDisplay = document.getElementById('client-type-display');
                    const isConnected = data.status === "connected";
                    const detailMessage = String(data.message || '').trim();

                    if (statusSpan) {
                        statusSpan.textContent = isConnected ? "CONNECTED" : "NOT CONNECTED";
                        statusSpan.className = isConnected ? "text-success" : "text-danger";
                    }
                    if (statusMessage) {
                        if (detailMessage) {
                            statusMessage.textContent = detailMessage;
                            statusMessage.className = isConnected
                                ? "small text-success-emphasis mt-1"
                                : "small text-danger mt-1";
                        } else if (isConnected) {
                            statusMessage.textContent = "";
                            statusMessage.className = "small text-body-secondary mt-1";
                        }
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
                case 'hardcover-enrichment':
                    updateHardcoverEnrichment(data);
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
        const response = await fetch(`${APP_BASE}/client/resolve_mid`, {
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

function getSettingsCategoryOptions() {
    const settingsCategorySelect = document.getElementById('TORRENT_CLIENT_CATEGORY');
    const options = [{ value: '', label: 'Use Global Default' }];
    const seen = new Set(['']);

    if (!settingsCategorySelect) return options;

    Array.from(settingsCategorySelect.options).forEach(option => {
        const value = String(option.value || '').trim();
        const label = String(option.textContent || '').trim();
        if (!value || seen.has(value)) return;
        seen.add(value);
        options.push({ value, label: label || value });
    });

    return options;
}

function getConfiguredTypeCategoryEntries() {
    const typeCategoryRulesList = document.getElementById('type-category-rules-list');

    if (typeCategoryRulesList) {
        return Array.from(typeCategoryRulesList.querySelectorAll('.type-category-row')).map(row => ({
            default_main_cat: String(row.querySelector('.type-category-default-select')?.value || '').trim(),
            default_torrent_category: String(row.querySelector('.type-category-select')?.value || '').trim()
        })).filter(entry => entry.default_main_cat && entry.default_torrent_category);
    }

    return Array.isArray(window.TYPE_SPECIFIC_TORRENT_CATEGORIES)
        ? window.TYPE_SPECIFIC_TORRENT_CATEGORIES
        : [];
}

function getPreferredClientCategoryForMainCat(mainCat = '') {
    const entries = getConfiguredTypeCategoryEntries();
    const mappedDefault = entries.find(entry => String(entry?.default_main_cat || '') === String(mainCat || ''));
    const typeSpecificCategory = String(mappedDefault?.default_torrent_category || '').trim();
    if (typeSpecificCategory) return typeSpecificCategory;
    return String(document.getElementById('TORRENT_CLIENT_CATEGORY')?.value || '').trim();
}

function syncTypeCategoryOptions() {
    const typeCategoryRulesList = document.getElementById('type-category-rules-list');
    if (!typeCategoryRulesList) return;

    const optionData = getSettingsCategoryOptions();

    typeCategoryRulesList.querySelectorAll('.type-category-select').forEach(select => {
        const currentValue = String(select.value || '').trim();
        select.innerHTML = '';

        optionData.forEach(item => {
            const option = document.createElement('option');
            option.value = item.value;
            option.textContent = item.label;
            select.appendChild(option);
        });

        if (currentValue && !Array.from(select.options).some(option => option.value === currentValue)) {
            const option = document.createElement('option');
            option.value = currentValue;
            option.textContent = currentValue;
            select.appendChild(option);
        }

        select.value = currentValue;
    });
}

function checkClientStatus() {
    const statusSpan = document.getElementById("client-status");
    const statusMessage = document.getElementById("client-status-message");
    const statusIconSpan = document.getElementById("client-status-icon");
    const clientTypeDisplay = document.getElementById('client-type-display');

    fetch(`${APP_BASE}/client/status`, { cache: "no-store" })
        .then(response => response.json())
        .then(data => {
            const isSuccess = data.status === "success";
            const detailMessage = String(data.message || '').trim();

            if (statusSpan) {
                statusSpan.textContent = isSuccess ? "CONNECTED" : "NOT CONNECTED";
                statusSpan.className = isSuccess ? "text-success" : "text-danger";
            }
            if (statusMessage) {
                if (detailMessage) {
                    statusMessage.textContent = detailMessage;
                    statusMessage.className = isSuccess
                        ? "small text-success-emphasis mt-1"
                        : "small text-danger mt-1";
                } else {
                    statusMessage.textContent = "";
                    statusMessage.className = "small text-body-secondary mt-1";
                }
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
            if (statusMessage) {
                statusMessage.textContent = error?.message || 'Unable to reach client status endpoint.';
                statusMessage.className = "small text-danger mt-1";
            }
            if (statusIconSpan) statusIconSpan.innerHTML = redXIcon;
        });
}

function refreshCategories() {
    fetch(`${APP_BASE}/client/categories`, { cache: "no-store" })
        .then(response => response.json())
        .then(data => {
            const resultDropdowns = document.querySelectorAll('.category-dropdown');
            const defaultCategory = document.getElementById('TORRENT_CLIENT_CATEGORY')?.value || '';

            resultDropdowns.forEach(dropdown => {
                dropdown.disabled = false; // <--- ADD THIS
                const currentVal = dropdown.value;
                const preferredDefault = getPreferredClientCategoryForMainCat(dropdown.dataset.mainCat || '');
                dropdown.innerHTML = '<option value="">Category</option>';
                if (data && typeof data === 'object') {
                    for (const key in data) dropdown.add(new Option(data[key].name, data[key].name));
                }
                dropdown.value = currentVal || preferredDefault || defaultCategory;
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
            syncTypeCategoryOptions();
        });
}

let authFailureToastShown = false;

// Shows the "add your IP to MAM" prompt (with the server's public IP, a copy
// button, and a security-page link) and fires a one-time toast pointing there.
function showMamAuthFailurePrompt() {
    hideMamNotConfiguredPrompt();
    const block = document.getElementById('mam-auth-fix-ip');
    if (block) block.classList.remove('d-none');
    // The IP is populated by fetchPublicIP(); fetch it now if it hasn't resolved.
    if (!window.mousesearchPublicIp) fetchPublicIP();
    if (!authFailureToastShown) {
        authFailureToastShown = true;
        showToast("MAM cookie isn't authenticating. Your server's IP may need to be added to your MAM session — see Settings → MyAnonaMouse Auth.", 'danger');
    }
}

function hideMamAuthFailurePrompt() {
    const block = document.getElementById('mam-auth-fix-ip');
    if (block) block.classList.add('d-none');
    authFailureToastShown = false;
}

let notConfiguredToastShown = false;

// Shows the "set your mam_id" prompt when no session cookie is configured. This is
// a different problem from an IP-authorization failure, so it gets its own message
// and never points the user at the security/IP page.
function showMamNotConfiguredPrompt() {
    hideMamAuthFailurePrompt();
    const block = document.getElementById('mam-not-configured');
    if (block) block.classList.remove('d-none');
    if (!notConfiguredToastShown) {
        notConfiguredToastShown = true;
        showToast("No MAM session ID is set. Add your mam_id in Settings → MyAnonaMouse Auth.", 'danger');
    }
}

function hideMamNotConfiguredPrompt() {
    const block = document.getElementById('mam-not-configured');
    if (block) block.classList.add('d-none');
    notConfiguredToastShown = false;
}

function loadMamUserData() {
    fetch(`${APP_BASE}/mam/user_data`, { cache: "no-store" })
        .then(async response => {
            const data = await response.json().catch(() => ({}));
            updateMamProxyStatusPanel(data?.proxy_status);
            if (!response.ok) {
                const error = new Error(data?.message || data?.error || `MAM request failed with HTTP ${response.status}`);
                error.httpStatus = response.status;
                error.responseMessage = data?.message || data?.error || '';
                throw error;
            }
            return data;
        })
        .then(data => {
            const statusSpan = document.getElementById('mam-status');
            const statusMessage = document.getElementById('mam-status-message');
            const statusIconSpan = document.getElementById('mam-status-icon');
            const mamIdField = document.getElementById('MAM_ID');
            if (statusSpan) { statusSpan.textContent = 'CONNECTED'; statusSpan.className = 'text-success'; }
            if (statusMessage) {
                statusMessage.textContent = String(data.message || 'MyAnonaMouse is connected.');
                statusMessage.className = 'small text-success-emphasis mt-1';
            }
            if (statusIconSpan) statusIconSpan.innerHTML = greenCheckIcon;
            if (mamIdField && Object.prototype.hasOwnProperty.call(data, 'current_mam_cookie')) {
                mamIdField.value = String(data.current_mam_cookie || '');
                updateCopyFieldButtons();
            }

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
            } else if (vipWeeksContainer && vipWeeksSpan) {
                vipWeeksSpan.textContent = '--';
                vipWeeksContainer.style.display = 'none';
            }

            updateMamCookieStatus({
                kind: 'authenticated',
                label: 'Authenticated',
                level: 'success',
            });
            hideMamAuthFailurePrompt();
            hideMamNotConfiguredPrompt();
        })
        .catch(error => {
            const statusSpan = document.getElementById('mam-status');
            const statusMessage = document.getElementById('mam-status-message');
            const statusIconSpan = document.getElementById('mam-status-icon');
            const vipWeeksContainer = document.getElementById('vip-weeks-container');
            const vipWeeksSpan = document.getElementById('vip-weeks-remaining');

            if (statusSpan) { statusSpan.textContent = 'NOT CONNECTED'; statusSpan.className = 'text-danger'; }
            if (statusMessage) {
                statusMessage.textContent = error?.message || 'Not logged into MAM or failed to fetch data.';
                statusMessage.className = 'small text-danger mt-1';
            }
            if (statusIconSpan) statusIconSpan.innerHTML = redXIcon;

            document.getElementById('mam-username').textContent = 'N/A';
            document.getElementById('mam-class').textContent = 'N/A';
            document.getElementById('mam-uploaded').textContent = 'N/A';
            document.getElementById('mam-downloaded').textContent = 'N/A';
            document.getElementById('mam-ratio').textContent = 'N/A';
            document.getElementById('mam-bonus').textContent = 'N/A';
            window.currentVipUntil = null;
            window.currentBonusPoints = 0;
            window.isVipActive = false;
            if (vipWeeksContainer && vipWeeksSpan) {
                vipWeeksSpan.textContent = '--';
                vipWeeksContainer.style.display = 'none';
            }

            const httpStatus = Number(error?.httpStatus);
            const responseMessage = String(error?.responseMessage || error?.message || 'Unknown error').trim();
            const isTimeout = httpStatus === 504 || /timed?\s*out|timeout/i.test(responseMessage);
            const mamIdInput = document.getElementById('MAM_ID');
            const mamIdEmpty = !mamIdInput || !String(mamIdInput.value || '').trim();
            // The backend returns 401 only when no mam_id cookie is set at all. Treat
            // that as "not configured" (set your mam_id), not an IP-authorization problem.
            const isNotConfigured = httpStatus === 401 && (mamIdEmpty || /not configured/i.test(responseMessage));
            // A genuine auth failure (cookie present but rejected — e.g. the IP/ASN
            // isn't allowed on the session) is what the IP-fix prompt is for.
            const isAuthFailure = httpStatus === 403 || (httpStatus === 401 && !isNotConfigured);

            if (isNotConfigured) {
                updateMamCookieStatus({
                    kind: 'unconfigured',
                    label: 'Not configured',
                    level: 'danger',
                });
                showMamNotConfiguredPrompt();
            } else if (isAuthFailure) {
                updateMamCookieStatus({
                    kind: 'unauthenticated',
                    label: 'Unauthenticated',
                    level: 'danger',
                });
                showMamAuthFailurePrompt();
            } else {
                const label = Number.isFinite(httpStatus) && httpStatus > 0
                    ? `HTTP ${httpStatus}: ${isTimeout ? `Timeout - ${responseMessage}` : responseMessage}`
                    : (isTimeout ? `Timeout: ${responseMessage}` : responseMessage);
                updateMamCookieStatus({
                    kind: isTimeout ? 'timeout' : 'error',
                    label,
                    level: 'danger',
                });
                // Network/server errors aren't IP-authorization issues, so don't push
                // the user toward the security page.
                hideMamAuthFailurePrompt();
                hideMamNotConfiguredPrompt();
            }
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
        const response = await fetch(`${APP_BASE}/client/info/${hash}`, { cache: "no-store" });
        if (response.ok) {
            const data = await response.json();
            updateTorrentUI(hash, data, resultItem);
        }
    } catch (error) { console.error(`Error fetching hash ${hash}:`, error); }
}

function copyTextWithFeedback(button, text) {
    if (!navigator.clipboard || !text) return;
    navigator.clipboard.writeText(text);
    const originalIcon = button.innerHTML;
    button.innerHTML = '<i class="bi bi-check2 text-success"></i>';
    setTimeout(() => button.innerHTML = originalIcon, 2000);
}

function fieldCopyValue(field) {
    if (!field) return '';
    const value = 'value' in field ? field.value : field.textContent;
    return String(value || '').trim();
}

function updateCopyFieldButtons() {
    document.querySelectorAll('.copy-field-btn').forEach(btn => {
        const selector = btn.dataset.copyTarget;
        const target = selector ? document.querySelector(selector) : null;
        const value = fieldCopyValue(target);
        const usableValue = value && value !== 'Not synced';
        btn.classList.toggle('d-none', !navigator.clipboard || !usableValue);
    });
}

function normalizeIpForCompare(value) {
    return String(value || '').trim().toLowerCase();
}

function setHealthBadge(elementId, state) {
    const badge = document.getElementById(elementId);
    if (!badge) return;

    const normalizedState = String(state || 'checking');
    const labels = {
        healthy: 'Healthy',
        unhealthy: 'Unhealthy',
        checking: 'Checking',
        disabled: 'Disabled',
    };
    const badgeClasses = {
        healthy: 'text-bg-success',
        unhealthy: 'text-bg-danger',
        checking: 'text-bg-secondary',
        disabled: 'text-bg-secondary',
    };

    badge.className = 'badge ms-2';
    badge.classList.add(badgeClasses[normalizedState] || badgeClasses.checking);
    badge.textContent = labels[normalizedState] || labels.checking;
}

function updateMamCookieStatus(status) {
    const alert = document.getElementById('mam-cookie-status-alert');
    const message = document.getElementById('mam-cookie-status-message');
    const networkStatus = document.getElementById('network-mam-cookie-status');
    const normalizedStatus = typeof status === 'object' && status !== null
        ? status
        : { kind: String(status || 'checking'), label: 'Checking...', level: 'secondary' };
    const label = String(normalizedStatus.label || 'Checking...');
    const level = String(normalizedStatus.level || 'secondary');
    const kind = String(normalizedStatus.kind || 'checking');

    window.mamCookieStatus = { kind, label, level };

    if (alert) {
        alert.className = 'alert small mb-3';
        alert.classList.add(`alert-${level}`);
    }
    if (message) {
        message.textContent = label;
    }
    if (networkStatus) {
        networkStatus.textContent = label;
    }

    updateNetworkSection();
}

function currentMamProxyRoute() {
    return String(window.mamProxyStatus?.route || 'direct');
}

function updateNetworkSection() {
    const alert = document.getElementById('network-status-alert');
    if (!alert) return;

    const healthLabel = document.getElementById('network-health-label');
    const statusMessage = document.getElementById('network-status-message');
    const routeLabel = document.getElementById('network-route-label');
    const ipList = document.getElementById('network-ip-list');
    const directIpEl = document.getElementById('network-direct-ip');
    const directIpRow = document.getElementById('network-direct-ip-row');
    const proxyIpEl = document.getElementById('network-proxy-ip');
    const proxyIpRow = document.getElementById('network-proxy-ip-row');
    const proxyErrorRow = document.getElementById('network-proxy-error-row');
    const proxyErrorEl = document.getElementById('network-proxy-error');

    const directIp = normalizeIpForCompare(window.mousesearchPublicIp);
    const proxyIp = normalizeIpForCompare(window.mousesearchProxyIp);
    const route = currentMamProxyRoute();
    const proxyEnabled = document.getElementById('MAM_PROXY_ENABLED')?.checked;
    const mamCookieStatus = window.mamCookieStatus && typeof window.mamCookieStatus === 'object'
        ? window.mamCookieStatus
        : { kind: 'checking', label: 'Checking...', level: 'secondary' };
    const routeLabels = {
        proxy: 'Proxy',
        direct_fallback: 'Direct Fallback',
        direct: 'Direct',
        error: 'Unavailable',
        configured: 'Unavailable',
    };

    if (directIpEl) directIpEl.textContent = directIp || 'Unavailable';
    if (proxyIpEl) proxyIpEl.textContent = proxyIp || 'Unavailable';
    if (proxyIpRow) proxyIpRow.classList.toggle('d-none', !proxyEnabled);
    if (routeLabel) routeLabel.textContent = proxyEnabled ? (routeLabels[route] || 'Direct') : 'Direct';

    if (ipList) {
        if (proxyEnabled) {
            if (proxyIpRow) ipList.appendChild(proxyIpRow);
            if (directIpRow) ipList.appendChild(directIpRow);
        } else {
            if (directIpRow) ipList.appendChild(directIpRow);
            if (proxyIpRow) ipList.appendChild(proxyIpRow);
        }
    }

    const lastError = String(window.mamProxyStatus?.last_error || '').trim();
    if (proxyErrorRow && proxyErrorEl) {
        proxyErrorEl.textContent = lastError;
        proxyErrorRow.classList.toggle('d-none', !lastError);
    }

    let networkState = 'checking';
    let networkMessage = 'Waiting for network status.';

    if (mamCookieStatus.kind === 'unconfigured') {
        networkState = 'unhealthy';
        networkMessage = 'No MAM session ID is configured.';
    } else if (mamCookieStatus.kind === 'unauthenticated') {
        networkState = 'unhealthy';
        networkMessage = 'MAM cookie is unauthenticated.';
    } else if (mamCookieStatus.kind === 'timeout' || mamCookieStatus.kind === 'error') {
        networkState = 'unhealthy';
        networkMessage = mamCookieStatus.label;
    } else if (mamCookieStatus.kind === 'checking') {
        networkState = 'checking';
        networkMessage = 'Waiting for MAM cookie status.';
    } else if (proxyEnabled) {
        if (route === 'proxy') {
            networkState = 'healthy';
            networkMessage = 'MAM requests are currently using the configured proxy.';
        } else if (route === 'direct_fallback') {
            networkState = 'unhealthy';
            networkMessage = 'Proxy is enabled, but MAM requests are falling back to direct.';
        } else if (route === 'error' || route === 'configured' || route === 'direct') {
            networkState = 'unhealthy';
            networkMessage = 'Proxy is enabled, but it is not currently active.';
        }
    } else {
        networkState = 'healthy';
        networkMessage = 'MAM requests are currently using the direct route.';
    }

    alert.className = 'alert small mb-0';
    alert.classList.add(`alert-${networkState === 'healthy' ? 'success' : networkState === 'unhealthy' ? 'danger' : 'secondary'}`);
    if (healthLabel) {
        healthLabel.textContent = networkState === 'healthy'
            ? 'Healthy'
            : networkState === 'unhealthy'
                ? 'Unhealthy'
                : 'Checking';
    }
    if (statusMessage) statusMessage.textContent = networkMessage;
}

function updateMamProxyStatusPanel(proxyStatus) {
    const alert = document.getElementById('mam-proxy-status-alert');
    const message = document.getElementById('mam-proxy-status-message');
    const data = proxyStatus || {};
    window.mamProxyStatus = data;
    const route = String(data.route || 'direct');
    const enabled = data.enabled !== false;

    let sectionState = 'checking';
    let sectionMessage = 'Checking...';

    if (!enabled) {
        sectionState = 'disabled';
        sectionMessage = 'Disabled';
    } else if (route === 'proxy') {
        sectionState = 'healthy';
        sectionMessage = 'Healthy (details in the Network section below)';
    } else if (route === 'direct' || route === 'direct_fallback' || route === 'error' || route === 'configured') {
        sectionState = 'unhealthy';
        sectionMessage = 'Unhealthy (details in the Network section below)';
    }

    if (alert) {
        alert.className = 'alert small mb-0';
        alert.classList.add(
            sectionState === 'healthy'
                ? 'alert-success'
                : sectionState === 'unhealthy'
                    ? 'alert-warning'
                    : 'alert-secondary'
        );
    }
    setHealthBadge('mam-proxy-health-badge', sectionState);
    if (message) message.textContent = sectionMessage;

    updateNetworkSection();
}

async function fetchPublicIP() {
    Promise.allSettled([
        fetch(`${APP_BASE}/system/public_ip?route=direct`).then(r => r.json()),
        fetch(`${APP_BASE}/system/public_ip?route=mam`).then(r => r.json()),
    ])
        .then(([directResult, proxyResult]) => {
            const directData = directResult.status === 'fulfilled' ? directResult.value : null;
            const proxyData = proxyResult.status === 'fulfilled' ? proxyResult.value : null;

            if (directData?.ip) {
                window.mousesearchPublicIp = directData.ip;
                document.querySelectorAll('.backend-ip-display').forEach(el => el.textContent = directData.ip);
                document.querySelectorAll('.backend-ip-display-badge').forEach(el => el.style.display = 'inline-block');
                document.querySelectorAll('.copy-ip-btn').forEach(btn => {
                    if (navigator.clipboard) {
                        btn.onclick = () => {
                            copyTextWithFeedback(btn, directData.ip);
                        };
                    } else {
                        btn.style.display = 'none';
                    }
                });
            } else {
                window.mousesearchPublicIp = '';
                document.querySelectorAll('.backend-ip-display').forEach(el => el.textContent = "Unavailable");
            }

            if (proxyData?.ip && String(proxyData?.route || '') === 'mam') {
                window.mousesearchProxyIp = proxyData.ip;
            } else {
                window.mousesearchProxyIp = '';
            }

            if (proxyData?.error) {
                const resolverErrors = Array.isArray(proxyData.resolver_errors) ? proxyData.resolver_errors : [];
                console.error('[Network] Proxy IP lookup failed:', proxyData.error, resolverErrors);
            }

            if (directData?.error) {
                const resolverErrors = Array.isArray(directData.resolver_errors) ? directData.resolver_errors : [];
                console.error('[Network] Direct IP lookup failed:', directData.error, resolverErrors);
            }

            if (proxyData?.proxy_status) {
                updateMamProxyStatusPanel(proxyData.proxy_status);
            }
            updateNetworkSection();
        })
        .catch(err => {
            console.error("Failed to fetch IP", err);
            document.querySelectorAll('.backend-ip-display').forEach(el => el.textContent = "Unavailable");
            window.mousesearchPublicIp = '';
            window.mousesearchProxyIp = '';
            updateNetworkSection();
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

    const advancedSearchButton = document.querySelector('[data-bs-target="#advancedSearchOffcanvas"]');
    if (advancedSearchButton) {
        advancedSearchButton.addEventListener('click', () => {
            triggerHaptic('menu');
        });
    }

    bindHapticClick('.btn-close[data-bs-dismiss="offcanvas"], .btn-close[data-bs-dismiss="modal"]', 'close');

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

    const closeHardcoverStatusPickers = (exceptPicker = null) => {
        const exceptCard = exceptPicker?.closest('.result-card') || null;
        document.querySelectorAll('[data-hardcover-status-picker].is-open').forEach((picker) => {
            if (exceptPicker && picker === exceptPicker) return;
            picker.classList.remove('is-open');
            picker.dataset.busy = 'false';
            picker.closest('.result-card')?.classList.remove('result-card--status-open');
            const toggle = picker.querySelector('[data-hardcover-status-toggle]');
            if (toggle) {
                toggle.setAttribute('aria-expanded', 'false');
                toggle.disabled = false;
            }
            picker.querySelectorAll('[data-hardcover-status-option]').forEach((option) => {
                option.disabled = false;
            });
        });
        document.querySelectorAll('.result-card.result-card--status-open').forEach((card) => {
            if (exceptCard && card === exceptCard) return;
            card.classList.remove('result-card--status-open');
        });
        if (!exceptPicker && !hasOpenHardcoverStatusPicker()) {
            flushPendingHardcoverEnrichmentUpdates();
        }
    };

    const setHardcoverStatusPickerBusy = (picker, isBusy) => {
        if (!picker) return;
        picker.dataset.busy = isBusy ? 'true' : 'false';
        const toggle = picker.querySelector('[data-hardcover-status-toggle]');
        if (toggle) {
            toggle.disabled = !!isBusy;
            toggle.setAttribute('aria-busy', isBusy ? 'true' : 'false');
            toggle.classList.toggle('is-pending', !!isBusy);
        }
        picker.querySelectorAll('[data-hardcover-status-option]').forEach((option) => {
            option.disabled = !!isBusy;
        });
    };

    const findHardcoverStatusPicker = (bookId, torrentId = '') => {
        const normalizedBookId = Number(bookId);
        if (!Number.isFinite(normalizedBookId) || normalizedBookId <= 0) return null;
        const safeTorrentId = String(torrentId || '').trim();
        if (safeTorrentId) {
            const escapedTorrentId = window.CSS && CSS.escape
                ? CSS.escape(safeTorrentId)
                : safeTorrentId.replace(/["\\]/g, '\\$&');
            const exactMatch = document.querySelector(
                `[data-hardcover-status-picker][data-book-id="${normalizedBookId}"][data-torrent-id="${escapedTorrentId}"]`
            );
            if (exactMatch) return exactMatch;
        }
        return document.querySelector(`[data-hardcover-status-picker][data-book-id="${normalizedBookId}"]`);
    };

    const openHardcoverStatusPicker = (picker) => {
        if (!picker) return;
        closeHardcoverStatusPickers(picker);
        picker.classList.add('is-open');
        hardcoverStatusPickerOpenedAt = performance.now();
        picker.closest('.result-card')?.classList.add('result-card--status-open');
        const pickerToggle = picker.querySelector('[data-hardcover-status-toggle]');
        if (pickerToggle) {
            pickerToggle.setAttribute('aria-expanded', 'true');
        }
    };

    const hydrateHardcoverUserBookStatus = async (picker) => {
        if (!picker || picker.dataset.userBookKnown === 'true' || picker.dataset.userBookLoading === 'true') {
            return picker;
        }

        const bookId = Number(picker.dataset.bookId || 0);
        const torrentId = String(picker.dataset.torrentId || '').trim();
        if (!Number.isFinite(bookId) || bookId <= 0) return picker;

        picker.dataset.userBookLoading = 'true';
        setHardcoverStatusPickerBusy(picker, true);

        try {
            const response = await fetch(`${APP_BASE}/hardcover/user-book/${bookId}`, { cache: 'no-store' });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.status !== 'success') {
                throw new Error(data.message || `Hardcover status lookup failed (HTTP ${response.status})`);
            }
            syncHardcoverUserBookStatus(bookId, data.user_book, { userBookKnown: true });
        } catch (error) {
            console.warn('[HARDCOVER] Status hydrate failed:', error);
        }

        const refreshedPicker = findHardcoverStatusPicker(bookId, torrentId) || picker;
        refreshedPicker.dataset.userBookLoading = 'false';
        setHardcoverStatusPickerBusy(refreshedPicker, false);
        return refreshedPicker;
    };

    const submitHardcoverStatusChange = async (picker, statusId, { action = '' } = {}) => {
        if (!picker || picker.dataset.busy === 'true') return;
        const bookId = Number(picker.dataset.bookId || 0);
        const currentStatusId = Number(picker.dataset.currentStatusId || 0);
        const normalizedAction = String(action || '').trim().toLowerCase();
        const isRemove = normalizedAction === 'remove';
        if (!Number.isFinite(bookId) || bookId <= 0) return;
        if (!isRemove && (!Number.isFinite(statusId) || statusId <= 0)) return;
        if (!isRemove && statusId === currentStatusId) {
            closeHardcoverStatusPickers();
            return;
        }

        closeHardcoverStatusPickers();
        setHardcoverStatusPending(bookId, true);
        try {
            const response = await fetch(`${APP_BASE}/hardcover/user-book/status`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    book_id: bookId,
                    ...(isRemove ? { action: 'remove' } : { status_id: statusId }),
                }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.status !== 'success') {
                throw new Error(data.message || `Hardcover status request failed (HTTP ${response.status})`);
            }

            syncHardcoverUserBookStatus(bookId, data.user_book);
            showToast(data.message || (isRemove ? 'Hardcover status removed.' : 'Hardcover status updated.'), 'success');
        } catch (error) {
            showToast(error?.message || (isRemove ? 'Unable to remove Hardcover status.' : 'Unable to update Hardcover status.'), 'danger');
        } finally {
            setHardcoverStatusPending(bookId, false);
        }
    };

    document.addEventListener('click', async (event) => {
        const removeButton = event.target.closest('[data-hardcover-status-remove]');
        if (removeButton) {
            event.preventDefault();
            event.stopImmediatePropagation();
            triggerHaptic('tap');
            const picker = removeButton.closest('[data-hardcover-status-picker]');
            submitHardcoverStatusChange(picker, null, { action: 'remove' });
            return;
        }

        const option = event.target.closest('[data-hardcover-status-option]');
        if (option) {
            event.preventDefault();
            event.stopImmediatePropagation();
            triggerHaptic('tap');
            const picker = option.closest('[data-hardcover-status-picker]');
            submitHardcoverStatusChange(picker, Number(option.dataset.hardcoverStatusOption || 0));
            return;
        }

        const toggle = event.target.closest('[data-hardcover-status-toggle]');
        if (toggle) {
            event.preventDefault();
            event.stopImmediatePropagation();
            triggerHaptic('tap');
            let picker = toggle.closest('[data-hardcover-status-picker]');
            if (!picker) return;
            const shouldOpen = !picker.classList.contains('is-open');
            if (!shouldOpen) {
                closeHardcoverStatusPickers();
                return;
            }
            picker = await hydrateHardcoverUserBookStatus(picker);
            openHardcoverStatusPicker(picker);
            return;
        }

        if (!event.target.closest('[data-hardcover-status-picker]')) {
            if (hardcoverStatusPickerOpenedAt && (performance.now() - hardcoverStatusPickerOpenedAt) < 200) {
                return;
            }
            closeHardcoverStatusPickers();
        }
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            closeHardcoverStatusPickers();
        }
    });

    const settingsTabContent = document.getElementById('settingsTabContent');

    document.querySelectorAll('#settingTabs [data-bs-toggle="tab"]').forEach(tabButton => {
        tabButton.addEventListener('click', () => {
            triggerHaptic('tab');
            if (settingsTabContent) {
                settingsTabContent.scrollTo({ top: 0, behavior: 'smooth' });
            }
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
            { trigger: 'MAM_PROXY_ENABLED', target: 'MAM_PROXY_URL' },
            { trigger: 'ENABLE_DYNAMIC_IP_UPDATE', target: 'DYNAMIC_IP_CHECK_INTERVAL_SECONDS' },
            { trigger: 'HARDCOVER_ENRICHMENT_ENABLED', target: 'HARDCOVER_API_TOKEN' },
            { trigger: 'AUTO_BUY_VIP', target: 'AUTO_BUY_VIP_INTERVAL_HOURS' },
            { trigger: 'AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_ENABLED', target: 'AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_MB' },
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

        const mamIdInput = document.getElementById('MAM_ID');
        if (mamIdInput) {
            mamIdInput.required = true;
        }
        updateCopyFieldButtons();
        updateNetworkSection();

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
    let settingsTorrentProbeRequestId = 0;
    let settingsHardcoverProbeRequestId = 0;

    function getTorrentClientProbePayload() {
        return {
            TORRENT_CLIENT_TYPE: document.getElementById('TORRENT_CLIENT_TYPE')?.value || '',
            TORRENT_CLIENT_URL: document.getElementById('TORRENT_CLIENT_URL')?.value || '',
            TORRENT_CLIENT_USERNAME: document.getElementById('TORRENT_CLIENT_USERNAME')?.value || '',
            TORRENT_CLIENT_PASSWORD: document.getElementById('TORRENT_CLIENT_PASSWORD')?.value || '',
        };
    }

    function getHardcoverProbePayload() {
        return {
            HARDCOVER_API_TOKEN: document.getElementById('HARDCOVER_API_TOKEN')?.value || '',
        };
    }

    async function checkSettingsTorrentClientConnection() {
        const payload = getTorrentClientProbePayload();
        if (!String(payload.TORRENT_CLIENT_URL || '').trim()) {
            setInlineConnectionStatus({
                alertId: 'settings-torrent-client-status',
                iconId: 'settings-torrent-client-status-icon',
                messageId: 'settings-torrent-client-status-message',
                state: 'idle',
                message: 'Enter a torrent client URL to test the connection.',
            });
            return;
        }

        const requestId = ++settingsTorrentProbeRequestId;
        setInlineConnectionStatus({
            alertId: 'settings-torrent-client-status',
            iconId: 'settings-torrent-client-status-icon',
            messageId: 'settings-torrent-client-status-message',
            state: 'idle',
            message: 'Checking torrent client connection...',
        });

        try {
            const response = await fetch(`${APP_BASE}/api/settings/test-torrent-client`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await response.json().catch(() => ({}));
            if (requestId !== settingsTorrentProbeRequestId) return;

            const state = data.status === 'success' ? 'success' : 'error';
            const message = String(data.message || `Torrent client probe failed (HTTP ${response.status}).`).trim();
            setInlineConnectionStatus({
                alertId: 'settings-torrent-client-status',
                iconId: 'settings-torrent-client-status-icon',
                messageId: 'settings-torrent-client-status-message',
                state,
                message,
            });
        } catch (error) {
            if (requestId !== settingsTorrentProbeRequestId) return;
            setInlineConnectionStatus({
                alertId: 'settings-torrent-client-status',
                iconId: 'settings-torrent-client-status-icon',
                messageId: 'settings-torrent-client-status-message',
                state: 'error',
                message: error?.message || 'Unable to reach the torrent client probe endpoint.',
            });
        }
    }

    async function checkSettingsHardcoverConnection() {
        const payload = getHardcoverProbePayload();
        if (!String(payload.HARDCOVER_API_TOKEN || '').trim()) {
            setInlineConnectionStatus({
                alertId: 'settings-hardcover-status',
                iconId: 'settings-hardcover-status-icon',
                messageId: 'settings-hardcover-status-message',
                state: 'idle',
                message: 'Enter a Hardcover API key to test the connection.',
            });
            return;
        }

        const requestId = ++settingsHardcoverProbeRequestId;
        setInlineConnectionStatus({
            alertId: 'settings-hardcover-status',
            iconId: 'settings-hardcover-status-icon',
            messageId: 'settings-hardcover-status-message',
            state: 'idle',
            message: 'Checking Hardcover connection...',
        });

        try {
            const response = await fetch(`${APP_BASE}/api/settings/test-hardcover`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await response.json().catch(() => ({}));
            if (requestId !== settingsHardcoverProbeRequestId) return;

            const rawStatus = String(data.status || '').toLowerCase();
            const state = rawStatus === 'success' ? 'success' : rawStatus === 'idle' ? 'idle' : 'error';
            const message = String(data.message || `Hardcover probe failed (HTTP ${response.status}).`).trim();
            setInlineConnectionStatus({
                alertId: 'settings-hardcover-status',
                iconId: 'settings-hardcover-status-icon',
                messageId: 'settings-hardcover-status-message',
                state,
                message,
            });
        } catch (error) {
            if (requestId !== settingsHardcoverProbeRequestId) return;
            setInlineConnectionStatus({
                alertId: 'settings-hardcover-status',
                iconId: 'settings-hardcover-status-icon',
                messageId: 'settings-hardcover-status-message',
                state: 'error',
                message: error?.message || 'Unable to reach the Hardcover probe endpoint.',
            });
        }
    }

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

        if (typeCategoryRulesList) {
            const defaults = Array.isArray(settingsSnapshot['type_category_defaults[]']) ? settingsSnapshot['type_category_defaults[]'] : [];
            const categories = Array.isArray(settingsSnapshot['type_category_values[]']) ? settingsSnapshot['type_category_values[]'] : [];
            const rows = defaults.map((defaultMainCat, idx) => ({
                default_main_cat: defaultMainCat || '',
                default_torrent_category: categories[idx] || ''
            }));
            renderTypeCategoryRows(rows);
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
            updateCopyFieldButtons();
        });
        settingsForm.addEventListener('change', () => {
            if (isRestoringSettings) return;
            settingsDirty = true;
            updateCopyFieldButtons();
        });
    }

    settingsForm?.addEventListener('focusout', (event) => {
        if (isRestoringSettings) return;
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;

        if (target.matches('#TORRENT_CLIENT_URL, #TORRENT_CLIENT_USERNAME, #TORRENT_CLIENT_PASSWORD')) {
            checkSettingsTorrentClientConnection();
        }

        if (target.matches('#HARDCOVER_API_TOKEN')) {
            checkSettingsHardcoverConnection();
        }
    });

    settingsForm?.addEventListener('change', (event) => {
        if (isRestoringSettings) return;
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;

        if (target.matches('#TORRENT_CLIENT_TYPE')) {
            checkSettingsTorrentClientConnection();
        }
    });

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
        checkSettingsTorrentClientConnection();
        checkSettingsHardcoverConnection();
    }

    document.querySelectorAll('.copy-field-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const selector = btn.dataset.copyTarget;
            const target = selector ? document.querySelector(selector) : null;
            copyTextWithFeedback(btn, fieldCopyValue(target));
        });
    });
    updateCopyFieldButtons();

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

            checkSettingsTorrentClientConnection();
        });
    }

    // Upload Amount Validation
    function normalizeUploadAmount(value) {
        const numValue = parseFloat(value);
        if (isNaN(numValue)) return UPLOAD_AMOUNT_MIN;
        const rounded = Math.round(numValue / UPLOAD_AMOUNT_STEP) * UPLOAD_AMOUNT_STEP;
        return Math.max(UPLOAD_AMOUNT_MIN, rounded);
    }

    function updateCustomUploadCostInfo() {
        const input = document.getElementById('custom-upload-amount');
        const info = document.getElementById('custom-amount-cost-info');
        if (!input || !info) return;

        const normalized = normalizeUploadAmount(input.value || UPLOAD_AMOUNT_MIN);
        info.textContent = `${normalized.toLocaleString()} GB costs ${(normalized * UPLOAD_COST_PER_GB).toLocaleString()} BP`;
    }

    document.querySelectorAll('.upload-amount-input').forEach(input => {
        input.addEventListener('blur', function () {
            const valid = normalizeUploadAmount(this.value);
            if (parseFloat(this.value) !== valid) this.value = valid;
            if (this.id === 'custom-upload-amount') updateCustomUploadCostInfo();
        });
    });

    updateMaxUploadPurchaseDisplay();
    updateCustomUploadCostInfo();

    // --- B. Button Handlers (Save, VIP, Upload) ---

    // Save Settings
    document.getElementById('save-settings-button')?.addEventListener('click', function () {
        triggerHaptic('save');
        fetch(`${APP_BASE}/update_settings`, { method: 'POST', body: new FormData(document.getElementById('settings-form')) })
            .then(response => response.json())
            .then(data => {
                showToast(data.message, data.status === 'success' ? 'success' : 'danger');
                if (data.status === 'success') {
                    captureSettingsSnapshot();
                    updateMamProxyStatusPanel(data.proxy_status);
                    fetchPublicIP();
                    updateCopyFieldButtons();
                    const catDropdown = document.getElementById('TORRENT_CLIENT_CATEGORY');
                    if (catDropdown) catDropdown.dataset.currentValue = catDropdown.value;

                    const clientLink = document.getElementById('clientLink');
                    const clientUrl = document.getElementById('TORRENT_CLIENT_URL').value;
                    if (clientLink) { clientLink.href = clientUrl; clientLink.textContent = clientUrl; }
                    checkClientStatus();
                    checkSettingsTorrentClientConnection();
                    checkSettingsHardcoverConnection();
                    loadMamUserData();
                    const dynamicIpEnabled = document.getElementById('ENABLE_DYNAMIC_IP_UPDATE')?.checked;
                    if (dynamicIpEnabled) {
                        setTimeout(() => loadMamUserData(), 3500);
                    }
                }
            })
            .catch(() => showToast("Error saving settings.", 'danger'));
    });

    document.getElementById('organize-now-button')?.addEventListener('click', async function () {
        document.getElementById('organize-now-status-body')?.classList.remove('d-none');
        const originalHtml = this.innerHTML;
        this.disabled = true;
        this.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Running...';
        setInlineActionStatus('organize-now-status', 'idle', 'Running one-time organization pass...');

        try {
            const response = await fetch(`${APP_BASE}/organize`, { method: 'POST' });
            const data = await response.json().catch(() => ({}));
            const results = data?.results || {};
            const succeeded = Number(results.succeeded || 0);
            const failed = Number(results.failed || 0);
            let message = '';

            if (data.status === 'success') {
                message = `Finished organizing pending torrents. Succeeded: ${succeeded}.`;
                setInlineActionStatus('organize-now-status', 'success', message);
                showToast(message, 'success');
            } else if (data.status === 'partial') {
                message = `Organization finished with partial success. Succeeded: ${succeeded}. Failed: ${failed}.`;
                setInlineActionStatus('organize-now-status', 'error', message);
                showToast(message, 'warning');
            } else {
                message = String(data.message || `Organization run failed. Succeeded: ${succeeded}. Failed: ${failed}.`).trim();
                setInlineActionStatus('organize-now-status', 'error', message);
                showToast(message, 'danger');
            }
        } catch (error) {
            const message = error?.message || 'Unable to start the organization run.';
            setInlineActionStatus('organize-now-status', 'error', message);
            showToast(message, 'danger');
        } finally {
            this.disabled = false;
            this.innerHTML = originalHtml;
        }
    });

    // Buy VIP Logic
    const buyVipButton = document.getElementById('buy-vip-button');
    const buyWedgeButton = document.getElementById('buy-wedge-button');
    const confirmWedgePurchaseButton = document.getElementById('confirm-wedge-purchase-button');
    const vipModalEl = document.getElementById('vipPurchaseModal');
    const vipModal = vipModalEl ? new bootstrap.Modal(vipModalEl) : null;
    const wedgeModalEl = document.getElementById('wedgePurchaseModal');
    const wedgeModal = wedgeModalEl ? new bootstrap.Modal(wedgeModalEl) : null;
    let wedgeConfirmationMode = 'purchase';
    let reopenDownloadModalAfterWedge = false;
    const VIP_COST_PER_WEEK = 1250;
    const MAX_VIP_WEEKS = 12.85;

    function showWedgeConfirmation(mode = 'purchase') {
        if (!wedgeModal) return;

        wedgeConfirmationMode = mode;
        const isDownloadUse = mode === 'download';
        const title = document.getElementById('wedge-modal-title-text');
        const question = document.getElementById('wedge-modal-question');
        const warning = document.getElementById('wedge-modal-warning');
        const balanceRow = document.getElementById('wedge-modal-balance-row');
        const confirmLabel = document.getElementById('wedge-modal-confirm-label');

        if (title) title.textContent = isDownloadUse ? 'Confirm Wedge Use' : 'Confirm Wedge Purchase';
        if (question) {
            question.textContent = isDownloadUse
                ? 'Use one personal Freeleech wedge when this download starts?'
                : 'Buy one personal Freeleech wedge using your MAM bonus points?';
        }
        if (warning) {
            warning.textContent = isDownloadUse
                ? 'The wedge will be spent when the download starts.'
                : 'This purchase spends bonus points immediately.';
        }
        if (balanceRow) balanceRow.classList.toggle('d-none', isDownloadUse);
        if (confirmLabel) confirmLabel.textContent = isDownloadUse ? 'Use Wedge' : 'Buy Wedge';

        if (!isDownloadUse) {
            const currentBonus = Number(window.currentBonusPoints || 0);
            const currentBonusEl = document.getElementById('wedge-modal-current-bp');
            if (currentBonusEl) currentBonusEl.textContent = currentBonus.toLocaleString();
        }

        wedgeModal.show();
    }

    wedgeModalEl?.addEventListener('hidden.bs.modal', () => {
        if (!reopenDownloadModalAfterWedge) return;
        reopenDownloadModalAfterWedge = false;
        confirmModal?.show();
    });

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

            document.querySelectorAll('.vip-buy-btn[data-duration]:not([data-duration="max"])').forEach(btn => {
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

                fetch(`${APP_BASE}/mam/buy_vip`, {
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

    if (buyWedgeButton && wedgeModal) {
        buyWedgeButton.addEventListener('click', function () {
            reopenDownloadModalAfterWedge = false;
            showWedgeConfirmation('purchase');
        });
    }

    if (confirmWedgePurchaseButton) {
        confirmWedgePurchaseButton.addEventListener('click', function () {
            if (wedgeConfirmationMode === 'download') {
                if (!pendingDownloadData?.id) {
                    reopenDownloadModalAfterWedge = false;
                    wedgeModal?.hide();
                    return;
                }

                pendingDownloadData.use_personal_freeleech = true;
                showToast('Freeleech Wedge will be used when this download starts.', 'success');
                updateConfirmModalFreeleechUI();
                wedgeModal?.hide();
                return;
            }

            const originalHtml = this.innerHTML;
            this.disabled = true;
            this.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Buying...';

            fetch(`${APP_BASE}/mam/buy_wedge`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        const purchased = data.amount ? `${data.amount} wedge${String(data.amount) === '1' ? '' : 's'}` : 'a wedge';
                        const bonusSuffix = data.seedbonus !== undefined ? ` Remaining: ${data.seedbonus} BP` : '';
                        showToast(`Purchased ${purchased}.${bonusSuffix}`, 'success');
                        loadMamUserData();
                        wedgeModal?.hide();
                    } else {
                        showToast(data.error || 'Failed to buy wedge', 'danger');
                    }
                })
                .catch(() => showToast('Error purchasing wedge', 'danger'))
                .finally(() => {
                    this.disabled = false;
                    this.innerHTML = originalHtml;
                });
        });
    }

    // Buy Upload Handlers
    const uploadAmountOptions = document.getElementById('upload-amount-options');
    const buyCustomUploadButton = document.getElementById('buy-custom-upload-button');
    const customUploadAmountInput = document.getElementById('custom-upload-amount');

    function submitUploadPurchase(amount, onBefore, onAfter) {
        if (typeof onBefore === 'function') onBefore();
        fetch(`${APP_BASE}/mam/buy_upload`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ amount })
        })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    const purchasedAmount = data.amount ?? amount;
                    showToast(`Purchased ${purchasedAmount} GB.`, 'success');
                    loadMamUserData();
                    bootstrap.Modal.getInstance(document.getElementById('uploadPurchaseModal'))?.hide();
                } else {
                    showToast(data.error || 'Failed', 'danger');
                }
            })
            .catch(() => showToast('Error purchasing upload', 'danger'))
            .finally(() => {
                if (typeof onAfter === 'function') onAfter();
            });
    }

    if (uploadAmountOptions) {
        uploadAmountOptions.addEventListener('click', function (e) {
            const button = e.target.closest('button');
            if (!button) return;
            const amount = button.dataset.amount;
            const buttons = uploadAmountOptions.querySelectorAll('button');
            const originalHtml = button.innerHTML;
            submitUploadPurchase(
                amount === 'max' ? 'max' : normalizeUploadAmount(amount),
                () => {
                    buttons.forEach(btn => btn.disabled = true);
                    button.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Buying...';
                },
                () => {
                    buttons.forEach(btn => btn.disabled = false);
                    button.innerHTML = originalHtml;
                }
            );
        });
    }

    if (customUploadAmountInput) {
        customUploadAmountInput.addEventListener('input', updateCustomUploadCostInfo);
    }

    if (buyCustomUploadButton && customUploadAmountInput) {
        buyCustomUploadButton.addEventListener('click', function () {
            const normalizedAmount = normalizeUploadAmount(customUploadAmountInput.value);
            customUploadAmountInput.value = normalizedAmount;
            const originalHtml = this.innerHTML;
            submitUploadPurchase(
                normalizedAmount,
                () => {
                    this.disabled = true;
                    customUploadAmountInput.disabled = true;
                    this.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Buying...';
                },
                () => {
                    this.disabled = false;
                    customUploadAmountInput.disabled = false;
                    this.innerHTML = originalHtml;
                    updateCustomUploadCostInfo();
                }
            );
        });
    }

    // ============================================================
    //  C. SEARCH & DOWNLOAD LOGIC
    // ============================================================

    const searchForm = document.getElementById("search-form");
    const resultsContainer = document.getElementById("results-container");
    const searchButton = document.getElementById("searchButton");
    const queryInput = document.getElementById('query');
    const queryClearButton = document.getElementById('query-clear-button');

    function setupSearchClearControl(input, button) {
        if (!input || !button) return () => {};

        const sync = () => button.classList.toggle('d-none', !input.value);
        input.addEventListener('input', sync);
        button.addEventListener('click', () => {
            input.value = '';
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.focus();
        });
        sync();
        return sync;
    }

    const syncQueryClearButton = setupSearchClearControl(queryInput, queryClearButton);
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
    const RESULTS_TITLE_DEFAULT_TEXT = resultsTitle ? resultsTitle.textContent || 'Results' : 'Results';
    const RESULTS_TITLE_LOADING_HTML = `<span class="d-inline-flex align-items-center"><span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>${RESULTS_TITLE_DEFAULT_TEXT}</span>`;
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
            const response = await fetch(`${APP_BASE}/update_default_search_filters`, {
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
        const allowedSortModes = new Set(
            [...resultsSortOptions].map(option => option.dataset.sortMode).filter(Boolean)
        );
        const configuredSortMode = String(window.resultsSortMode || '');
        currentResultsSort = allowedSortModes.has(configuredSortMode)
            ? configuredSortMode
            : DEFAULT_RESULTS_SORT;
        updateSortMenuUI();

        resultsSortOptions.forEach(option => {
            option.addEventListener('click', () => {
                currentResultsSort = option.dataset.sortMode || DEFAULT_RESULTS_SORT;
                updateSortMenuUI();
                applyCurrentResultsSort(resultsContainer);
                applyHideDownloadedResultsFilter();
                fetch(`${APP_BASE}/update_results_sort_mode`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ sort_mode: currentResultsSort })
                })
                    .then(response => response.json().then(data => ({ response, data })))
                    .then(({ response, data }) => {
                        if (!response.ok || data.status !== 'success') {
                            throw new Error(data.message || 'Failed to save sort preference.');
                        }
                        window.resultsSortMode = data.sort_mode;
                    })
                    .catch(() => showToast('Failed to save result sorting.', 'danger'));
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
        return fetch(`${APP_BASE}/update_result_display_fields`, {
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

    const typeCategoryRulesList = document.getElementById('type-category-rules-list');
    const addTypeCategoryBtn = document.getElementById('add-type-category-btn');
    const typeCategoriesCountBadge = document.getElementById('type-categories-count');
    const destinationPathsList = document.getElementById('destination-paths-list');
    const addDestinationPathBtn = document.getElementById('add-destination-path-btn');
    const typePathsCountBadge = document.getElementById('type-paths-count');
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

    function getSettingsCategoryOptions() {
        const settingsCategorySelect = document.getElementById('TORRENT_CLIENT_CATEGORY');
        const options = [{ value: '', label: 'Use Global Default' }];
        const seen = new Set(['']);

        if (!settingsCategorySelect) return options;

        Array.from(settingsCategorySelect.options).forEach(option => {
            const value = String(option.value || '').trim();
            const label = String(option.textContent || '').trim();
            if (!value || seen.has(value)) return;
            seen.add(value);
            options.push({ value, label: label || value });
        });

        return options;
    }

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
        const currentRows = readDestinationRows();
        const sourceEntries = destinationPathsList ? currentRows : (window.DESTINATION_PATHS || []).slice(1);
        const extras = normalizeDestinationEntries(sourceEntries, defaultPath, false)
            .filter(entry => entry.path !== defaultPath || entry.default_main_cat);
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
        row.className = 'destination-path-row mb-2';

        const defaultWrap = document.createElement('div');
        defaultWrap.className = 'form-floating destination-path-row-main-type';
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
        defaultLabel.textContent = 'Main Type';
        defaultWrap.append(defaultSelect, defaultLabel);

        const removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.className = 'btn btn-outline-danger remove-path-btn';
        removeBtn.title = 'Remove Path';
        removeBtn.setAttribute('aria-label', 'Remove Path');
        removeBtn.innerHTML = '<i class="bi bi-trash"></i>';

        const pathWrap = document.createElement('div');
        pathWrap.className = 'form-floating';
        const pathInput = document.createElement('input');
        pathInput.type = 'text';
        pathInput.className = 'form-control destination-path-input';
        pathInput.name = 'extra_dest_paths[]';
        pathInput.placeholder = '/data/media';
        pathInput.required = true;
        pathInput.value = path;
        const pathLabel = document.createElement('label');
        pathLabel.textContent = 'Destination Path';
        pathWrap.append(pathInput, pathLabel);

        row.append(defaultWrap, pathWrap, removeBtn);
        return row;
    }

    function updateDestinationPathCount() {
        if (!typePathsCountBadge || !destinationPathsList) return;
        const count = destinationPathsList.querySelectorAll('.destination-path-row').length;
        typePathsCountBadge.textContent = String(count);
        typePathsCountBadge.classList.toggle('d-none', count < 1);
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
            .filter(entry => entry.path !== fallbackPath || entry.default_main_cat);
        destinationPathEntries = normalized;

        destinationPathsList.innerHTML = '';
        normalized.forEach(entry => {
            destinationPathsList.appendChild(buildDestinationRow(entry.path, entry.default_main_cat));
        });

        updateDestinationPathCount();
        updateDestinationRowUI();
        syncConfirmDestinationOptions();
    }

    function normalizeTypeCategoryEntries(entries) {
        const normalized = [];
        const seenDefaults = new Set();

        (Array.isArray(entries) ? entries : []).forEach(entry => {
            let defaultMainCat = String(entry?.default_main_cat || '').trim();
            const defaultTorrentCategory = String(entry?.default_torrent_category || '').trim();
            if (!allowedDestinationDefaults.has(defaultMainCat) || !defaultTorrentCategory) {
                return;
            }
            if (seenDefaults.has(defaultMainCat)) {
                return;
            }
            seenDefaults.add(defaultMainCat);
            normalized.push({
                default_main_cat: defaultMainCat,
                default_torrent_category: defaultTorrentCategory
            });
        });

        return normalized;
    }

    function readTypeCategoryRows() {
        if (!typeCategoryRulesList) return [];
        return Array.from(typeCategoryRulesList.querySelectorAll('.type-category-row')).map(row => ({
            default_main_cat: row.querySelector('.type-category-default-select')?.value || '',
            default_torrent_category: row.querySelector('.type-category-select')?.value || ''
        }));
    }

    function buildTypeCategoryRow(defaultMainCat = '', defaultTorrentCategory = '') {
        const row = document.createElement('div');
        row.className = 'type-category-row mb-2';

        const defaultWrap = document.createElement('div');
        defaultWrap.className = 'form-floating type-category-row-main-type';
        const defaultSelect = document.createElement('select');
        defaultSelect.className = 'form-select type-category-default-select';
        defaultSelect.name = 'type_category_defaults[]';

        const chooseOption = document.createElement('option');
        chooseOption.value = '';
        chooseOption.textContent = 'Choose Type';
        defaultSelect.appendChild(chooseOption);

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
        defaultLabel.textContent = 'Main Type';
        defaultWrap.append(defaultSelect, defaultLabel);

        const removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.className = 'btn btn-outline-danger remove-type-category-btn';
        removeBtn.title = 'Remove Rule';
        removeBtn.setAttribute('aria-label', 'Remove Rule');
        removeBtn.innerHTML = '<i class="bi bi-trash"></i>';

        const categoryWrap = document.createElement('div');
        categoryWrap.className = 'form-floating';
        const categorySelect = document.createElement('select');
        categorySelect.className = 'form-select type-category-select';
        categorySelect.name = 'type_category_values[]';

        getSettingsCategoryOptions().forEach(optionData => {
            const option = document.createElement('option');
            option.value = optionData.value;
            option.textContent = optionData.label;
            if (optionData.value === defaultTorrentCategory) {
                option.selected = true;
            }
            categorySelect.appendChild(option);
        });

        if (defaultTorrentCategory && !Array.from(categorySelect.options).some(option => option.value === defaultTorrentCategory)) {
            const option = document.createElement('option');
            option.value = defaultTorrentCategory;
            option.textContent = defaultTorrentCategory;
            option.selected = true;
            categorySelect.appendChild(option);
        }

        const categoryLabel = document.createElement('label');
        categoryLabel.textContent = 'Default Client Category';
        categoryWrap.append(categorySelect, categoryLabel);

        row.append(defaultWrap, categoryWrap, removeBtn);
        return row;
    }

    function updateTypeCategoryCount() {
        if (!typeCategoriesCountBadge || !typeCategoryRulesList) return;
        const count = typeCategoryRulesList.querySelectorAll('.type-category-row').length;
        typeCategoriesCountBadge.textContent = String(count);
        typeCategoriesCountBadge.classList.toggle('d-none', count < 1);
    }

    function updateTypeCategoryRowUI() {
        if (!typeCategoryRulesList) return;

        const rows = Array.from(typeCategoryRulesList.querySelectorAll('.type-category-row'));
        const selectedDefaults = rows.map(row => row.querySelector('.type-category-default-select')?.value || '');

        rows.forEach((row, index) => {
            const select = row.querySelector('.type-category-default-select');
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
    }

    function renderTypeCategoryRows(entries) {
        if (!typeCategoryRulesList) return;
        const normalized = normalizeTypeCategoryEntries(entries);
        typeCategoryRulesList.innerHTML = '';
        normalized.forEach(entry => {
            typeCategoryRulesList.appendChild(buildTypeCategoryRow(
                entry.default_main_cat,
                entry.default_torrent_category
            ));
        });
        updateTypeCategoryCount();
        updateTypeCategoryRowUI();
        syncTypeCategoryOptions();
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
            updateDestinationPathCount();
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
            updateDestinationPathCount();
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

    if (typeCategoryRulesList) {
        const initialRows = readTypeCategoryRows();
        if (initialRows.length) {
            renderTypeCategoryRows(initialRows);
        } else {
            renderTypeCategoryRows(window.TYPE_SPECIFIC_TORRENT_CATEGORIES || []);
        }

        addTypeCategoryBtn?.addEventListener('click', () => {
            typeCategoryRulesList.appendChild(buildTypeCategoryRow('', ''));
            updateTypeCategoryCount();
            updateTypeCategoryRowUI();
            syncTypeCategoryOptions();
            if (!isRestoringSettings) settingsDirty = true;
        });

        typeCategoryRulesList.addEventListener('click', (event) => {
            const removeBtn = event.target.closest('.remove-type-category-btn');
            if (!removeBtn) return;

            const row = removeBtn.closest('.type-category-row');
            if (!row) return;

            row.remove();
            updateTypeCategoryCount();
            updateTypeCategoryRowUI();
            if (!isRestoringSettings) settingsDirty = true;
        });

        typeCategoryRulesList.addEventListener('change', (event) => {
            const target = event.target;
            if (!(target instanceof HTMLElement)) return;
            if (target.classList.contains('type-category-default-select')) {
                updateTypeCategoryRowUI();
            }
            if (!isRestoringSettings) settingsDirty = true;
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
                const response = await fetch(`${APP_BASE}/client/info/${hash}`, { cache: 'no-store' });
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
        const queuedPersonal = data?.use_personal_freeleech === true
            || String(data?.use_personal_freeleech ?? '').toLowerCase() === 'true'
            || String(data?.use_personal_freeleech ?? '') === '1';

        if (free) return { isFreeleech: true, reason: 'Public Freeleech' };
        if (personal) return { isFreeleech: true, reason: 'Personal Freeleech' };
        if (vipFree) return { isFreeleech: true, reason: 'VIP Freeleech' };
        if (queuedPersonal) return { isFreeleech: true, reason: 'Freeleech Wedge queued for this download' };
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
            const tooltip = disabledReason || 'Use one Freeleech Wedge when this download starts';
            tooltipTarget.setAttribute('title', tooltip);
            tooltipTarget.setAttribute('data-bs-original-title', tooltip);
            tooltipTarget.setAttribute('data-bs-toggle', 'tooltip');
            refreshTooltip(tooltipTarget);
        }
    }

    personalFlBtn?.addEventListener('click', function () {
        if (!pendingDownloadData?.id) return;

        reopenDownloadModalAfterWedge = true;
        if (confirmModalEl?.classList.contains('show')) {
            confirmModalEl.addEventListener('hidden.bs.modal', () => {
                showWedgeConfirmation('download');
            }, { once: true });
            confirmModal?.hide();
        } else {
            showWedgeConfirmation('download');
        }
    });

    if (confirmInput) {
        confirmInput.addEventListener('input', updateConfirmPathPreview);
    }

    function performSearch(queryString, isHistoryNavigation = false, options = {}) {
        const {
            preScrollToResults = false,
            showResultsHeaderLoading = false,
            skipPostSearchScroll = false
        } = options;

        activeSearchRequests += 1;
        refreshAppLogoState();
        searchButton.disabled = true;
        searchButton.innerHTML = SEARCH_BUTTON_LOADING_HTML;
        if (resultsTitle) {
            if (showResultsHeaderLoading) {
                resultsTitle.innerHTML = RESULTS_TITLE_LOADING_HTML;
            } else {
                resultsTitle.textContent = RESULTS_TITLE_DEFAULT_TEXT;
            }
        }
        hashToElementMap.clear();
        stopHardcoverEnrichmentPolling();
        const searchUrl = queryString ? `${APP_BASE}/mam/search?${queryString}` : `${APP_BASE}/mam/search`;

        if (preScrollToResults && wrapper) {
            wrapper.style.display = 'block';
            scrollToResultsWrapper();
        }

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
                if (!isHistoryNavigation && !skipPostSearchScroll) {
                    scrollToResultsWrapper();
                }
                refreshCategories();
                initializeSnatchedTorrents();
                applyHideDownloadedResultsFilter();
                startHardcoverEnrichmentLoading(resultsContainer);
            })
            .catch(error => {
                const errorText = error?.message ? `Search failed: ${error.message}` : 'Search failed.';
                wrapper.style.display = 'block';
                resultsContainer.innerHTML = `<div class="alert alert-danger">${errorText}</div>`;
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
        syncQueryClearButton();

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

    document.addEventListener('click', (event) => {
        const searchLink = event.target.closest('.hardcover-series-search-link');
        if (!searchLink) return;
        if (event.defaultPrevented) return;
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

        const href = searchLink.getAttribute('href');
        if (!href) return;

        event.preventDefault();
        triggerHaptic('search');

        const url = new URL(href, window.location.origin);
        const queryString = url.searchParams.toString();
        const newUrl = `${window.location.pathname}${queryString ? `?${queryString}` : ''}`;

        restoreFormFromURL(url.searchParams);
        history.pushState({ type: 'search', query: queryString }, '', newUrl);
        bootstrap.Modal.getOrCreateInstance(document.getElementById('bookDetailsModal')).hide();
        performSearch(queryString);
    });

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
    document.getElementById('bookDetailsModal')?.addEventListener('shown.bs.modal', function () {
        const bookModalEl = document.getElementById('bookDetailsModal');
        const detailDescriptionEl = document.getElementById('detail-description');
        if (detailDescriptionEl) {
            configureExpandableDetailDescription(
                detailDescriptionEl,
                detailDescriptionEl.dataset.expandableHtml || detailDescriptionEl.innerHTML || ''
            );
        }
        const hardcoverDescriptionEl = document.getElementById('detail-hc-description');
        if (hardcoverDescriptionEl) {
            configureExpandableHardcoverDescription(
                hardcoverDescriptionEl,
                hardcoverDescriptionEl.textContent || '',
                bookModalEl?.dataset.hardcoverDescriptionExpanded === 'true'
            );
        }
        scheduleBookDetailsColumnBalance();
    });
    document.getElementById('bookDetailsModal')?.addEventListener('hidden.bs.modal', function () {
        if (detailColumnBalanceFrame) {
            cancelAnimationFrame(detailColumnBalanceFrame);
            detailColumnBalanceFrame = 0;
        }
    });
    window.addEventListener('resize', scheduleBookDetailsColumnBalance);
    document.getElementById('mediaInfoCollapse')?.addEventListener('shown.bs.collapse', scheduleBookDetailsColumnBalance);
    document.getElementById('mediaInfoCollapse')?.addEventListener('hidden.bs.collapse', scheduleBookDetailsColumnBalance);

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
            if (event.target.closest('select') || event.target.closest('a') || event.target.closest('[data-hardcover-status-picker]')) {
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
            vip_freeleech: button.dataset.vipFreeleech ?? 0,
            personal_freeleech: button.dataset.personalFreeleech ?? 0,
            fl_vip: button.dataset.flVip ?? 0,
            use_personal_freeleech: false,
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
        const highResProxy = `${APP_BASE}/proxy_thumbnail?url=${encodeURIComponent(highResUrl)}`;
        const lowResProxy = `${APP_BASE}/proxy_thumbnail?url=${encodeURIComponent(lowResUrl)}`;

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
            hiResSrc = `${APP_BASE}/proxy_thumbnail?url=${encodeURIComponent(rawHi)}`;
            lowResSrc = `${APP_BASE}/proxy_thumbnail?url=${encodeURIComponent(rawLow)}`;
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
        configureExpandableDetailDescription(
            document.getElementById('detail-description'),
            data.description || "No description available."
        );

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
        imgEl.style.cursor = 'pointer';

        // Remove old listeners to prevent stacking if function runs multiple times
        const newImgEl = imgEl.cloneNode(true);
        imgEl.parentNode.replaceChild(newImgEl, imgEl);

        const coverLightboxModalEl = document.getElementById('coverLightboxModal');
        if (coverLightboxModalEl && !coverLightboxModalEl.dataset.outsideClickBound) {
            coverLightboxModalEl.dataset.outsideClickBound = 'true';
            coverLightboxModalEl.addEventListener('click', function (event) {
                if (event.target.closest('#modal-image-wrapper')) return;

                const modalInstance = bootstrap.Modal.getInstance(coverLightboxModalEl);
                if (modalInstance) modalInstance.hide();
            });
        }

        newImgEl.onclick = function () {
            const lightboxImg = document.getElementById('lightbox-img');
            // Use the hiResSrc we calculated for the modal
            lightboxImg.src = hiResSrc;

            const lightboxModal = bootstrap.Modal.getOrCreateInstance(coverLightboxModalEl);
            lightboxModal.show();
        };

        // Reset reference for the rest of the logic
        const activeImgEl = newImgEl;

        // 2. Reset Background Styles
        heroBg.style.filter = 'blur(50px)';
        heroBg.style.transform = 'scale(1.2)';
        heroBg.style.opacity = '0.5';

        // 3. Attach Error Handler — try Hardcover cover before falling back to placeholder
        const hcCoverProxy = data.hardcover_enrichment?.hardcover?.cover_image
            ? `${APP_BASE}/proxy_thumbnail?url=${encodeURIComponent(data.hardcover_enrichment.hardcover.cover_image)}`
            : null;
        activeImgEl.onerror = function () {
            if (hcCoverProxy && !this.dataset.triedHardcoverCover) {
                this.dataset.triedHardcoverCover = 'true';
                this.src = hcCoverProxy;
                const bg = document.getElementById('detail-hero-bg');
                if (bg) bg.style.backgroundImage = `url('${hcCoverProxy}')`;
            } else {
                handleBookCoverError(this);
            }
        };

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
        dlBtn.dataset.vipFreeleech = data.vip_freeleech;
        dlBtn.dataset.personalFreeleech = data.personal_freeleech;
        dlBtn.dataset.flVip = data.fl_vip;

        const detailCategorySelect = document.getElementById('detail-cat-select');
        if (detailCategorySelect) {
            detailCategorySelect.dataset.mainCat = data.main_cat || '';
            detailCategorySelect.value = getPreferredClientCategoryForMainCat(data.main_cat || '');
        }

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

        // Track which torrent is open so live enrichment updates can target the modal
        const bookModalEl = document.getElementById('bookDetailsModal');
        if (bookModalEl) {
            bookModalEl.dataset.currentTorrentId = String(data.id || '');
            bookModalEl.dataset.currentMainCat = String(data.main_cat || '');
            bookModalEl.dataset.currentFiletype = String(data.filetype || '');
            bookModalEl.dataset.hardcoverDescriptionExpanded = 'false';
        }

        // Render Hardcover enrichment card (may already be available or arrive later via SSE)
        renderBookDetailsHardcover(data.hardcover_enrichment);
        scheduleBookDetailsColumnBalance();
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

        fetch(`${APP_BASE}/client/add`, {
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

                    if (data.personal_freeleech_applied && downloadData.id) {
                        downloadData.personal_freeleech = 1;
                        markTorrentPersonalFreeleech(downloadData.id);
                        loadMamUserData();
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
        fetch(`${APP_BASE}/mam/buy_upload`, {
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
    const navSearchClearButton = document.getElementById('nav-search-clear-button');
    const navbarBrand = document.getElementById('navbar-brand');
    setupSearchClearControl(navSearchInput, navSearchClearButton);

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
                    mainInput.dispatchEvent(new Event('input', { bubbles: true }));
                    triggerHaptic('search');
                    mainInput.blur();
                    navSearchInput.blur();

                    const formData = new FormData(searchForm);
                    const queryParams = new URLSearchParams(formData);
                    const queryString = queryParams.toString();
                    const newUrl = `${window.location.pathname}?${queryString}`;

                    history.pushState({ type: 'search', query: queryString }, '', newUrl);
                    performSearch(queryString, false, {
                        preScrollToResults: true,
                        showResultsHeaderLoading: true,
                        skipPostSearchScroll: true
                    });
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
    const associatedForm = input.form || input.closest('form');

    // Create results dropdown container
    const container = document.createElement('div');
    container.className = 'autosuggest-results list-group shadow-sm';
    if (isMainSearchInput) {
        container.classList.add('autosuggest-results--above');
    }
    container.style.display = 'none';
    input.parentNode.appendChild(container);

    // State management for cancellation
    let debounceTimer = null;
    let abortController = null;
    let cacheProbeController = null;
    let hasIssuedInitialSearch = false;
    let activeIndex = -1;
    let originalInputValue = '';
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

    const getTypeIconClass = (type) => {
        if (type === 'author') return 'bi-person';
        if (type === 'series') return 'bi-collection';
        if (type === 'narrator') return 'bi-mic';
        return 'bi-book';
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

    const isSearchSubmitTarget = (target) => {
        if (!(target instanceof Element)) return false;
        const submitControl = target.closest('button, input[type="submit"], input[type="button"]');
        if (!submitControl) return false;

        let submitForm = submitControl.form || null;
        if (!submitForm) {
            const explicitFormId = submitControl.getAttribute('form');
            if (explicitFormId) {
                submitForm = document.getElementById(explicitFormId);
            }
        }

        if (!submitForm) return false;
        return submitForm.id === 'search-form' || submitForm.id === 'nav-search-form';
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
        activeIndex = -1;
    };

    const dismissAutosuggest = () => {
        clearTimeout(debounceTimer);
        debounceTimer = null;

        if (abortController) {
            abortController.abort();
            abortController = null;
        }
        if (cacheProbeController) {
            cacheProbeController.abort();
            cacheProbeController = null;
        }

        container.style.display = 'none';
        activeIndex = -1;
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
        activeIndex = -1;
        originalInputValue = input.value.trim();

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
            a.dataset.primaryText = primaryText;
            const authorText = String(item.author_text || '').trim();
            const showAuthorText = (primaryType === 'title' || primaryType === 'series') && authorText.length > 0;
            const badgeClass = getTypeBadgeClass(primaryType);
            const badgeLabel = getTypeLabel(primaryType);
            const iconClass = getTypeIconClass(primaryType);
            const authorMetaHtml = showAuthorText
                ? `<span class="text-body-secondary opacity-75 text-xs text-truncate" style="min-width: 0;">• ${escapeHtml(authorText)}</span>`
                : '';
            a.innerHTML = `
                <div class="d-flex align-items-center justify-content-between gap-2 w-100">
                    <div class="d-flex align-items-center gap-2 text-truncate" style="min-width: 0;">
                        <i class="bi ${iconClass} text-body-secondary flex-shrink-0" aria-hidden="true"></i>
                        <div class="d-flex align-items-center gap-1 text-truncate" style="min-width: 0;">
                            <div class="text-truncate text-sm" style="min-width: 0;">${highlightQueryMatch(primaryText, val)}</div>
                            ${authorMetaHtml}
                        </div>
                    </div>
                    <span class="badge ${badgeClass}" style="font-size: 0.65rem; flex-shrink: 0;">${badgeLabel}</span>
                </div>
            `;

            a.addEventListener('click', (e) => {
                e.preventDefault();
                dismissAutosuggest();

                input.value = primaryText;

                const mainQuery = document.getElementById('query');
                if (mainQuery && input.id !== 'query') {
                    mainQuery.value = input.value;
                }

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
            const res = await fetch(`${APP_BASE}/mam/autosuggest?${queryString}&cache_only=true`, {
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

            const res = await fetch(`${APP_BASE}/mam/autosuggest?${queryString}`, {
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

    const getItems = () => Array.from(container.querySelectorAll('.list-group-item'));

    const activateSuggestion = (index) => {
        const items = getItems();
        items.forEach(item => item.classList.remove('active'));
        if (index >= 0 && index < items.length) {
            items[index].classList.add('active');
            items[index].scrollIntoView({ block: 'nearest' });
            input.value = items[index].dataset.primaryText || '';
        } else {
            input.value = originalInputValue;
        }
        activeIndex = index;
    };

    // --- Event Listeners ---

    // 1. Input: Debounce the search
    input.addEventListener('input', (e) => {
        // Only trigger autosuggest if the input is currently focused by the user.
        // This prevents it from opening on page load if the URL has a pre-filled query.
        if (document.activeElement !== input) {
            return;
        }

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

    // 2. Keydown: Arrow navigation, Enter, Escape
    input.addEventListener('keydown', (e) => {
        const isOpen = container.style.display !== 'none';
        if (isOpen && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
            e.preventDefault();
            const items = getItems();
            if (!items.length) return;
            if (e.key === 'ArrowDown') {
                activateSuggestion(activeIndex < items.length - 1 ? activeIndex + 1 : 0);
            } else {
                // ArrowUp: -1 wraps to last; 0 goes back to -1 (restores typed text)
                activateSuggestion(activeIndex === 0 ? -1 : (activeIndex < 0 ? items.length - 1 : activeIndex - 1));
            }
            return;
        }
        if (e.key === 'Enter') {
            if (activeIndex >= 0) {
                getItems()[activeIndex]?.click();
                return;
            }
            dismissAutosuggest();
        }
        else if (e.key === 'Escape') {
            dismissAutosuggest();
        }
    });

    // 2b. Container keydown: arrow navigation when list items are focused
    container.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            const items = getItems();
            if (!items.length) return;
            if (e.key === 'ArrowDown') {
                activateSuggestion(activeIndex < items.length - 1 ? activeIndex + 1 : 0);
            } else {
                if (activeIndex === 0) {
                    activateSuggestion(-1);
                    input.focus();
                } else {
                    activateSuggestion(activeIndex < 0 ? items.length - 1 : activeIndex - 1);
                }
            }
        } else if (e.key === 'Enter') {
            if (activeIndex >= 0) getItems()[activeIndex]?.click();
        } else if (e.key === 'Escape') {
            dismissAutosuggest();
            input.focus();
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
        if (isSearchSubmitTarget(e.target)) {
            hideAllAutosuggestContainers();
            return;
        }
        hideAllAutosuggestContainers();
        if (e.cancelable) e.preventDefault();
        e.stopImmediatePropagation();
    }, true);

    associatedForm?.addEventListener('submit', () => {
        dismissAutosuggest();
    });

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
