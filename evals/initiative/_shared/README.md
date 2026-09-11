# Fixture rules

**This repo is public. Fixtures built from real chat history must be anonymised before
they are committed.** Applies to every eval suite, not just this one.

Replace consistently across all fixtures so cross-window references stay coherent:

| real | fixture |
|---|---|
| participant handles | kostya, misha, den, artem, pasha, timur, gulnara, ruslan |
| first names inside message text | any name not in the cast above |
| the bot's nickname | `ChatBot(<character>)` |
| URLs with coordinates, venue names, phone numbers | a neutral placeholder of the same shape |

Keep everything else byte-identical to what `blackbox get_window` returns — timestamps,
media descriptions, OCR text, reply quotes, reaction lines. Those are what the judge reads.
