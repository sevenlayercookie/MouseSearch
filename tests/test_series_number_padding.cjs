const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/main.js', 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('const REL_PATH_TOKEN_NAME_RE'), source.indexOf('function getRelPathTemplateValue(')), context);
vm.runInContext(source.slice(source.indexOf('function normalizeRelPathTemplate('), source.indexOf('const MAIN_CAT_DISPLAY_NAMES')), context);
vm.runInContext(source.slice(source.indexOf('function normalizeSeriesNumber('), source.indexOf('function getPrimarySeriesInfo(')), context);
test('preview padding matches server semantics, including fractions and missing numbers', () => {
    for (const [raw, expected] of [['1','01'], ['01','01'], ['23','23'], ['123','123'], ['1.5','01.5'], ['0.5','00.5'], ['0','00'], ['', ''], [null,''], ['-1',''], ['Omnibus','Omnibus']]) {
        const values = {seriesNumber:context.normalizeSeriesNumber(raw)};
        assert.equal(context.buildRelativePathFromTemplate('{SeriesNumber:02}',values), expected);
        assert.equal(context.buildRelativePathFromTemplate('Book{ #SeriesNumber:02}',values), 'Book' + (expected ? ' #' + expected : ''));
    }
});
test('padding is opt-in and bounded; tag detection works with padded tokens', () => {
    const values = {seriesNumber:'1.5', title:'Book'};
    for (const [template, expected] of [['{SeriesNumber:03}', '001.5'], ['{SeriesNumber}','1.5'], ['{SeriesNumber:010}', '0000000001.5'], ['{SeriesNumber:011}', '1.5:011'], ['{Title:02}', 'Book:02']]) {
        assert.equal(context.buildRelativePathFromTemplate(template, values), expected);
    }
    assert.equal(context.templateHasToken('{ #SeriesNumber:02}', '{SeriesNumber}'), true);
});
