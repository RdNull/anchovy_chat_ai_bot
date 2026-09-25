// Reads the shipped persona so the eval cannot drift from production.
// The character is picked per run: `CHARACTER=chatzhpt promptfoo eval -c ./reply/characters`.
// Defaults to whyzzzy. promptfoo var files must return { output: <value> }.
const path = require('path');
const fs = require('fs');
const yaml = require('js-yaml');

const DEFAULT_CHARACTER = 'whyzzzy';

const characterCode = () => {
    const code = process.env.CHARACTER || DEFAULT_CHARACTER;
    if (!/^[a-z0-9_]+$/.test(code)) {
        throw new Error(`CHARACTER must match [a-z0-9_]+, got: ${code}`);
    }
    return code;
};

module.exports = () => {
    const code = characterCode();
    const file = path.resolve(__dirname, `../../../src/characters/repository/${code}.yaml`);
    if (!fs.existsSync(file)) {
        throw new Error(`CHARACTER=${code}: no such character at ${file}`);
    }
    const character = yaml.load(fs.readFileSync(file, 'utf8'));
    return { output: character.prompt };
};

module.exports.characterCode = characterCode;
