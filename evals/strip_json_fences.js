// Output transform for JSON evals.
// Production wraps these prompts in `with_structured_output`, so the model always
// returns bare JSON. Promptfoo sends the raw prompt without a schema, so the model
// is free to wrap the answer in a ```json fence — strip it before assertions run.

const CODE_FENCE = /```[a-zA-Z0-9_-]*[ \t]*\r?\n([\s\S]*?)\r?\n?[ \t]*```/;

module.exports = function (output) {
    if (typeof output !== 'string') {
        return output;
    }

    const match = output.match(CODE_FENCE);
    return match ? match[1].trim() : output;
};
