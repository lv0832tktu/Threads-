"""Conservative observational analysis, without causal claims or approval changes."""
import hashlib
import json
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from .insights import read_snapshots, save_snapshots


def analyze(path, min_samples=5):
    rows = []
    groups = defaultdict(list)
    for snapshot in read_snapshots(path)['snapshots']:
        metrics = snapshot['metrics']
        views = metrics.get('views')
        if not isinstance(views, (int, float)) or views <= 0:
            continue
        # Missing reaction metrics must not silently become zero.
        reaction_names = ('likes', 'replies', 'reposts', 'quotes')
        complete = all(name in metrics for name in reaction_names)
        reactions = sum(metrics[name] for name in reaction_names if name in metrics)
        local = datetime.fromisoformat(snapshot['published_at']).astimezone(ZoneInfo('Asia/Tokyo'))
        text = snapshot.get('text', '')
        row = {'post_id': snapshot['post_id'], 'account': snapshot['account'], 'checkpoint': snapshot['checkpoint'], 'views': views, 'known_reactions': reactions, 'reaction_rate': reactions/views if complete else None, 'known_reaction_rate_lower_bound': reactions/views, 'complete_reactions': complete, 'hook': text.split('\n')[0][:100], 'characters': len(text), 'topic': snapshot.get('topic', ''), 'hour_jst': local.hour, 'variant': snapshot.get('variant', 'baseline'), 'late': snapshot.get('late', False)}
        row['length_bin'] = 'short' if len(text) <= 100 else ('medium' if len(text) <= 250 else 'long')
        row['hook_format'] = 'question' if '?' in row['hook'] or '？' in row['hook'] else 'statement'
        rows.append(row)
        # Never pool checkpoints or delayed observations with on-time samples.
        for dimension in ('topic', 'hour_jst', 'variant', 'length_bin', 'hook_format'):
            groups[(row['account'], snapshot['checkpoint'], row['late'], dimension, str(row[dimension]))].append(row)
    comparisons = []
    for (account, checkpoint, late, dimension, value), samples in groups.items():
        samples = list({(r['account'], r['post_id']): r for r in samples}.values())
        eligible = [r for r in samples if r['complete_reactions']]
        total_views = sum(r['views'] for r in eligible)
        comparisons.append({'account': account, 'checkpoint': checkpoint, 'late': late, 'dimension': dimension, 'value': value, 'samples': len(samples), 'complete_samples': len(eligible), 'weighted_reaction_rate': sum(r['known_reactions'] for r in eligible)/total_views if total_views else None, 'evidence': 'insufficient' if len(eligible)<min_samples else 'observational_only'})
    high_low = []
    strata = defaultdict(dict)
    for row in rows:
        if row['complete_reactions']:
            strata[(row['account'], row['checkpoint'], row['late'])][row['post_id']] = row
    for (account, checkpoint, late), unique in strata.items():
        samples = sorted(unique.values(), key=lambda r: r['reaction_rate'])
        if len(samples) >= min_samples:
            width = max(1, len(samples)//3)
            high_low.append({'account': account, 'checkpoint': checkpoint, 'late': late, 'samples': len(samples), 'lower': samples[:width], 'higher': samples[-width:], 'evidence': 'observational_only'})
    return {'posts': rows, 'comparisons': comparisons, 'high_low': high_low, 'caution': 'Small samples, timing, topic and audience differences prevent causal conclusions. Compare the same checkpoint; late measurements are separate.'}


def write_improvement(path, output, ai=None):
    report = analyze(path)
    suggestions = ['Test one change at a time: opening hook, length, topic or posting hour.', 'Keep generated revisions as unapproved drafts; compare matching checkpoints and variants.']
    if not report['high_low']:
        suggestions.insert(0, 'Evidence is insufficient: collect at least five distinct posts at the same checkpoint before selecting a winning pattern.')
    else:
        for group in report['high_low']:
            high = group['higher']
            low = group['lower']
            higher_length = sum(r['characters'] for r in high)/len(high)
            lower_length = sum(r['characters'] for r in low)/len(low)
            suggestions.append(f"At {group['checkpoint']} for account {group['account']} (late={group['late']}), higher-rate posts averaged {higher_length:.0f} characters versus {lower_length:.0f} for lower-rate posts. Test a length variation; this is an association, not proof of effectiveness.")
            suggestions.append(f"Compare opening hooks in higher-rate post IDs {[r['post_id'] for r in high]} with lower-rate IDs {[r['post_id'] for r in low]}; retain topic and time controls for the next experiment.")
    if ai is not None:
        suggestions = ai(report)
        if not isinstance(suggestions, list) or not all(isinstance(s, str) for s in suggestions):
            raise ValueError('Improvement provider must return a list of strings')
    experiment_id = 'experiment-' + hashlib.sha256(json.dumps(suggestions, ensure_ascii=False).encode()).hexdigest()[:12] if report['high_low'] else 'baseline'
    result = {'experiment_id': experiment_id, 'schema_version': 1, 'analysis': report, 'suggestions': suggestions, 'advisory_only': True}
    save_snapshots(output, result)
    return result
