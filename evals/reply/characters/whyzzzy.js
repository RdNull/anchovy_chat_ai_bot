const path = require('path');
const fs = require('fs');
const yaml = require('js-yaml');

module.exports = () => {
    const character = yaml.load(
        fs.readFileSync(path.resolve(__dirname, '../../../src/characters/repository/whyzzzy.yaml'), 'utf8')
    );
    return { output: character.prompt };
};