// Shared assertion, applied to every memory case via defaultTest.
//
// Mirrors the code-side attribution guard (src/memory/dedup.py), including its incumbent
// logic — not just its normalization.
//
// A cross-participant duplicate has two very different causes, and only one of them is a bug:
//
//   1. RE-EMIT COPY — the fact was already stored on someone in `current_memory` and the
//      model copied or relocated it during the re-emit. This is the bug the attribution
//      work exists to kill. FAILS the case.
//
//   2. PARALLEL FACTS — two participants independently did the same thing in the same
//      window ("@bob: Отлично, я буду" / "@charlie: Я тоже"), so both entries are
//      individually correct and happen to normalize to the same string. Neither was an
//      incumbent. REPORTED, not failed — failing here would punish correct output.
//
// The 26-08-20 run demonstrated case 2 on `existing_memory_update`: v4 wrote
// «согласился на встречу в 18:00 по проекту Зенит» for both @charlie and @bob, both true.
// v3 dodged it only by phrasing the two entries differently.

const {norm, entries, shapeAnomalies, incumbentMap, asMemory, parse} = require('./memory_lib.js');

module.exports = function (output, context) {
    const {memory, error} = parse(output);
    if (error) {
        return {pass: false, score: 0, reason: error};
    }

    const anomalies = shapeAnomalies(memory);
    const shapeNote = anomalies.length ? ` [shape: ${anomalies.join('; ')}]` : '';

    const incumbent = incumbentMap(asMemory(context && context.vars && context.vars.current_memory));

    const holders = new Map();
    for (const {nick, text} of entries(memory)) {
        const key = norm(text);
        if (!key) {
            continue;
        }
        if (!holders.has(key)) {
            holders.set(key, {nicks: new Set(), sample: text});
        }
        holders.get(key).nicks.add(nick);
    }

    const failures = [];
    const parallel = [];
    for (const [key, {nicks, sample}] of holders) {
        if (nicks.size < 2) {
            continue;
        }
        const old = incumbent.get(key) || new Set();
        const carried = [...nicks].filter((n) => old.has(n));
        const where = [...nicks].join(', ');

        if (carried.length === 1) {
            failures.push(`re-emit copy: "${sample}" was stored on ${carried[0]}, now also on ${where}`);
        } else if (carried.length > 1) {
            failures.push(`duplicate carried forward unresolved: "${sample}" on ${where}`);
        } else {
            parallel.push(`"${sample}" on ${where}`);
        }
    }

    const parallelNote = parallel.length ? ` [parallel facts, not failed: ${parallel.join('; ')}]` : '';

    if (failures.length) {
        return {pass: false, score: 0, reason: `${failures.join(' | ')}${parallelNote}${shapeNote}`};
    }
    return {pass: true, score: 1, reason: `no relocated facts${parallelNote}${shapeNote}`};
};
