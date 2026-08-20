// Shared helpers for memory eval assertions.
//
// `norm` intentionally mirrors the normalization used by the code-side attribution
// guard (src/memory/dedup.py): lowercase, drop @nick tokens, ё → е, strip punctuation,
// collapse whitespace. Keeping the two in sync means an eval failure here predicts an
// actual drop in production, and a passing eval predicts a quiet guard.
//
// Tolerance note: production binds the model with `with_structured_output(StructuredMemory)`,
// so pydantic guarantees the shape. Promptfoo sends the bare prompt with no schema, so the
// model is free to emit `traits: {}` for an empty list, an index-keyed object instead of an
// array, or a `{text, last_seen_at}` object where a bare string is now the correct shape.
// These helpers recover what they can and report the deviation, rather than throwing — a
// shape wobble that cannot happen in production must not mask the attribution signal the
// case exists to measure.

function norm(text) {
    return String(text || '')
        .toLowerCase()
        .replace(/@[\w\d_]+/g, ' ')
        .replace(/ё/g, 'е')
        .replace(/[^\p{L}\p{N}\s]/gu, ' ')
        .replace(/\s+/g, ' ')
        .trim();
}

// `{}` for an empty list and `{"0": ..., "1": ...}` for a populated one are both common
// unschema'd model outputs. Object.values recovers the second and yields [] for the first.
function asArray(value) {
    if (Array.isArray(value)) {
        return value;
    }
    if (value && typeof value === 'object') {
        return Object.values(value);
    }
    return [];
}

// A recent entry is a bare string now that code owns the clock. An unschema'd model still
// emits the old `{text, ...}` object sometimes, so read the text out of it rather than
// letting the entry silently vanish from every assertion.
function itemText(item) {
    if (typeof item === 'string') {
        return item;
    }
    if (item && typeof item === 'object') {
        return String(item.text || '');
    }
    return '';
}

// Flatten participants into [{nick, field, text}] across traits + recent.
// traits and recent deliberately share one keyspace — a fact can be a trait on one
// person and a recent on another, and checking the lists separately misses that.
function entries(memory) {
    const out = [];
    const participants = (memory && memory.participants) || {};
    if (typeof participants !== 'object') {
        return out;
    }

    for (const [nick, info] of Object.entries(participants)) {
        if (!info || typeof info !== 'object') {
            continue;
        }
        for (const t of asArray(info.traits)) {
            out.push({nick, field: 'traits', text: itemText(t)});
        }
        for (const r of asArray(info.recent)) {
            out.push({nick, field: 'recent', text: itemText(r)});
        }
    }
    return out;
}

// Schema deviations worth reporting but not worth failing a case over, since production's
// structured-output binding makes them impossible there.
function shapeAnomalies(memory) {
    const notes = [];
    const participants = (memory && memory.participants) || {};

    if (Array.isArray(participants)) {
        notes.push('participants is an array, expected an object keyed by @nick');
    }

    for (const [nick, info] of Object.entries(participants)) {
        if (!info || typeof info !== 'object') {
            notes.push(`${nick} is ${Array.isArray(info) ? 'an array' : typeof info}, expected an object`);
            continue;
        }
        for (const field of ['traits', 'recent']) {
            if (info[field] !== undefined && !Array.isArray(info[field])) {
                notes.push(`${nick}.${field} is ${typeof info[field]}, expected an array`);
            }
        }
        for (const r of asArray(info.recent)) {
            if (r && typeof r === 'object') {
                notes.push(`${nick}.recent has an object, expected a bare string`);
                break;
            }
        }
    }

    const state = (memory && memory.state) || {};
    for (const field of ['active_topics', 'open_questions', 'running_jokes']) {
        if (state[field] !== undefined && !Array.isArray(state[field])) {
            notes.push(`state.${field} is ${typeof state[field]}, expected an array`);
        }
    }

    return notes;
}

// Map of normalized entry text -> set of nicks holding it, built from a memory object.
// Mirrors the incumbent map the code guard builds from `current_memory`.
function incumbentMap(memory) {
    const map = new Map();
    for (const {nick, text} of entries(memory)) {
        const key = norm(text);
        if (!key) {
            continue;
        }
        if (!map.has(key)) {
            map.set(key, new Set());
        }
        map.get(key).add(nick);
    }
    return map;
}

// `current_memory` arrives as the raw file contents (a string) or, for inline vars, as
// an already-parsed object. Never throws — an unreadable prior memory just means no
// incumbents, which degrades the assertion to report-only rather than failing the case.
function asMemory(value) {
    if (!value) {
        return {};
    }
    if (typeof value === 'object') {
        return value;
    }
    try {
        return JSON.parse(String(value));
    } catch (e) {
        return {};
    }
}

function parse(output) {
    try {
        return {memory: JSON.parse(output), error: null};
    } catch (e) {
        return {memory: null, error: `invalid JSON: ${e.message}`};
    }
}

// Turn a list of {ok, reason} checks into a promptfoo assertion result with partial
// credit, so the v3 vs v4 comparison shows degrees of improvement, not just pass/fail.
function grade(checks) {
    const failed = checks.filter((c) => !c.ok);
    return {
        pass: failed.length === 0,
        score: checks.length ? (checks.length - failed.length) / checks.length : 0,
        reason: failed.length ? failed.map((c) => c.reason).join(' | ') : 'all attribution checks passed',
    };
}

module.exports = {norm, asArray, itemText, entries, shapeAnomalies, incumbentMap, asMemory, parse, grade};
