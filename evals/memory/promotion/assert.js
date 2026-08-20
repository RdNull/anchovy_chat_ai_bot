// promotion — the one recurrence signal the whole decay design leans on.
//
// `recent → traits` is a structural move, so `src/memory/decay.py:reconcile` can detect it
// by diffing two snapshots. Nothing else in the system can tell recurrence from re-emission:
// the prompt instructs the model to carry everything forward, so "present in both snapshots"
// only means still stored. If the model stops promoting, decay has no recurrence input left
// and `cycles` degrades to time-since-birth.
//
// Seeded memory holds «опоздал на созвон» in @imdemur's `recent`. The window repeats the
// lateness across two separate calls. What is asserted is what production can act on: a
// lateness trait exists on @imdemur, and the *seeded key* has left `recent`.
//
// Deliberately NOT asserted: that no lateness remains in `recent` at all. New incidents in
// the window are legitimate new `recent` entries — only the promoted one must go.
//
// Note what this case cannot check. The model always generalises when promoting
// («опоздал на созвон» → «часто опаздывает»), so the trait is a different normalized key
// and `reconcile` sees a vanish plus a birth rather than a `promote`. That is why the churn
// log counts `promote_candidate` alongside the exact signal.

const {norm, stem, entries, parse, grade} = require('../memory_lib.js');

// Word-start anchored, so a lateness root cannot match inside an unrelated word. The
// «не пришёл» spelling is gone: `norm` folds ё → е, so it could never have matched.
const LATE = stem('опазд', 'опозд', 'просып', 'проспал', 'пропуст', 'не пришел');
const SEEDED_LATE = norm('опоздал на созвон');
const SEEDED_JOB = norm('работает в найме');

module.exports = function (output) {
    const {memory, error} = parse(output);
    if (error) {
        return {pass: false, score: 0, reason: error};
    }

    const all = entries(memory);
    const late = all.filter((e) => LATE.test(norm(e.text)));
    const lateTraits = late.filter((e) => e.field === 'traits' && e.nick === '@imdemur');
    const seededRecent = all.filter(
        (e) => e.field === 'recent' && e.nick === '@imdemur' && norm(e.text) === SEEDED_LATE
    );
    const job = [...new Set(all.filter((e) => norm(e.text) === SEEDED_JOB).map((e) => e.nick))];

    return grade([
        {
            ok: lateTraits.length > 0,
            reason: `repeated lateness never promoted to @imdemur.traits (found ${JSON.stringify(late.map((e) => `${e.nick}.${e.field}`))})`,
        },
        {
            ok: seededRecent.length === 0,
            reason: 'the promoted entry «опоздал на созвон» is still in @imdemur.recent',
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
