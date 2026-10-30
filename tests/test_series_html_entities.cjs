const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/main.js', 'utf8');
const assigned = [];
const document = {createElement() { return {set innerHTML(entity) {
    assigned.push(entity);
    const named = {'&amp;':'&', '&quot;':'"', '&lt;':'<', '&gt;':'>'};
    this.value = named[entity] || (entity.startsWith('&#x') ? String.fromCodePoint(parseInt(entity.slice(3),16)) : entity.startsWith('&#') ? String.fromCodePoint(parseInt(entity.slice(2),10)) : entity);
}};}};
const context = vm.createContext({document, console});
vm.runInContext(source.slice(source.indexOf('function normalizeSeriesNumber('), source.indexOf('function formatPrimarySeriesLabel(')), context);
vm.runInContext(source.slice(source.indexOf('function sanitizeFilename('), source.indexOf('const DEFAULT_REL_PATH_TEMPLATE')), context);
test('series preview decodes before filename sanitization', () => {
    const series = context.getPrimarySeriesInfo(JSON.stringify({'1':["Derek&#039;s &amp; Depot &#x2F; Annex", '1']}));
    assert.equal(context.sanitizeFilename(series.name), "Derek's & Depot  Annex");
});
test('literal metadata markup is never assigned to innerHTML', () => {
    assigned.length = 0;
    assert.equal(context.decodeMamSeriesName('<img src=x onerror=alert(1)>&amp;'), '<img src=x onerror=alert(1)>&');
    assert.deepEqual(assigned, ['&amp;']);
    assert.equal(context.decodeMamSeriesName('A &amp;#039; B'), 'A &#039; B'); // Decode once, matching Python.
});
