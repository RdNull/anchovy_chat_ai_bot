// Reads the shipped persona so the eval cannot drift from production.
// promptfoo var files must return { output: <value> }, not a bare value.
// `js-yaml` is already a dependency in evals/package.json.
const fs = require('fs');
const path = require('path');
const yaml = require('js-yaml');

module.exports = function () {
  const file = path.join(__dirname, '../../../src/characters/repository/whyzzzy.yaml');
  return { output: yaml.load(fs.readFileSync(file, 'utf8')).prompt };
};
