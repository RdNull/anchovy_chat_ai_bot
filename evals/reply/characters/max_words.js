// Per-character reply length cap for the `word-count` assert.
// A plain {min, max} object is not templated by promptfoo, so the limit comes from a script value.
const { characterCode } = require('./character.js');

const MAX_WORDS = { whyzzzy: 20, chatzhpt: 35 };
const DEFAULT_MAX_WORDS = 25;

module.exports.limits = () => ({
    min: 1,
    max: MAX_WORDS[characterCode()] ?? DEFAULT_MAX_WORDS,
});
