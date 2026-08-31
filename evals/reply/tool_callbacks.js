// Shared fake callbacks for the eval-time character loop provider.
// Used by both reply/setup and reply/characters via character_loop_provider.js.

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

        const fragments = /айфон|iphone/i.test(query)
            ? ['17 pro ~750к тенге', 'вышел 19 сентября', 'в казахстане с октября']
            : ['цена ~120к тенге', 'вышел 14 марта', 'выиграл Аякс 3:1'];

        return fragments.slice(0, limit);
    },
};
