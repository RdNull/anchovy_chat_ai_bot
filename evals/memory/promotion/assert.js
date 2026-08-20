// promotion — the one recurrence signal the whole decay design leans on.
//
// `recent → traits` is a structural move, so `src/memory/decay.py:reconcile` can detect it
// by diffing two snapshots. Nothing else in the system can tell recurrence from re-emission:
// the prompt instructs the model to carry everything forward, so "present in both snapshots"
// only means still stored. If the model stops promoting, decay has no recurrence input left
// and `cycles` degrades to time-since-birth.
//
// Seeded memory holds «опоздал на созвон» in @imdemur's `recent`. The window repeats the
// lateness four times across two separate calls. The fact must land in `traits` as a
// property and leave `recent` — a copy in both places is the half-completed move that
// `_drop_traits_recent_overlap` cleans up in production, so it is scored, not failed.

const {norm, entries, parse, grade} = require('../memory_lib.js');

const LATE = /опазд|опозд|просып|проспал|пропуст|не пришёл|не пришел/;
const SEEDED_JOB = norm('работает в найме');

module.exports = function (output) {
    const {memory, error} = parse(output);
    if (error) {
        return {pass: false, score: 0, reason: error};
    }

    const all = entries(memory);
    const late = all.filter((e) => LATE.test(norm(e.text)));
    const lateTraits = late.filter((e) => e.field === 'traits' && e.nick === '@imdemur');
    const lateRecent = late.filter((e) => e.field === 'recent' && e.nick === '@imdemur');
    const job = [...new Set(all.filter((e) => norm(e.text) === SEEDED_JOB).map((e) => e.nick))];

    return grade([
        {
            ok: lateTraits.length > 0,
            reason: `repeated lateness never promoted to @imdemur.traits (found ${JSON.stringify(late.map((e) => `${e.nick}.${e.field}`))})`,
        },
        {
            ok: lateRecent.length === 0,
            reason: `promoted fact left behind in @imdemur.recent: ${JSON.stringify(lateRecent.map((e) => e.text))}`,
        },
        {
            ok: late.every((e) => e.nick === '@imdemur'),
            reason: `lateness leaked onto ${JSON.stringify([...new Set(late.filter((e) => e.nick !== '@imdemur').map((e) => e.nick))])}`,
        },
        {
            ok: job.length === 1 && job[0] === '@rdnull',
            reason: `carryover failed: «работает в найме» should stay on @rdnull, found on ${JSON.stringify(job)}`,
        },
    ]);
};
