// Shared assertion, applied to every memory case via defaultTest.
//
// Code owns the clock now: `src/memory/decay.py` stamps an entry's age in a sidecar the
// model never sees, and `v4.j2` no longer documents a timestamp field at all. A stamped
// entry means the prompt still leaks the old contract — production's structured output
// would coerce it to a string like "26-08-18 09:16: ездил в Лондон", quietly poisoning
// the normalized key the attribution guard and the sidecar both index on.

const {entries, asArray, parse} = require('./memory_lib.js');

const TIMESTAMP = /\d{2}-\d{2}-\d{2}[ T]\d{2}:\d{2}/;

module.exports = function (output) {
    const {memory, error} = parse(output);
    if (error) {
        return {pass: false, score: 0, reason: error};
    }

    const stamped = entries(memory)
        .filter((e) => TIMESTAMP.test(e.text))
        .map((e) => `${e.nick}.${e.field}: "${e.text}"`);

    const participants = (memory && memory.participants) || {};
    const objects = [];
    for (const [nick, info] of Object.entries(participants)) {
        if (!info || typeof info !== 'object') {
            continue;
        }
        for (const r of asArray(info.recent)) {
            if (r && typeof r === 'object' && r.last_seen_at !== undefined) {
                objects.push(`${nick}.recent still carries last_seen_at`);
                break;
            }
        }
    }

    const failures = [...stamped, ...objects];
    if (failures.length) {
        return {pass: false, score: 0, reason: `timestamped entries: ${failures.join(' | ')}`};
    }
    return {pass: true, score: 1, reason: 'no timestamps in participant entries'};
};
