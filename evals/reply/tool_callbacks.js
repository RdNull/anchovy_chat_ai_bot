// Shared fake callbacks for the eval-time character loop provider.
// Used by both reply/setup and reply/characters via character_loop_provider.js.

// Mirrors production's STICKER_SEARCH_LIMIT (src/settings.py).
const STICKER_SEARCH_LIMIT = 10;
// Mirrors production's _MAX_QUERIES clamp (src/characters/tools/context.py).
const MAX_QUERIES = 3;

const STICKERS = [
    // intent is in the description (production: memes with text, described well)
    {sticker_id:'AgADwU8AAupbyEg', emoji:'🐸', description:'Мем с жабой в руках человека, выражающей недовольство или усталость от процесса удерживания.', text:'бля а долго держать?'},
    {sticker_id:'AgADxk8AAtBcyUh', emoji:'🐸', description:'Мем: руки держат большую жабу. Используется для передачи изображения жабы собеседнику.', text:'бля держи жабу'},
    {sticker_id:'AgADm1AAAn3TyEg', emoji:'😐', description:'Мем с Жаком Фреско, выражающим смирение или иронию словом «ладно».', text:'ладно\n— Жак Фреско —'},
    {sticker_id:'AgADp1AAAqLUyUh', emoji:'🤨', description:'Мем с тигром и собакой. Тигр в воде говорит собаке: «вот такая вот хуйня, собачка!»', text:'ВОТ ТАКАЯ ВОТ ХУЙНЯ, СОБАЧКА!'},
    {sticker_id:'AgADt1AAAv3VyEg', emoji:'🖕', description:'Мем с изображением поднятой ладони, выражающей отказ или недовольство, с грубым текстом.', text:'аухуел? ненада мне твоей жабы ипаной'},
    {sticker_id:'AgADu1AAAr7WyUh', emoji:'🤔', description:'Зеленые шлепанцы на фоне здания Белого дома в Москве. Надпись «думайте».', text:'думайте'},
    {sticker_id:'AgADv1AAAs3XyEg', emoji:'🤝', description:'Кадр из аниме, где персонаж кладет руку на плечо другому, мем «Дружеское похлопывание».', text:''},
    {sticker_id:'AgADw1AAAt7YyUh', emoji:'🤣', description:'Мем с Томом из «Тома и Джерри» с безумным взглядом.', text:''},
    // literal / flat descriptions (production: textless stickers, emotion stripped by the
    // vision prompt). Present on purpose - a suite where every candidate is perfect
    // overstates how good the real corpus is.
    {sticker_id:'AgADx1AAAu9ZyEg', emoji:'😹', description:'Кот висит на веревке, привязанной к дверному косяку.', text:''},
    {sticker_id:'AgADy1AAAvAayUh', emoji:'😌', description:'Шоколадный лабрадор с закрытыми глазами наслаждается солнечным светом.', text:''},
    {sticker_id:'AgADz1AAAwBbyEg', emoji:'🎧', description:'Человек в наушниках Logitech G. Вид сбоку.', text:'G'},
    {sticker_id:'AgAD01AAAxCcyUh', emoji:'👍', description:'Черно-белый рисунок в стиле аниме: девочка показывает большой палец вверх.', text:''},
];

// One reserved branch that always misses, so the empty-result path («пустой список →
// отвечай текстом» in the tool's own description) stays covered by a real case
// (sticker_no_match) instead of silently disappearing once the corpus above grew
// past zero. Matches the case's bureaucracy-flavoured fixture, plus an explicit
// escape hatch for a future case that wants the same branch on demand.
const NO_MATCH = /налог|документ|справк|бухгалт|квитанц|нотариус|нетуточно|no-match/i;

const MATCHERS = [
    // жаба - both frog memes
    {re: /жаб|лягуш/i, ids: ['AgADwU8AAupbyEg', 'AgADxk8AAtBcyUh']},
    // смирение / "ладно" - Жак Фреско
    {re: /ладно|похуй|смирен/i, ids: ['AgADm1AAAn3TyEg']},
    // отказ - поднятая ладонь
    {re: /отказ|^нет\b|нет,|ненад/i, ids: ['AgADt1AAAv3VyEg']},
    // домашние животные
    {re: /кот\b|котик|собак|пёс|пес\b|лабрадор/i, ids: ['AgADx1AAAu9ZyEg', 'AgADy1AAAvAayUh']},
    // усталость / недовольство - жаба + Том
    {re: /устал|недовольн/i, ids: ['AgADwU8AAupbyEg', 'AgADw1AAAt7YyUh']},
    // "думай" - шлепанцы с надписью «думайте»
    {re: /think|думай/i, ids: ['AgADu1AAAr7WyUh']},
];

