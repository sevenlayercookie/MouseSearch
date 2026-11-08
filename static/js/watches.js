/*
 * Watches (beta) panel. Loaded only when WATCHES_ENABLED is on; talks to /api/watches.
 */
(function () {
    'use strict';

    const API = `${(window.APP_BASE || '').replace(/\/+$/, '')}/api/watches`;
    const COST_LABELS = { free: 'freeleech only', wedge: 'freeleech or wedge', ratio: 'freeleech, wedge or ratio' };
    const ACTION_BADGES = {
        grab: 'text-bg-success', waiting: 'text-bg-warning', baseline: 'text-bg-secondary',
        have: 'text-bg-light', seen: 'text-bg-light', error: 'text-bg-danger',
    };
    const EVENT_BADGES = { grabbed: 'text-bg-success', baseline: 'text-bg-secondary', error: 'text-bg-danger' };
    const state = { watches: [], allowWedges: false, returnView: 'watches-list-view' };

    const $ = (id) => document.getElementById(id);

    function toast(message, type = 'primary') {
        if (typeof showToast === 'function') showToast(message, type);
    }

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, (c) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        }[c]));
    }

    function relativeTime(iso) {
        if (!iso) return 'never';
        const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
        if (!Number.isFinite(seconds)) return iso;
        if (seconds < 60) return 'just now';
        if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
        if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
        return `${Math.floor(seconds / 86400)} d ago`;
    }

    async function api(path = '', options = {}) {
        const init = { method: options.method || 'GET', headers: {} };
        if (options.body !== undefined) {
            init.headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(options.body);
        }
        const response = await fetch(`${API}${path}`, init);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
        return data;
    }

    function showView(id) {
        document.querySelectorAll('#watchesOffcanvas .watches-view').forEach((el) => {
            el.classList.toggle('d-none', el.id !== id);
        });
    }

    function setBusy(button, busy, label) {
        if (!button) return;
        if (busy) {
            button.dataset.label = button.innerHTML;
            button.disabled = true;
            button.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>${escapeHtml(label || '')}`;
        } else {
            button.disabled = false;
            if (button.dataset.label) button.innerHTML = button.dataset.label;
        }
    }

    // ------------------------------------------------------------------ list
    async function loadWatches() {
        try {
            const data = await api();
            state.watches = data.watches || [];
            state.allowWedges = !!data.allow_wedges;
            $('watches-status').innerHTML =
                `Grabbed in the last 24h: <strong>${data.grabs_last_24h}</strong> of ${data.daily_limit} allowed` +
                ` &middot; wedges ${data.allow_wedges ? 'allowed' : '<span class="text-body-secondary">off</span>'}`;
            renderList();
        } catch (error) {
            $('watches-status').textContent = error.message;
        }
    }

    function describe(watch) {
        const parts = [`"${watch.query}"`, `in ${(watch.search_in || []).join('/')}`, COST_LABELS[watch.cost] || watch.cost,
            `every ${watch.interval_minutes} min`];
        if (watch.title_regex) parts.push(`titles /${watch.title_regex}/`);
        return parts.join(' · ');
    }

    function renderList() {
        const list = $('watches-list');
        list.innerHTML = '';
        $('watches-empty').classList.toggle('d-none', state.watches.length > 0);
        state.watches.forEach((watch) => {
            const item = document.createElement('div');
            item.className = 'list-group-item';
            item.innerHTML = `
              <div class="d-flex justify-content-between align-items-start gap-2">
                <div class="me-auto">
                  <div class="fw-semibold">${escapeHtml(watch.name)}</div>
                  <div class="small text-body-secondary">${escapeHtml(describe(watch))}</div>
                  <div class="small mt-1">
                    <span class="text-body-secondary">Last run ${escapeHtml(relativeTime(watch.last_run_at))}${watch.last_summary ? ':' : ''}</span>
                    ${escapeHtml(watch.last_summary || '')}
                    ${watch.baseline_done ? '' : '<span class="badge text-bg-secondary ms-1">first run will only record existing</span>'}
                  </div>
                </div>
                <div class="form-check form-switch m-0" title="Enabled">
                  <input class="form-check-input" type="checkbox" data-watch-toggle="${watch.id}" ${watch.enabled ? 'checked' : ''}>
                </div>
              </div>
              <div class="d-flex flex-wrap gap-1 mt-2">
                <button type="button" class="btn btn-outline-secondary btn-sm" data-watch-action="test" data-watch-id="${watch.id}">Preview</button>
                <button type="button" class="btn btn-outline-primary btn-sm" data-watch-action="run" data-watch-id="${watch.id}">Run now</button>
                <button type="button" class="btn btn-outline-secondary btn-sm" data-watch-action="events" data-watch-id="${watch.id}">History</button>
                <button type="button" class="btn btn-outline-secondary btn-sm" data-watch-action="edit" data-watch-id="${watch.id}">Edit</button>
              </div>`;
            list.appendChild(item);
        });
    }

    // ------------------------------------------------------------------ form
    function openForm(watch, prefill) {
        const source = Object.assign({
            id: '', name: '', enabled: true, query: '', search_in: ['author'], title_regex: '',
            main_cat: [], language_ids: ['1'], cost: 'free', interval_minutes: 60, max_grabs_per_run: 2,
            grab_existing: false, torrent_category: '',
        }, watch || {}, prefill || {});
        $('watch-form-title').textContent = watch ? `Edit watch: ${watch.name}` : 'New watch';
        $('watch-id').value = watch ? watch.id : '';
        $('watch-name').value = source.name;
        $('watch-query').value = source.query;
        const fields = new Set(source.search_in || []);
        document.querySelectorAll('.watch-search-in').forEach((cb) => { cb.checked = fields.has(cb.value); });
        $('watch-main-cat').value = (source.main_cat || [])[0] || '';
        $('watch-language').value = (source.language_ids || [])[0] || '';
        (document.querySelector(`input[name="watch-cost"][value="${source.cost}"]`) || $('watch-cost-free')).checked = true;
        $('watch-interval').value = source.interval_minutes;
        $('watch-max-grabs').value = source.max_grabs_per_run;
        $('watch-title-regex').value = source.title_regex || '';
        $('watch-category').value = source.torrent_category || '';
        $('watch-grab-existing').checked = !!source.grab_existing;
        $('watch-enabled').checked = !!source.enabled;
        $('watch-delete').classList.toggle('d-none', !watch);
        $('watch-form-error').classList.add('d-none');
        updateWedgeNote();
        showView('watch-form-view');
    }

    function updateWedgeNote() {
        const cost = document.querySelector('input[name="watch-cost"]:checked')?.value || 'free';
        const show = !state.allowWedges && cost !== 'free';
        $('watch-wedges-off-note').classList.toggle('d-none', !show);
        document.querySelector('#watch-wedges-off-note .watch-ratio-only')?.classList.toggle('d-none', cost !== 'ratio');
    }

    function readForm() {
        const mainCat = $('watch-main-cat').value;
        const language = $('watch-language').value;
        return {
            name: $('watch-name').value.trim(),
            query: $('watch-query').value.trim(),
            search_in: [...document.querySelectorAll('.watch-search-in:checked')].map((cb) => cb.value),
            main_cat: mainCat ? [mainCat] : [],
            language_ids: language ? [language] : [],
            cost: document.querySelector('input[name="watch-cost"]:checked')?.value || 'free',
            interval_minutes: $('watch-interval').value,
            max_grabs_per_run: $('watch-max-grabs').value,
            title_regex: $('watch-title-regex').value.trim(),
            torrent_category: $('watch-category').value.trim(),
            grab_existing: $('watch-grab-existing').checked,
            enabled: $('watch-enabled').checked,
        };
    }

    function formError(message) {
        const el = $('watch-form-error');
        el.textContent = message;
        el.classList.toggle('d-none', !message);
    }

    async function saveForm(event) {
        event.preventDefault();
        const id = $('watch-id').value;
        const button = $('watch-save');
        setBusy(button, true, 'Saving');
        try {
            const data = await api(id ? `/${id}` : '', { method: id ? 'PUT' : 'POST', body: readForm() });
            toast(data.message || 'Saved.', 'success');
            await loadWatches();
            showView('watches-list-view');
        } catch (error) {
            formError(error.message);
        } finally {
            setBusy(button, false);
        }
    }

    async function deleteWatch() {
        const id = $('watch-id').value;
        if (!id || !window.confirm('Delete this watch and its history?')) return;
        try {
            await api(`/${id}`, { method: 'DELETE' });
            toast('Watch deleted.', 'success');
            await loadWatches();
            showView('watches-list-view');
        } catch (error) {
            formError(error.message);
        }
    }

    function prefillFromSearch() {
        const form = document.getElementById('search-form');
        if (!form) return {};
        const fd = new FormData(form);
        const query = String(fd.get('query') || '').trim();
        const searchIn = ['title', 'author', 'series', 'narrator'].filter((f) => fd.get(`search_in_${f}`));
        const mainCat = fd.getAll('main_cat').map(String).find((v) => v === '13' || v === '14');
        const language = fd.getAll('language_ids').map(String).filter(Boolean);
        return {
            name: query,
            query,
            search_in: searchIn.length ? searchIn : ['author'],
            main_cat: mainCat ? [mainCat] : [],
            language_ids: language.length === 1 ? language : [],
        };
    }

    // ------------------------------------------------------------------ preview / run
    function renderResult(summary, title) {
        $('watch-result-title').textContent = title;
        const text = summary.error ? `Error: ${summary.error}` : (summary.summary_text || '');
        let lead = summary.dry_run ? 'Preview (nothing was grabbed): ' : '';
        if (summary.baseline_run && !summary.error) {
            lead += summary.dry_run
                ? 'This watch has not run yet, so its first run will only record these. '
                : 'First run: existing results were recorded, nothing was grabbed. ';
        }
        $('watch-result-summary').textContent = lead + text;
        const b = summary.budget;
        $('watch-result-budget').textContent = b
            ? (b.blocker ? `Grabbing is paused: ${b.blocker}.` :
                `Limits before this run: ${b.unsat_slots} unsat slot(s), ${b.daily_remaining} grab(s) left today, ` +
                `${b.wedges} spendable wedge(s), ${b.ratio_allowance_gb} GB above your ratio floor.`)
            : '';
        const list = $('watch-result-items');
        list.innerHTML = '';
        const items = summary.items || [];
        if (!items.length) {
            list.innerHTML = '<li class="list-group-item text-body-secondary">No matching results.</li>';
        }
        items.forEach((item) => {
            const li = document.createElement('li');
            li.className = 'list-group-item';
            li.innerHTML = `
              <div class="d-flex justify-content-between gap-2">
                <span>${escapeHtml(item.title)} ${item.free ? '<span class="badge text-bg-success ms-1">FL</span>' : ''}</span>
                <span class="badge ${ACTION_BADGES[item.action] || 'text-bg-secondary'} align-self-start">${escapeHtml(item.action)}</span>
              </div>
              <div class="text-body-secondary">${escapeHtml([item.author, item.size, item.added].filter(Boolean).join(' · '))}</div>
              ${item.detail ? `<div class="text-body-secondary fst-italic">${escapeHtml(item.detail)}</div>` : ''}`;
            list.appendChild(li);
        });
        showView('watch-result-view');
    }

    async function runAction(path, body, button, busyLabel, title, returnView) {
        setBusy(button, true, busyLabel);
        try {
            const summary = await api(path, { method: 'POST', body });
            state.returnView = returnView;
            renderResult(summary, title);
            if (!summary.dry_run) loadWatches();
        } catch (error) {
            toast(error.message, 'danger');
        } finally {
            setBusy(button, false);
        }
    }

    async function loadEvents(watch) {
        $('watch-events-title').textContent = `History: ${watch.name}`;
        const list = $('watch-events-list');
        list.innerHTML = '';
        try {
            const events = (await api(`/${watch.id}/events`)).events || [];
            $('watch-events-empty').classList.toggle('d-none', events.length > 0);
            events.forEach((ev) => {
                const li = document.createElement('li');
                li.className = 'list-group-item';
                li.innerHTML = `
                  <div class="d-flex justify-content-between gap-2">
                    <span><span class="badge ${EVENT_BADGES[ev.kind] || 'text-bg-secondary'} me-2">${escapeHtml(ev.kind)}</span>${escapeHtml(ev.title || '')}</span>
                    <span class="text-body-secondary text-nowrap" title="${escapeHtml(ev.ts)}">${escapeHtml(relativeTime(ev.ts))}</span>
                  </div>
                  ${ev.detail ? `<div class="text-body-secondary">${escapeHtml(ev.detail)}</div>` : ''}`;
                list.appendChild(li);
            });
            showView('watch-events-view');
        } catch (error) {
            toast(error.message, 'danger');
        }
    }

    // ------------------------------------------------------------------ wiring
    function bind() {
        const panel = $('watchesOffcanvas');
        if (!panel) return;

        panel.addEventListener('show.bs.offcanvas', () => { showView('watches-list-view'); loadWatches(); });
        $('watches-new').addEventListener('click', () => openForm(null));
        $('watches-new-from-search').addEventListener('click', () => openForm(null, prefillFromSearch()));
        $('watch-form').addEventListener('submit', saveForm);
        $('watch-delete').addEventListener('click', deleteWatch);
        document.querySelectorAll('input[name="watch-cost"]').forEach((r) => r.addEventListener('change', updateWedgeNote));
        $('watch-preview').addEventListener('click', (event) => {
            const body = readForm();
            runAction('/preview', Object.assign(body, { id: $('watch-id').value || undefined }), event.currentTarget,
                'Searching', `Preview: ${body.name || body.query}`, 'watch-form-view');
        });

        panel.addEventListener('click', (event) => {
            if (event.target.closest('[data-watch-back]')) {
                const fromResult = !$('watch-result-view').classList.contains('d-none');
                showView(fromResult ? state.returnView : 'watches-list-view');
                return;
            }
            const button = event.target.closest('[data-watch-action]');
            if (!button) return;
            const watch = state.watches.find((w) => String(w.id) === button.dataset.watchId);
            if (!watch) return;
            switch (button.dataset.watchAction) {
                case 'test':
                    runAction(`/${watch.id}/test`, {}, button, '', `Preview: ${watch.name}`, 'watches-list-view');
                    break;
                case 'run':
                    if (window.confirm(`Run "${watch.name}" now? Matches that pass your limits will be sent to your client.`)) {
                        runAction(`/${watch.id}/run`, {}, button, '', `Run: ${watch.name}`, 'watches-list-view');
                    }
                    break;
                case 'events': loadEvents(watch); break;
                case 'edit': openForm(watch); break;
                default: break;
            }
        });

        panel.addEventListener('change', async (event) => {
            const toggle = event.target.closest('[data-watch-toggle]');
            if (!toggle) return;
            try {
                await api(`/${toggle.dataset.watchToggle}`, { method: 'PUT', body: { enabled: toggle.checked } });
                loadWatches();
            } catch (error) {
                toggle.checked = !toggle.checked;
                toast(error.message, 'danger');
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        bind();
    }
})();
