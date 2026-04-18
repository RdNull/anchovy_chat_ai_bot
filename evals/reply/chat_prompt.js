const nunjucks = require('nunjucks');
const path = require('path');

const repoRoot = path.resolve(__dirname, '../..');

const env = new nunjucks.Environment(
    new nunjucks.FileSystemLoader(repoRoot, {noCache: true})
);

module.exports = function (templatePath) {
    return async function ({vars}) {
        const systemMessage = env.render(templatePath, vars);

        const messages =
            typeof vars.messages === 'string' ? JSON.parse(vars.messages) : vars.messages || [];

        return JSON.stringify([{role: 'system', content: systemMessage}, ...messages]);
    };
};