// A nonsense query should still see candidates in production (the search always
// returns *something* above the score threshold, just not a good match), so the
// fallback below is 2-3 deterministic-but-arbitrary entries rather than an empty
// list or the whole corpus - either extreme would flatter the model relative to
// what search_sticker_ids actually hands it.
const FALLBACK_POOL = [
    ['AgADv1AAAs3XyEg', 'AgADz1AAAwBbyEg'],
    ['AgAD01AAAxCcyUh', 'AgADp1AAAqLUyUh', 'AgADm1AAAn3TyEg'],
    ['AgADz1AAAwBbyEg', 'AgADy1AAAvAayUh'],
];

function byId(id) {
    return STICKERS.find((s) => s.sticker_id === id);
}

function sumCharCodes(str) {
    let sum = 0;
    for (let i = 0; i < str.length; i++) sum += str.charCodeAt(i);
    return sum;
}

// Branches on the queries the model writes, the same way search_web branches on its
// query - a fixture corpus standing in for the real Qdrant search. Handles both arms
// of production's `queries: list[str] | str` union (src/characters/tools/context.py),
// since the fixture has to accept whatever shape the model actually emits.
function findStickers(argsJson) {
    const parsed = JSON.parse(argsJson);
    const raw = parsed.queries ?? parsed.query ?? [];
    const queries = (Array.isArray(raw) ? raw : [raw]).filter(Boolean).slice(0, MAX_QUERIES);
    const joined = queries.join(' ');

    if (NO_MATCH.test(joined)) return [];

    const matchedIds = [];
    for (const { re, ids } of MATCHERS) {
        if (re.test(joined)) {
            for (const id of ids) if (!matchedIds.includes(id)) matchedIds.push(id);
        }
    }

    const ids = matchedIds.length > 0
        ? matchedIds
        : FALLBACK_POOL[sumCharCodes(joined) % FALLBACK_POOL.length];

    return ids.slice(0, STICKER_SEARCH_LIMIT).map((id) => {
        const s = byId(id);
        return { sticker_id: s.sticker_id, emoji: s.emoji, description: s.description, text: s.text };
    });
}

module.exports = {
    get_user_facts: (argsJson) => {
        const { nickname } = JSON.parse(argsJson);
        const db = {
            whyzzzy: 'Бегает по утрам, программист, пьёт много кофе',
            sasha:   'Забывает зарядку, фанат Apple',
            kolya:   'Шарит в devops, ест доширак',
        };
        return db[nickname] ?? 'нет данных';
    },

    search_messages: (argsJson) => {
        const { search_query } = JSON.parse(argsJson);
        return `[25-04-29] @whyzzzy упоминал "${search_query}" — сказал что всё понятно и без этого`;
    },

    // Branches on the query the model writes, so one callback serves every
    // web_search_* case. The prose branch is deliberately shaped like something
    // worth reciting — `word-count max: 20` is what catches a paste.
    search_web: (argsJson) => {
        const { query = '', limit = 2 } = JSON.parse(argsJson);

        if (/half.?life|халф|hl3/i.test(query)) {
            return ['не нашлось'];
        }

        if (/биткоин|bitcoin|btc/i.test(query)) {
            return [
                'Биткоин — первая децентрализованная криптовалюта, запущенная в 2009 году ' +
                'человеком или группой лиц под псевдонимом Сатоси Накамото. Курс формируется ' +
                'на биржах и исторически отличается крайне высокой волатильностью: после ' +
                'пика 2021 года актив терял более двух третей стоимости, а затем неоднократно ' +
                'обновлял максимумы. Аналитики связывают текущую динамику с притоком средств ' +
                'через биржевые фонды и с ожиданиями по ставке ФРС.',
            ];
        }

        if (/доллар|тенге|курс|usd|kzt/i.test(query)) {
            return ['доллар ~525 тенге', 'за неделю +3 тенге'].slice(0, limit);
        }

        const fragments = /айфон|iphone/i.test(query)
            ? ['17 pro ~750к тенге', 'вышел 19 сентября', 'в казахстане с октября']
            : ['цена ~120к тенге', 'вышел 14 марта', 'выиграл Аякс 3:1'];

        return fragments.slice(0, limit);
    },

    find_stickers: findStickers,

    // Validates against the fixture corpus, so a hallucinated sticker_id scores as a
    // bad choice (a failure string that reads like a real ToolFailure) rather than
    // silently passing.
    send_sticker: (argsJson) => {
        const { sticker_id } = JSON.parse(argsJson);
        return STICKERS.some((s) => s.sticker_id === sticker_id) ? 'ok' : 'стикер недоступен';
    },
};
