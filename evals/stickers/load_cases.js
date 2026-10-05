// Test loader for the sticker describer suite (spec 009).
// Reads public/cases.yaml and, when present, fixtures/cases.yaml (git-ignored);
// pairs each case with its generated frames in frames/ (see extract_frames.py).

const fs = require('fs');
const path = require('path');
const yaml = require('js-yaml');

const ROOT = __dirname;
const FRAMES_DIR = path.join(ROOT, 'frames');

const SETS = [
    { visibility: 'public', cases: path.join(ROOT, 'public', 'cases.yaml') },
    { visibility: 'private', cases: path.join(ROOT, 'fixtures', 'cases.yaml') },
];

function framesFor(uniqueId) {
    const prefix = `${uniqueId}_`;
    return fs
        .readdirSync(FRAMES_DIR)
        .filter((name) => name.startsWith(prefix))
        .sort((a, b) => parseInt(a.slice(prefix.length), 10) - parseInt(b.slice(prefix.length), 10))
        .map((name) => `file://${path.join('frames', name)}`);
}

module.exports = function () {
    const tests = [];
    for (const { visibility, cases } of SETS) {
        if (!fs.existsSync(cases)) {
            continue;
        }
        for (const c of yaml.load(fs.readFileSync(cases, 'utf8'))) {
            const uniqueId = path.parse(c.file).name;
            const frames = framesFor(uniqueId);
            if (frames.length === 0) {
                throw new Error(`No frames for ${c.id} (${c.file}); run extract_frames.py`);
            }
            const kind = path.extname(c.file) === '.webp' ? 'static' : 'animated';
            tests.push({
                description: c.id,
                metadata: { visibility, kind, note: c.note || '' },
                vars: {
                    // Scalar vars, not a list: promptfoo expands a list var into one test per item.
                    ...Object.fromEntries(frames.map((frame, i) => [`frame_${i}`, frame])),
                    kind,
                    must: c.must,
                    bonus: c.bonus || '',
                    must_not: c.must_not || '',
                },
            });
        }
    }
    return tests;
};
