import json, math, os, sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.setdefault('DATABASE_URL', 'sqlite://')

from src.services import stability
from src.services.analysis import cluster


def make_rows(n=60, seed=7, small=3):
    """构造三个高斯簇；small 为刻意做小的簇大小，用于检验小群漏采。"""
    import random
    rng = random.Random(seed)
    centers = [(5, .3, .2), (15, .8, .25), (27, .6, .8)]
    sizes = [n - 2 * small, small, small]
    rows = []
    idx = 0
    for (c, sz) in zip(centers, sizes):
        for _ in range(sz):
            idx += 1
            rows.append({
                'id': idx,
                'code': f'M-{idx:03d}',
                'weekly_reading': round(max(0, rng.gauss(c[0], 1.0)), 4),
                'completion': round(max(0, min(1, rng.gauss(c[1], .04))), 4),
                'night_share': round(max(0, min(1, rng.gauss(c[2], .05))), 4),
            })
    return rows


def cluster_rows(rows, features=('weekly_reading', 'completion', 'night_share'), k=3):
    return cluster(rows, list(features), k)


def test_group_number_permutation_does_not_change_stability():
    rows = make_rows()
    result = cluster_rows(rows)
    snap = rows
    base = stability.baseline_from_result(result)
    checksum = result['checksum']
    features = result['features']
    a = stability.run_review(snap, features, 3, 4, 0.6, base, checksum)
    # 任意置换群号，例如群 0->2, 1->0, 2->1
    mapping = {0: 2, 1: 0, 2: 1}
    permuted = {code: mapping[label] for code, label in base.items()}
    b = stability.run_review(snap, features, 3, 4, 0.6, permuted, checksum)
    assert [m['stable'] for m in a['members']] == [m['stable'] for m in b['members']]
    assert [m['stability'] for m in a['members']] == [m['stability'] for m in b['members']]
    assert a['summary']['stable'] == b['summary']['stable']
    # 样本与轮次完全一致（抽样不依赖群号）
    assert [s['codes'] for s in a['samples']] == [s['codes'] for s in b['samples']]


def test_sampling_is_reproducible():
    rows = make_rows()
    result = cluster_rows(rows)
    first = [stability.sample_indices(len(rows), 3, result['checksum'], result['features'], r) for r in range(1, 6)]
    second = [stability.sample_indices(len(rows), 3, result['checksum'], result['features'], r) for r in range(1, 6)]
    assert first == second
    sizes = {len(s) for s in first}
    assert len(sizes) == 1 and sizes.pop() == math.ceil(len(rows) * 0.8)


def test_small_group_missed_sample_is_not_stable():
    rows = make_rows(small=2)
    result = cluster_rows(rows)
    base = stability.baseline_from_result(result)
    sizes = {}
    for label in base.values():
        sizes[label] = sizes.get(label, 0) + 1
    small_label = min(sizes, key=sizes.get)
    state = stability.new_state(rows, result['features'], 3, 3, 0.6, base, result['checksum'])

    # 构造“小群整轮未入样”的抽样：只保留其他群的样本
    other = [i for i, r in enumerate(rows) if base[r['code']] != small_label]
    fake = sorted(other[: int(math.ceil(len(rows) * 0.8))])
    real = stability.sample_indices
    def forced(n, k, checksum, features, round_no):
        return fake
    stability.sample_indices = forced
    try:
        for r in range(1, 4):
            stability.execute_round(state, rows, result['features'], 3, r, result['checksum'], base)
    finally:
        stability.sample_indices = real
    stability.finalize(state, result['checksum'])
    small_members = [m for m in state['members'] if m['cluster_id'] == small_label]
    assert small_members, '小群应存在于成员证据中'
    for m in small_members:
        assert m['rounds_sampled'] == 0
        assert m['stable'] is False
        assert m['sufficient_evidence'] is False
        assert '共同入样不足' in m['reason']
    assert state['summary']['insufficient_evidence'] >= len(small_members)


