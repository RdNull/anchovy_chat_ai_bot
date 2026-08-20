// attribution_subject — intake rules, empty starting memory.
//
// Two facts must be saved, two must be dropped:
//   19:05  reply + «ты ... ездишь»        → bike fact belongs to @imdemur (reply target)
//   19:09  «@vasabi опять пропал»         → belongs to @vasabi (speaker is not the subject)
//   19:08  «он ... на нём» , no nick      → DROP (unresolvable pronoun; also redundant,
//                                            so dropping it costs nothing)
//   19:11  «они вдвоём подписку на зал»   → DROP (subject is more than one person)

const {norm, entries, parse, grade} = require('../memory_lib.js');

const BIKE = /велосипед|велик/;
const GONE = /пропал|не пиш|третий день/;
const GYM = /подписк|зал/;

module.exports = function (output) {
    const {memory, error} = parse(output);
    if (error) {
        return {pass: false, score: 0, reason: error};
    }

    const all = entries(memory);
    const holders = (re) => [...new Set(all.filter((e) => re.test(norm(e.text))).map((e) => e.nick))];

    const bike = holders(BIKE);
    const gone = holders(GONE);
    const gym = holders(GYM);

    return grade([
        {
            ok: bike.includes('@imdemur'),
            reason: `bike fact missing from @imdemur (found on ${JSON.stringify(bike)})`,
        },
        {
            ok: bike.every((n) => n === '@imdemur'),
            reason: `bike fact leaked onto ${JSON.stringify(bike.filter((n) => n !== '@imdemur'))}`,
        },
        {
            ok: gone.length === 1 && gone[0] === '@vasabi',
            reason: `disappearance fact should sit only on @vasabi, found on ${JSON.stringify(gone)}`,
        },
        {
            ok: gym.length === 0,
            reason: `multi-subject gym fact should have been dropped, found on ${JSON.stringify(gym)}`,
        },
    ]);
};
