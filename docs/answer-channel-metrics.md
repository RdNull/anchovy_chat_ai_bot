# Answer channel metrics

Queries against the `anchovy-bot-logs` Axiom dataset for measuring which of the
three answer tools (`answer_text`, `set_reaction`, `send_sticker`) the bot
actually uses.

`attributes.request_id` is bound once per Telegram update by
`ContextBindingApplication` (`src/bot.py`) and is what groups every `TOOL_CALL`/
`REPLY_SENT`/`MESSAGE_ANSWER_DONE` row produced while answering one message.

## 1. Answer channel share over time

The headline number: text vs. reaction vs. sticker, by day.

```kusto
['anchovy-bot-logs']
| where _time > ago(7d) and ['attributes.event'] == 'REPLY_SENT'
| summarize count() by ['attributes.kind'], bin(_time, 1d)
```

Bad number: `kind=sticker` and `kind=reaction` at or near zero over weeks while
`kind=text` dominates — the situation this task exists to measure, not fix.

## 2. Initiation: context-tool calls per reply

```kusto
['anchovy-bot-logs']
| where _time > ago(7d) and ['attributes.event'] == 'TOOL_CALL'
| where ['attributes.tool'] in ('search_messages','get_user_facts','search_web','find_stickers')
| summarize context_calls = count() by ['attributes.request_id']
| summarize replies = count() by context_calls
| sort by context_calls asc
```

Replies that called **zero** context tools produce no rows here, so the zero
bucket is `(count of MESSAGE_ANSWER_DONE) - (sum of buckets above)`:

```kusto
['anchovy-bot-logs']
| where _time > ago(7d) and ['attributes.event'] == 'MESSAGE_ANSWER_DONE'
| summarize count()
```

Note: `MESSAGE_ANSWER_DONE` is only emitted by the mention/reply path
(`src/messages/response.py`). Initiative-driven replies emit
`INITIATIVE_REPLY_SENT` instead and never `MESSAGE_ANSWER_DONE`, so an
unfiltered window mixes the two paths into this arithmetic.

Bad number: `context_calls=0` dominating the distribution — the model is
answering blind rather than reaching for `find_stickers` or `get_user_facts`.

## 3. Conversion: of replies that searched for a sticker, how many sent one

```kusto
['anchovy-bot-logs']
| where _time > ago(7d) and ['attributes.event'] == 'TOOL_CALL'
| where ['attributes.tool'] in ('find_stickers','send_sticker')
| summarize tools = make_set(['attributes.tool']) by ['attributes.request_id']
| extend searched = set_has_element(tools, 'find_stickers'),
         sent     = set_has_element(tools, 'send_sticker')
| summarize searches = countif(searched), sends = countif(sent)
```

Bad number: `searches` well above zero but `sends` at or near zero — the corpus
returns candidates but the model never picks one (or `find_stickers` still
returns nothing, in which case check `STICKER_CORPUS`).

## 4. Batched tool calls

Requires Part 2 (`LLM_MULTIPLE_TOOL_CALLS`, renamed from `LLM_MULTIPLE_DIRECT_TOOLS`).

```kusto
['anchovy-bot-logs']
| where _time > ago(7d) and ['attributes.event'] == 'LLM_MULTIPLE_TOOL_CALLS'
| summarize count() by tostring(['attributes.tools'])
```

Bad number: any row where `tools` contains `find_stickers` (or another context
tool) alongside a direct tool — that batch paid for a search whose result the
model never saw, because the loop returns as soon as the first successful
direct tool lands.

## 5. Sticker repeats - is the bot reusing the same sticker?

`STICKER_RECENT_EXCLUDE` is `0` (`src/settings.py`), which disables repeat
suppression entirely (`get_recent_sticker_ids` always returns an empty set).
Raising it is plausible — RRF fusion is deterministic, so similar contexts
surface similar top candidates — but no measurement supports any particular
value, and against the current 57-entry corpus an exclusion window could just
as easily push the model onto worse candidates instead. Watch this query
before touching the setting.

**Unverified**: Axiom materializes a column on first write, and as of writing
no sticker has ever been sent in production, so `attributes.sticker_id` does
not exist as a column yet and this query currently errors. The field name is
confirmed from `Replier.reply_sticker` (`src/characters/reply.py`), which logs
`REPLY_SENT kind=sticker sticker_id=<unique_id>` at send time.

```kusto
['anchovy-bot-logs']
| where _time > ago(7d) and ['attributes.event'] == 'REPLY_SENT'
| where ['attributes.kind'] == 'sticker'
| summarize sends = count() by tostring(['attributes.sticker_id'])
| sort by sends desc
```

Bad number: any `sticker_id` with `sends` well above 1 within the window — the
bot is repeating itself instead of drawing on the corpus.