def test_failed_rounds_are_recorded_and_not_counted_stable():
    rows = make_rows()
    result = cluster_rows(rows)
    base = stability.baseline_from_result(result)
    state = stability.new_state(rows, result['features'], 3, 3, 0.6, base, result['checksum'])

    def broken(rows_, features_, k_):
        raise ValueError('实际群数小于k')

    record = stability.execute_round(state, rows, result['features'], 3, 1, result['checksum'], base, cluster_fn=broken)
    assert record['status'] == 'failed'
    assert '实际群数' in record['error']
    assert record['codes']
    stability.finalize(state, result['checksum'])
    assert state['summary']['completed_rounds'] == 0
    assert state['summary']['stable'] == 0
    assert all(m['stable'] is False for m in state['members'])
    assert any('重分群失败' in m['reason'] or '共同入样不足' in m['reason'] for m in state['members'])
    # 失败轮次只对当轮入样的成员计数；未入样成员以“共同入样不足”处理
    sampled = set(record['codes'])
    assert all(m['failed_rounds_sampled'] == 1 for m in state['members'] if m['code'] in sampled)
    assert all(m['failed_rounds_sampled'] == 0 for m in state['members'] if m['code'] not in sampled)


def test_round_is_idempotent_for_resume():
    rows = make_rows()
    result = cluster_rows(rows)
    base = stability.baseline_from_result(result)
    state = stability.new_state(rows, result['features'], 3, 4, 0.6, base, result['checksum'])
    stability.execute_round(state, rows, result['features'], 3, 1, result['checksum'], base)
    snapshot = json.dumps(state, sort_keys=True)
    again = stability.execute_round(state, rows, result['features'], 3, 1, result['checksum'], base)
    assert json.dumps(state, sort_keys=True) == snapshot
    assert len(state['samples']) == 1
    assert state['completed_rounds'] == 1
    assert again['round'] == 1


def test_digest_detects_tampering():
    rows = make_rows()
    result = cluster_rows(rows)
    base = stability.baseline_from_result(result)
    state = stability.run_review(rows, result['features'], 3, 3, 0.6, base, result['checksum'])
    assert stability.verify_digest(state)
    state['members'][0]['stable'] = not state['members'][0]['stable']
    assert not stability.verify_digest(state)


def test_review_uses_frozen_snapshot_not_live_readers(tmp_path):
    """复核计算函数只接受快照入参：修改“当前档案”不会影响已复算的样本。"""
    rows = make_rows()
    result = cluster_rows(rows)
    base = stability.baseline_from_result(result)
    state = stability.new_state(list(rows), result['features'], 3, 3, 0.6, base, result['checksum'])
    live = [dict(r) for r in rows]
    for r in live:
        r['weekly_reading'] = 999
    stability.execute_round(state, live, result['features'], 3, 1, result['checksum'], base)
    record = state['samples'][0]
    assert record['codes'] == [rows[i]['code'] for i in stability.sample_indices(len(rows), 3, result['checksum'], result['features'], 1)]
    # 抽取的样本来自传入的 snapshot；把 live 修改后，轮次内记录的 code 仍与快照一致
    assert set(record['codes']) <= {r['code'] for r in rows}


def test_neighborhood_jaccard_penalizes_merge_and_split():
    """被并入其他群（合并）与被拆散（分裂）都会降低邻域稳定性。"""
    rows = make_rows()
    result = cluster_rows(rows)
    base = stability.baseline_from_result(result)
    checksum = result['checksum']
    features = result['features']
    good = stability.run_review(rows, features, 3, 4, 0.6, base, checksum)
    by_code = {m['code']: m for m in good['members']}

    # 合并：把群0的少数成员错标成群1，重算时它们会回到群0，
    # 原群同伴减少、新群同伴增多，邻域 Jaccard 应明显下降。
    label0 = [c for c, v in base.items() if v == 0]
    moved = label0[:5]
    merged = {c: (1 if c in moved else v) for c, v in base.items()}
    bad = stability.run_review(rows, features, 3, 4, 0.6, merged, checksum)
    bad_by_code = {m['code']: m for m in bad['members']}
    for code in moved:
        assert bad_by_code[code]['stability'] < by_code[code]['stability']
        assert bad_by_code[code]['neighbor_union'] > bad_by_code[code]['neighbor_intersection']

    # 分裂：把群0拆成两半，重算仍会合成一群 -> 原群同伴被视为“新群同伴”，
    # 原群掩盖了拆分，但被拆的成员仍应比原结论差。
    half = label0[: len(label0) // 2]
    split = {c: (3 if c in half else v) for c, v in base.items()}
    split_state = stability.new_state(rows, features, 3, 4, 0.6, split, checksum)
    for r in range(1, 5):
        stability.execute_round(split_state, rows, features, 3, r, checksum, split)
    stability.finalize(split_state, checksum)
    split_by_code = {m['code']: m for m in split_state['members']}
    worse = [c for c in label0 if split_by_code[c]['stability'] is not None and split_by_code[c]['stability'] < by_code[c]['stability']]
    assert worse, '拆分应使至少部分原群成员稳定性下降'
