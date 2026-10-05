"""Build the markdown report for a sticker eval run.

    python evals/stickers/build_report.py evals/stickers/fixtures/results/run-013.json

Writes the `.md` next to the `.json`. Holds no fixture data: everything comes from the results file.
Descriptions are printed for public cases only; private ones are counted, never quoted.
"""

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

STICKER_ARM_PREFIX = 'sticker'
UNCLEAR = 'неясен'
OPTION_SEPARATOR = ' или '


def metric_failed(result, metric):
    components = result['gradingResult']['componentResults']
    return any(c['assertion']['metric'] == metric and not c['pass'] for c in components)


def run_score(result):
    """The judge's `meaning` score; the run's own `score` also averages in the `blocked` check."""
    return result['namedScores'].get('meaning', 0)


def description_of(result):
    output = (result.get('response') or {}).get('output')
    if isinstance(output, dict):
        return output.get('description')
    return None


def group(results):
    """Arm (`prompt @ provider`) -> case id -> list of results, in file order."""
    arms = defaultdict(lambda: defaultdict(list))
    for result in sorted(results, key=lambda r: r['promptIdx']):
        arm = f'{result["prompt"]["label"]} @ {result["provider"]["label"]}'
        arms[arm][result['testCase']['description']].append(result)
    return arms


def case_row(case_id, runs, with_descriptions):
    passed = sum(r['success'] for r in runs)
    scores = ' '.join(f'{run_score(r):g}' for r in runs)
    blocked = sum(metric_failed(r, 'blocked') for r in runs)
    row = f'| {case_id} | {passed}/{len(runs)} | {scores} | {blocked}/{len(runs)} |'
    if with_descriptions:
        descriptions = list(dict.fromkeys(description_of(r) or '(none)' for r in runs))
        row += f' {" // ".join(descriptions)} |'
    return row


def arm_section(arm, cases):
    runs = [r for case_runs in cases.values() for r in case_runs]
    passed = sum(r['success'] for r in runs)
    perfect = sum(run_score(r) == 1 for r in runs)
    lines = [
        f'## Arm: {arm}',
        '',
        f'Totals: {passed}/{len(runs)} runs pass; {perfect} runs score 1.0.',
    ]
    for visibility in ('public', 'private'):
        ids = [
            c
            for c, rs in cases.items()
            if rs[0]['testCase']['metadata']['visibility'] == visibility
        ]
        if not ids:
            continue
        public = visibility == 'public'
        header = '| id | pass | scores | blocked |' + (' descriptions |' if public else '')
        divider = '|---|---|---|---|' + ('---|' if public else '')
        lines += ['', f'### {visibility}', '', header, divider]
        lines += [case_row(c, cases[c], public) for c in ids]
    return lines


def word_count(text):
    return len(text.split())


def ids_with(cases, predicate):
    """(number of matching outputs, case ids with a repeat count) over every run of the arm."""
    hits = [(c, r) for c, rs in cases.items() for r in rs if predicate(r)]
    counts = defaultdict(int)
    for case_id, _ in hits:
        counts[case_id] += 1
    ids = ', '.join(f'{c} ×{n}' if n > 1 else c for c, n in counts.items()) or '-'
    return len(hits), ids


def has_description(result, needle):
    text = description_of(result)
    return text is not None and needle in text.lower()


def sticker_stats(arm, cases):
    all_runs = [r for rs in cases.values() for r in rs]
    unclear, unclear_ids = ids_with(cases, lambda r: has_description(r, UNCLEAR))
    options, options_ids = ids_with(cases, lambda r: has_description(r, OPTION_SEPARATOR))
    broken, broken_ids = ids_with(cases, lambda r: metric_failed(r, 'blocked'))
    longest = max((word_count(description_of(r) or '') for r in all_runs), default=0)
    return [
        f'## {arm}: output shape',
        '',
        f'- outputs containing «{UNCLEAR}»: {unclear} ({unclear_ids})',
        f'- outputs containing «{OPTION_SEPARATOR}» in description: {options} ({options_ids})',
        f'- unparsed or blocked outputs: {broken} ({broken_ids})',
        f'- longest description: {longest} words',
    ]


def metric_failed_safe(result):
    """`blocked` failed, or there is no grading at all (the call errored)."""
    if not result.get('gradingResult'):
        return True
    return metric_failed(result, 'blocked')


def broken_kind(result):
    response = result.get('response') or {}
    finish = response.get('finishReason') or '-'
    if not response.get('output') and not isinstance(response.get('output'), dict):
        return 'error', finish
    if finish == 'length':
        return 'truncated', finish
    output = response.get('output')
    if isinstance(output, dict) and 'unparsed' in output:
        return 'unparsed', finish
    if metric_failed_safe(result):
        return 'empty or placeholder', finish
    if finish != 'stop':
        return 'finish ' + finish, finish
    return None, finish


def cost_stats(arm, cases):
    runs = [r for rs in cases.values() for r in rs]
    responses = [r.get('response') or {} for r in runs]
    usages = [x['tokenUsage'] for x in responses if x.get('tokenUsage')]
    completion = [u.get('completion', 0) for u in usages]
    reasoning = [u.get('completionDetails', {}).get('reasoning') for u in usages]
    reasoning = [x for x in reasoning if x is not None]
    latencies = [r['latencyMs'] for r in runs if r.get('latencyMs')]
    total_cost = sum(x.get('cost') or 0 for x in responses)
    lines = [
        f'## {arm}: cost and latency',
        '',
        f'- total cost (describer calls): ${total_cost:.4f}',
        f'- median latency: {statistics.median(latencies) / 1000:.1f}s'
        if latencies
        else '- median latency: -',
        f'- mean completion tokens: {statistics.fmean(completion):.0f}'
        if completion
        else '- mean completion tokens: -',
    ]
    if any(reasoning):
        lines.append(f'- mean reasoning tokens: {statistics.fmean(reasoning):.0f}')
    return lines


def broken_outputs(arm, cases):
    rows = []
    for case_id, runs in cases.items():
        for result in runs:
            kind, finish = broken_kind(result)
            if kind:
                rows.append(f'- {case_id}: {kind}, finish reason {finish}')
    return [f'## {arm}: broken outputs', '', *(rows or ['- none'])]


def build(results_path):
    data = json.loads(results_path.read_text(encoding='utf-8'))
    arms = group(data['results']['results'])
    lines = [f'# {results_path.stem}', '']
    for arm, cases in arms.items():
        lines += [*arm_section(arm, cases), '']
    for arm, cases in arms.items():
        if arm.startswith(STICKER_ARM_PREFIX):
            lines += [*sticker_stats(arm, cases), '']
        lines += [*cost_stats(arm, cases), '', *broken_outputs(arm, cases), '']
    out = results_path.with_suffix('.md')
    out.write_text('\n'.join(lines), encoding='utf-8')
    return out


if __name__ == '__main__':
    sys.stdout.write(f'{build(Path(sys.argv[1]))}\n')
