// attribution_merge — merge invariance, starting memory pre-seeded.
//
// Every line puts @rdnull next to bicycles, and 12:35 literally calls him
// «велосипедист». The stored trait «ездит на велосипеде» must stay on @imdemur and
// must not be copied or relocated.
//
// Checks are made against the exact seeded string (normalized), NOT a loose /велосипед/
// regex: @rdnull legitimately picking up the joke or «ездит на самокате» is correct
// behaviour and must not fail the case.

const {norm, entries, parse, grade} = require('../memory_lib.js');

const SEEDED_BIKE = norm('ездит на велосипеде');
const SEEDED_JOB = norm('работает в найме');

module.exports = function (output) {
    const {memory, error} = parse(output);
    if (error) {
        return {pass: false, score: 0, reason: error};
    }

    const all = entries(memory);
    const holders = (key) => [...new Set(all.filter((e) => norm(e.text) === key).map((e) => e.nick))];

    const bike = holders(SEEDED_BIKE);
    const job = holders(SEEDED_JOB);

    return grade([
        {
            ok: bike.includes('@imdemur'),
            reason: `seeded bike trait lost from @imdemur (found on ${JSON.stringify(bike)})`,
        },
        {
            ok: bike.length <= 1,
            reason: `seeded bike trait duplicated onto ${JSON.stringify(bike.filter((n) => n !== '@imdemur'))}`,
        },
        {
            ok: bike.length === 0 || bike[0] === '@imdemur',
            reason: `seeded bike trait relocated to ${JSON.stringify(bike)}`,
        },
        {
            ok: job.length === 1 && job[0] === '@rdnull',
            reason: `carryover failed: «работает в найме» should stay on @rdnull, found on ${JSON.stringify(job)}`,
        },
    ]);
};
