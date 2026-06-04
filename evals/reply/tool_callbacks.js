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
};
