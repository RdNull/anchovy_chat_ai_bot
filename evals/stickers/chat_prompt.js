// Prompt arms for the sticker describer suite. One export per arm; the config
// references each as `file://chat_prompt.js:<export>`. A new arm is one more export.

const fs = require('fs');
const path = require('path');

const PROMPTS_DIR = path.join(__dirname, '..', '..', 'src', 'prompts');
const MAX_FRAMES = 8;

function promptText(task, version = 'v1') {
    return fs.readFileSync(path.join(PROMPTS_DIR, task, `${version}.j2`), 'utf8');
}

function build(system, vars) {
    const images = [];
    for (let i = 0; i < MAX_FRAMES; i++) {
        const frame = vars[`frame_${i}`];
        if (frame) {
            images.push({ type: 'image_url', image_url: { url: frame } });
        }
    }
    return [
        { role: 'system', content: system },
        { role: 'user', content: images },
    ];
}

// Production: image_describe for a static sticker, animation_describe for an animated one.
function prodPrompts({ vars }) {
    const task = vars.kind === 'static' ? 'image_describe' : 'animation_describe';
    return build(promptText(task), vars);
}

// sticker_describe/v1 for every case.
function stickerV1({ vars }) {
    return build(promptText('sticker_describe'), vars);
}

// sticker_describe/v2 for every case.
function stickerV2({ vars }) {
    return build(promptText('sticker_describe', 'v2'), vars);
}

module.exports = { prodPrompts, stickerV1, stickerV2 };
