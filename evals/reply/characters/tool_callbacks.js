module.exports = {
    get_user_facts: (argsJson) => {
        const { username } = JSON.parse(argsJson);
        const db = {
            whyzzzy: 'Бегает по утрам, программист, пьёт много кофе',
            sasha:   'Забывает зарядку, фанат Apple',
            kolya:   'Шарит в devops, ест доширак',
        };
        return db[username] ?? 'нет данных';
    },

    search_messages: (argsJson) => {
        const { query } = JSON.parse(argsJson);
        return `[25-04-29] @whyzzzy упоминал "${query}" — сказал что всё понятно и без этого`;
    },
};