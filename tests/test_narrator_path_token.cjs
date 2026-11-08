const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/main.js', 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('const REL_PATH_TOKEN_NAME_RE'), source.indexOf('/** True when the template uses a token')), context);
vm.runInContext(source.slice(source.indexOf('function normalizeRelPathTemplate('), source.indexOf('const MAIN_CAT_DISPLAY_NAMES')), context);
test('narrator preview renders optional text and omits absent narrators', () => {
    assert.equal(context.buildRelativePathFromTemplate('{Author}/{Title}{ (Narrator)}', {author:'A',title:'Book',narrator:'Stephen Fry'}), 'A/Book (Stephen Fry)');
    assert.equal(context.buildRelativePathFromTemplate('{Title}{ (Narrator)}', {title:'Book'}), 'Book');
});
