// Run with: node --test tests/test_search_sort.cjs
// Execute the production handlers with controlled network completion order.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { test } = require('node:test');
const source = fs.readFileSync(process.env.SEARCH_JS || 'static/js/main.js', 'utf8');
const perform = source.slice(source.indexOf('    function performSearch('), source.indexOf('    function restoreFormFromURL('));
const handlerStart = source.indexOf('                currentResultsSort = option.dataset.sortMode');
const handler = source.slice(handlerStart, source.indexOf('                fetch(`${APP_BASE}/update_results_sort_mode`', handlerStart));
function setup(query = 'query=A', sort = 'seeders_desc') {
    const pending = [];
    const c = {
        URLSearchParams, AbortController, APP_BASE: '', currentResultsSort: sort,
        DEFAULT_RESULTS_SORT: 'quality_desc', lastPerformedQuery: query,
        lastSearchMamSort: query === null ? null : sort,
        latestSearchQuery: query, requestedMamSort: sort, searchAbortController: null,
        latestSearchRequestId: 0, activeSearchRequests: 0, searchButton: {}, resultsTitle: {},
        wrapper: { style: {} }, resultsContainer: { innerHTML: 'old', querySelectorAll: () => [] },
        hashToElementMap: new Map(), window: {}, bootstrap: {},
        SEARCH_BUTTON_LOADING_HTML: '', SEARCH_BUTTON_DEFAULT_HTML: '',
        RESULTS_TITLE_LOADING_HTML: '', RESULTS_TITLE_DEFAULT_TEXT: '',
        mamSortFor: s => ['quality_desc', 'author_asc', 'author_desc'].includes(s) ? 'default' : s,
        // Deliberately allow aborted responses to complete, testing the stale-response guard too.
        fetch: (url, options) => new Promise((resolve, reject) => pending.push({ url, options, resolve, reject })),
        getSelectedResultFields: () => []
    };
    for (const name of ['refreshAppLogoState', 'stopHardcoverEnrichmentPolling', 'scrollToResultsWrapper',
        'applyCurrentResultsSort', 'localizeDates', 'applyResultDisplayFields', 'refreshCategories',
        'initializeSnatchedTorrents', 'applyHideDownloadedResultsFilter', 'startHardcoverEnrichmentLoading',
        'updateSortMenuUI']) c[name] = () => {};
    vm.createContext(c);
    vm.runInContext(perform, c);
    return { c, pending, click(mode) {
        c.option = { dataset: { sortMode: mode } };
        vm.runInContext('{'+handler+'}', c);
    } };
}
async function finish(request) {
    request.resolve({ text: async () => request.url });
    await new Promise(resolve => setImmediate(resolve));
}
test('returning to the displayed sort supersedes the pending different sort', async () => {
    const t = setup();
    t.click('snatches_desc');
    t.click('seeders_desc');
    assert.equal(t.pending.length, 2);
    assert.equal(t.pending[0].options.signal.aborted, true);
    await finish(t.pending[1]);
    await finish(t.pending[0]);
    assert.equal(t.c.lastSearchMamSort, 'seeders_desc');
    assert.match(t.c.resultsContainer.innerHTML, /sort_mode=seeders_desc/);
    assert.equal(t.c.activeSearchRequests, 0);
});
test('sort changes retain the currently loading query', async () => {
    const t = setup('query=old');
    t.c.performSearch('query=new');
    t.click('snatches_desc');
    assert.match(t.pending[1].url, /query=new/);
    await finish(t.pending[1]);
    await finish(t.pending[0]);
    assert.equal(t.c.lastPerformedQuery, 'query=new');
    assert.equal(t.c.lastSearchMamSort, 'snatches_desc');
});
test('sort changes during the first search request the right subset', async () => {
    const t = setup(null);
    t.c.performSearch('query=first');
    t.click('snatches_desc');
    assert.equal(t.pending.length, 2);
    await finish(t.pending[0]);
    assert.equal(t.c.searchButton.disabled, true);
    await finish(t.pending[1]);
    assert.equal(t.c.lastSearchMamSort, 'snatches_desc');
    assert.equal(t.c.searchButton.disabled, false);
});
test('client-only sorts share the default upstream request', async () => {
    const t = setup(null, 'quality_desc');
    t.c.performSearch('query=A');
    t.click('author_asc');
    assert.equal(t.pending.length, 1);
    await finish(t.pending[0]);
    assert.equal(t.c.lastSearchMamSort, 'default');
    assert.equal(t.c.currentResultsSort, 'author_asc');
});
test('stale failures cannot replace newer results', async () => {
    const t = setup();
    t.click('snatches_desc');
    t.click('size_desc');
    await finish(t.pending[1]);
    t.pending[0].reject(new Error('old request failed'));
    await new Promise(resolve => setImmediate(resolve));
    assert.match(t.c.resultsContainer.innerHTML, /sort_mode=size_desc/);
    assert.equal(t.c.searchAbortController, null);
});
test('same-query refresh retains valid download categories; new queries reset them', async () => {
    const t = setup();
    let rows = [{ id: 'catSelect123', value: 'chosen' }];
    t.c.resultsContainer.querySelectorAll = selector => selector === 'select.category-dropdown' ? rows : [];
    Object.defineProperty(t.c.resultsContainer, 'innerHTML', { set() {
        rows = [{ id: 'catSelect123', value: 'default', options: [{ value: 'default' }, { value: 'chosen' }] }];
    } });
    t.click('snatches_desc');
    await finish(t.pending[0]);
    assert.equal(rows[0].value, 'chosen');
    t.c.performSearch('query=B');
    await finish(t.pending[1]);
    assert.equal(rows[0].value, 'default');
});
