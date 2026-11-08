const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/main.js', 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('const REL_PATH_TOKEN_NAME_RE'), source.indexOf('function getRelPathTemplateValue(')), context);
vm.runInContext(source.slice(source.indexOf('const REL_PATH_TOKENS = ['), source.indexOf('/**\n * The one template editor')), context);

test('templates split into literal text and token groups', () => {
    const parts = context.splitRelPathTemplate('{Author}/Vol{ #SeriesNumber:02} - {Title}{Unknown}{ (Year)}');
    assert.deepEqual([...parts].map(p => p.group ? `[${p.group.raw}]` : p.text),
        ['[{Author}]', '/Vol', '[{ #SeriesNumber:02}]', ' - ', '[{Title}]', '{Unknown}', '[{ (Year)}]']);
    // Text glued to a token name is not a token, so it stays literal.
    assert.deepEqual([...context.splitRelPathTemplate("{VolSeriesNumber}")].map(p => p.text), ['{VolSeriesNumber}']);
});
