// Custom promptfoo provider that mirrors the bot's production tool loop
// (src/characters/character.py::_run_llm_loop): keep calling an OpenAI-compat
// chat completions endpoint, run context-tool callbacks, feed results back,
// until `answer_text` / `set_reaction` is called or maxIterations is hit.

const path = require('path');

const DEFAULTS = {
    baseUrl: 'https://openrouter.ai/api/v1',
    apiKeyEnv: 'OPENROUTER_API_KEY',
    maxIterations: 5,
};

// Config keys consumed by the provider itself. Everything else under `config:`
// is forwarded as-is into the chat completions request body, so new OpenRouter
// params don't need a whitelist update here.
const INTERNAL_CONFIG_KEYS = new Set([
    'model',
    'baseUrl',
    'apiKeyEnv',
    'maxIterations',
    'functionToolCallbacks',
]);

function loadCallbacks(ref) {
    if (!ref) return {};
    if (typeof ref === 'object') return ref;
    if (typeof ref !== 'string') return {};
    // Drop optional `file://` prefix and `:funcName` suffix; resolve relative to
    // this file so cwd doesn't matter.
    const relPath = (ref.startsWith('file://') ? ref.slice(7) : ref).split(':')[0];
    const absPath = path.isAbsolute(relPath) ? relPath : path.resolve(__dirname, relPath);
    delete require.cache[require.resolve(absPath)];
    return require(absPath);
}

function parseMessages(prompt) {
    if (Array.isArray(prompt)) return prompt;
    if (typeof prompt !== 'string') return [];
    try {
        return JSON.parse(prompt);
    } catch (e) {
        return [{ role: 'user', content: prompt }];
    }
}

function parseArgs(raw) {
    if (raw == null) return {};
    if (typeof raw !== 'string') return raw;
    try {
        return JSON.parse(raw);
    } catch (e) {
        return {};
    }
}

function extractAnswer(toolCalls) {
    for (const tc of toolCalls) {
        const fn = tc.function || tc;
        const args = parseArgs(fn.arguments);
        if (fn.name === 'answer_text' && args.text) return args.text;
        if (fn.name === 'set_reaction' && args.emoji) return `[reaction: ${args.emoji}]`;
    }
    return null;
}

class CharacterLoopProvider {
    constructor({ id, label, config = {} } = {}) {
        if (!config.model) throw new Error('character_loop_provider: config.model is required');

        this.providerId = id || 'character_loop_provider';
        this.label = label || this.providerId;
        this.model = config.model;
        this.baseUrl = config.baseUrl || DEFAULTS.baseUrl;
        this.apiKeyEnv = config.apiKeyEnv || DEFAULTS.apiKeyEnv;
        this.maxIterations = config.maxIterations || DEFAULTS.maxIterations;
        this.callbacks = loadCallbacks(config.functionToolCallbacks);
        this.requestExtras = Object.fromEntries(
            Object.entries(config).filter(([k]) => !INTERNAL_CONFIG_KEYS.has(k)),
        );
    }

    id() {
        return this.providerId;
    }

    async callApi(prompt) {
        const apiKey = process.env[this.apiKeyEnv];
        if (!apiKey) return { error: `Missing ${this.apiKeyEnv} env var` };

        const messages = parseMessages(prompt);
        const tokenUsage = { total: 0, prompt: 0, completion: 0 };
        // Which tools actually fired, in order. Surfaced as provider metadata so a
        // `javascript` assert can score the tool choice itself, not just the reply.
        const toolsCalled = [];

        for (let i = 0; i < this.maxIterations; i++) {
            const result = await this._chat(apiKey, messages, tokenUsage);
            if (result.error) return { error: result.error };

            const toolCalls = result.message.tool_calls || [];
            for (const tc of toolCalls) toolsCalled.push((tc.function || tc).name);

            const answer = extractAnswer(toolCalls);
            if (answer !== null) return { output: answer, tokenUsage, metadata: { toolsCalled } };
            if (toolCalls.length === 0) {
                return { output: result.message.content || '', tokenUsage, metadata: { toolsCalled } };
            }

            messages.push({ role: 'assistant', content: result.message.content || null, tool_calls: toolCalls });
            for (const tc of toolCalls) {
                messages.push({ role: 'tool', tool_call_id: tc.id, content: await this._runCallback(tc) });
            }
        }

        return {
            output: '[loop exceeded maxIterations without answer tool call]',
            tokenUsage,
            metadata: { toolsCalled },
        };
    }

    async _chat(apiKey, messages, tokenUsage) {
        let response;
        try {
            response = await fetch(`${this.baseUrl}/chat/completions`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${apiKey}`,
                },
                body: JSON.stringify({ model: this.model, messages, ...this.requestExtras }),
            });
        } catch (e) {
            return { error: `Network error: ${e.message}` };
        }
        if (!response.ok) {
            const text = await response.text().catch(() => '');
            return { error: `API ${response.status}: ${text}` };
        }

        const data = await response.json();
        const usage = data.usage || {};
        tokenUsage.prompt += usage.prompt_tokens || 0;
        tokenUsage.completion += usage.completion_tokens || 0;
        tokenUsage.total += usage.total_tokens || (usage.prompt_tokens || 0) + (usage.completion_tokens || 0);

        const message = data.choices?.[0]?.message;
        if (!message) return { error: `Unexpected response: ${JSON.stringify(data).slice(0, 500)}` };
        return { message };
    }

    async _runCallback(toolCall) {
        const fn = toolCall.function || toolCall;
        const callback = this.callbacks[fn.name];
        if (typeof callback !== 'function') return '[no callback registered]';
        const args = typeof fn.arguments === 'string' ? fn.arguments : JSON.stringify(fn.arguments ?? {});
        try {
            const result = await callback(args);
            return typeof result === 'string' ? result : JSON.stringify(result);
        } catch (e) {
            return `[callback error: ${e.message}]`;
        }
    }
}

module.exports = CharacterLoopProvider;
