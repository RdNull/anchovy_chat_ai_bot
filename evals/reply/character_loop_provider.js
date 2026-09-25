// Custom promptfoo provider that mirrors the bot's production tool loop
// (src/characters/character.py::_run_llm_loop): keep calling an OpenAI-compat
// chat completions endpoint, run context-tool callbacks, feed results back,
// until a direct tool is called or maxIterations is hit.
//
// Knowingly out of sync with production: the loop there can recover from a direct
// tool that fails (ToolFailure -> the model gets another turn, bounded by
// _MAX_LOOP_DEPTH). This suite grades prompt and tool choice, not loop mechanics,
// so a direct tool here always succeeds. Do not "fix" the divergence.

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
        // Bracket-free on purpose: the `not-regex` defaultTest assert exists to catch the
        // model leaking the *input* message format ([2026-09-15 17:36] nick: …, [sticker: …])
        // into its reply, so that guard stays global rather than scoped per-case. That means
        // our own gesture answers can't use brackets either, or they fail the same assert a
        // real leak would — a `REACTION:`/`STICKER:` sentinel says the same thing without
        // colliding. `file_unique_id` is Telegram base64url (`A-Za-z0-9_-`), disjoint from
        // the not-regex character class, so STICKER:<id> can't reintroduce the leak this
        // assert guards against.
        if (fn.name === 'set_reaction' && args.emoji) return `REACTION:${args.emoji}`;
        if (fn.name === 'send_sticker' && args.sticker_id) return `STICKER:${args.sticker_id}`;
    }
    return null;
}

// OpenRouter normalises upstream thinking into one of two shapes, and which one arrives
// depends on the provider behind the model: a flat `reasoning` string, or the newer
// `reasoning_details[]` (`reasoning.text` / `reasoning.summary` / `reasoning.encrypted`).
// Prefer whichever actually carries text. When neither does, say *why* instead of returning
// an empty string - "the model returned no reasoning field" and "the model returned one we
// are not allowed to read" are different findings and only one of them needs a fallback probe.
// Read-only by design: nothing here is ever pushed back into `messages`, because production
// doesn't send reasoning back either and changing what the model sees changes the experiment.
function extractReasoning(message) {
    const details = Array.isArray(message.reasoning_details) ? message.reasoning_details : [];
    const readable = details
        .map((d) => (typeof d.text === 'string' && d.text) || (typeof d.summary === 'string' && d.summary) || '')
        .filter(Boolean)
        .join('\n');
    if (readable) return readable;
    if (typeof message.reasoning === 'string' && message.reasoning) return message.reasoning;
    if (details.length) {
        return `[unreadable reasoning_details: ${details.map((d) => d.type || 'unknown').join(', ')}]`;
    }
    return '';
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
        // One entry per loop turn: what the model was thinking, and which tools that turn
        // produced. `toolsCalled` is flat across turns - a single turn can batch several
        // calls - so it cannot be indexed against a per-turn array; the pairing lives inside
        // each entry instead. Diagnostics only, no assert reads this.
        const reasoningTurns = [];

        for (let i = 0; i < this.maxIterations; i++) {
            const result = await this._chat(apiKey, messages, tokenUsage);
            // Turns already recorded stay recorded: a failure on turn 3 still leaves turns
            // 1-2 worth reading, and promptfoo keeps `metadata` on a row it marks as errored.
            if (result.error) return { error: result.error, metadata: { toolsCalled, reasoningTurns } };

            const toolCalls = result.message.tool_calls || [];
            const turnTools = toolCalls.map((tc) => (tc.function || tc).name);
            toolsCalled.push(...turnTools);
            // `max_tokens` is shared with the thinking budget here, so a turn that stops on
            // `length` - or spends most of its completion tokens before emitting a tool call -
            // is a mechanical explanation for the tool choice rather than a preference one.
            reasoningTurns.push({
                tools: turnTools,
                finishReason: result.finishReason,
                reasoningTokens: result.reasoningTokens,
                reasoning: extractReasoning(result.message),
                content: result.message.content || '',
                args: toolCalls.map((tc) => (tc.function || tc).arguments),
            });

            const answer = extractAnswer(toolCalls);
            if (answer !== null) return { output: answer, tokenUsage, metadata: { toolsCalled, reasoningTurns } };
            if (toolCalls.length === 0) {
                return {
                    output: result.message.content || '',
                    tokenUsage,
                    metadata: { toolsCalled, reasoningTurns },
                };
            }

            messages.push({ role: 'assistant', content: result.message.content || null, tool_calls: toolCalls });
            for (const tc of toolCalls) {
                messages.push({ role: 'tool', tool_call_id: tc.id, content: await this._runCallback(tc) });
            }
        }

        return {
            output: '[loop exceeded maxIterations without answer tool call]',
            tokenUsage,
            metadata: { toolsCalled, reasoningTurns },
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

        const choice = data.choices?.[0];
        const message = choice?.message;
        if (!message) return { error: `Unexpected response: ${JSON.stringify(data).slice(0, 500)}` };
        return {
            message,
            finishReason: choice.finish_reason,
            reasoningTokens: usage.completion_tokens_details?.reasoning_tokens ?? null,
        };
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
