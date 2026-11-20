"""稳定性复核的纯计算部分。

复核只使用分群版本创建时冻结的原始档案快照（segment.result.snapshot），
以确定性种子反复抽取子样本、按同一特征/k 重新聚类。成员是否稳定，只看
“共同入样的成员对”在重算后是否仍保持同群/异群关系，不比较群号本身，
因此群号任意置换不影响结论。

每次抽样轮的样本、seed、重算标签或失败原因都保存在 result.samples；
每轮的累计量在 result.stats 中，轮次按编号幂等写入，中断续跑不会重复累计。
"""
import hashlib, json, math, random
from collections import defaultdict

import numpy as np

from src.services.analysis import cluster

SAMPLE_RATIO = 0.8
DEFAULT_ROUNDS = 5
DEFAULT_THRESHOLD = 0.6
MIN_ROUNDS = 2
MAX_ROUNDS = 20


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest_of(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def sample_seed(checksum, features, k, round_no):
    payload = canonical({'checksum': checksum, 'features': list(features), 'k': int(k), 'ratio': SAMPLE_RATIO, 'round': int(round_no)})
    return int.from_bytes(hashlib.sha256(payload.encode()).digest()[:8], 'big')


def sample_indices(n, k, checksum, features, round_no):
    """同一快照、特征、k、轮号必然得到同一批样本（与群号无关）。

    只依赖 Python 标准库的 Mersenne Twister，避免 NumPy 版本差异改变抽样；
    不按群号分层，因此小群可能整轮未被纳入，由“共同入样不足”显式标记，
    而不是把它当成稳定。
    """
    size = min(n, max(int(k) + 1, int(math.ceil(n * SAMPLE_RATIO))))
    rng = random.Random(sample_seed(checksum, features, k, round_no))
    return sorted(rng.sample(range(n), size))


def min_evidence_rounds(rounds):
    """一名成员至少要有多少次“与同群成员共同入样”才算证据充足。"""
    return min(int(rounds), max(2, int(math.ceil(int(rounds) * 0.4))))


def baseline_from_result(result):
    """从成功分群结果取 code -> 群号；群号只用于分组，不参与比较语义。"""
    return {m['code']: int(m['cluster_id']) for m in result.get('members', [])}


def new_state(snapshot, features, k, rounds, threshold, baseline, checksum):
    codes = [r['code'] for r in snapshot]
    if len(codes) != len(set(codes)):
        raise ValueError('复核快照存在重复会员编号')
    if set(codes) != set(baseline):
        raise ValueError('复核快照与分群成员不一致，不能复核')
    stats = {}
    for code in codes:
        stats[code] = {
            'cluster_id': baseline[code],
            'rounds_sampled': 0,
            'rounds_co_sampled': 0,
            'failed_rounds_sampled': 0,
            'same_pairs_observed': 0,
            'same_pairs_agreed': 0,
            'other_pairs_observed': 0,
            'other_pairs_agreed': 0,
            'neighbor_intersection': 0,
            'neighbor_union': 0,
        }
    sizes = defaultdict(int)
    for label in baseline.values():
        sizes[str(label)] += 1
    return {
        'segment_checksum': checksum,
        'features': list(features),
        'k': int(k),
        'rounds': int(rounds),
        'threshold': float(threshold),
        'sample_ratio': SAMPLE_RATIO,
        'min_evidence_rounds': min_evidence_rounds(rounds),
        'samples': [],
        'stats': stats,
        'baseline_sizes': dict(sorted(sizes.items(), key=lambda x: int(x[0]))),
        'completed_rounds': 0,
        'failed_rounds': 0,
        'members': [],
        'summary': {},
        'digest': '',
    }


def existing_round(state, round_no):
    for record in state.get('samples', []):
        if int(record.get('round', -1)) == int(round_no):
            return record
    return None


def execute_round(state, snapshot, features, k, round_no, checksum, baseline, cluster_fn=cluster):
    """幂等执行一轮。轮号已存在时直接返回，绝不重复累计。"""
    prior = existing_round(state, round_no)
    if prior is not None:
        return prior
    indices = sample_indices(len(snapshot), k, checksum, features, round_no)
    codes = [snapshot[i]['code'] for i in indices]
    seed = sample_seed(checksum, features, k, round_no)
    record = {'round': int(round_no), 'seed': seed, 'size': len(indices), 'codes': codes}
    try:
        recomputed = cluster_fn([snapshot[i] for i in indices], features, k)
        assignments = {m['code']: int(m['cluster_id']) for m in recomputed['members']}
        if set(assignments) != set(codes):
            raise ValueError('重分群成员与抽样名单不一致')
    except Exception as exc:
        record.update(status='failed', error=str(exc), assignments=[])
        for code in codes:
            state['stats'][code]['failed_rounds_sampled'] += 1
        state['failed_rounds'] = int(state.get('failed_rounds', 0)) + 1
        state.setdefault('samples', []).append(record)
        return record

    sample_base_groups = defaultdict(list)
    new_groups = defaultdict(list)
    for code in codes:
        sample_base_groups[baseline[code]].append(code)
        new_groups[assignments[code]].append(code)
    # (重算群, 原群) 交集人数，用于 O(1) 计算成员的同群一致对
    common = defaultdict(int)
    for code in codes:
        common[(assignments[code], baseline[code])] += 1

    for code in codes:
        st = state['stats'][code]
        base_label = baseline[code]
        new_label = assignments[code]
        base_peers = sample_base_groups[base_label]
        new_peers = new_groups[new_label]
        inter = common[(new_label, base_label)]
        same_observed = len(base_peers) - 1
        same_agreed = inter - 1
        other_observed = len(codes) - len(base_peers)
        other_agreed = len(new_peers) - inter
        st['rounds_sampled'] += 1
        if same_observed > 0:
            # 只有与至少一位同群成员共同入样，才产生可比较的同群关系证据
            st['rounds_co_sampled'] += 1
        st['same_pairs_observed'] += same_observed
        st['same_pairs_agreed'] += same_agreed
        st['other_pairs_observed'] += other_observed
        st['other_pairs_agreed'] += other_agreed
        # 成员邻域的 Jaccard：与原群同伴集合、重算后同群集合的交/并，
        # 对“被拆开”和“被并入别的群”同等扣分，且与群号命名无关。
        st['neighbor_intersection'] += inter - 1
        st['neighbor_union'] += (len(base_peers) - 1) + (len(new_peers) - 1) - (inter - 1)

    record.update(status='ok', error='', assignments=[{'code': c, 'cluster_id': assignments[c]} for c in sorted(codes)],
                  cluster_sizes=sorted(len(v) for v in new_groups.values()))
    state.setdefault('samples', []).append(record)
    state['completed_rounds'] = int(state.get('completed_rounds', 0)) + 1
    return record


def finalize(state, checksum):
    rounds = int(state['rounds'])
    threshold = float(state['threshold'])
    need = int(state['min_evidence_rounds'])
    members = []
    for code in sorted(state['stats']):
        st = state['stats'][code]
        observed = int(st['same_pairs_observed'])
        agreed = int(st['same_pairs_agreed'])
        inter = int(st['neighbor_intersection'])
        union = int(st['neighbor_union'])
        # 邻域 Jaccard：共同入样的同伴关系在重算后保留的比例。
        # 以“同群对”为分母会漏掉被拆到其他群的情形，因此用交/并。
        stability = round(inter / union, 6) if union > 0 else None
        sufficient = int(st['rounds_co_sampled']) >= need and union > 0
        stable = bool(sufficient and stability is not None and stability >= threshold)
        if not sufficient:
            if int(st['rounds_sampled']) == 0:
                reason = '未被任何成功抽样轮纳入，共同入样不足'
            else:
                reason = f'共同入样不足：{int(st["rounds_co_sampled"])} 次与同群成员共同入样，至少需要 {need} 次'
            if int(st['failed_rounds_sampled']):
                reason += f'；另有 {int(st["failed_rounds_sampled"])} 次抽样重分群失败，失败轮次不计为稳定'
        elif not stable:
            reason = f'邻域稳定性 {stability:.2f} 低于门槛 {threshold:.2f}（同群关系一致 {agreed}/{observed} 对，邻域交集/并集 {inter}/{union}）'
        else:
            reason = ''
        members.append({
            'code': code,
            'cluster_id': int(st['cluster_id']),
            'rounds_sampled': int(st['rounds_sampled']),
            'rounds_co_sampled': int(st['rounds_co_sampled']),
            'failed_rounds_sampled': int(st['failed_rounds_sampled']),
            'same_pairs_observed': observed,
            'same_pairs_agreed': agreed,
            'other_pairs_observed': int(st['other_pairs_observed']),
            'other_pairs_agreed': int(st['other_pairs_agreed']),
            'neighbor_intersection': inter,
            'neighbor_union': union,
            'stability': stability,
            'sufficient_evidence': sufficient,
            'stable': stable,
            'reason': reason,
        })
    state['members'] = members
    state['summary'] = {
        'total': len(members),
        'stable': sum(1 for m in members if m['stable']),
        'excluded': sum(1 for m in members if not m['stable']),
        'insufficient_evidence': sum(1 for m in members if not m['sufficient_evidence']),
        'below_threshold': sum(1 for m in members if m['sufficient_evidence'] and not m['stable']),
        'completed_rounds': int(state.get('completed_rounds', 0)),
        'failed_rounds': int(state.get('failed_rounds', 0)),
        'rounds': rounds,
        'threshold': threshold,
        'sample_ratio': SAMPLE_RATIO,
        'min_evidence_rounds': need,
    }
    state['digest'] = review_digest(state, checksum)
    return state


def review_digest(state, checksum):
    payload = {k: v for k, v in state.items() if k != 'digest'}
    payload['segment_checksum'] = checksum
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def state_digest(state):
    """复核内容摘要（不包含 digest 字段自身）。"""
    return review_digest(state, state.get('segment_checksum', ''))


def verify_digest(state):
    if not state or not state.get('digest') or not state.get('segment_checksum'):
        return False
    return state_digest(state) == state['digest']


def run_review(snapshot, features, k, rounds, threshold, baseline, checksum, cluster_fn=cluster):
    """测试与离线复算使用的便捷入口，行为与 worker 逐轮写入一致。"""
    state = new_state(snapshot, features, k, rounds, threshold, baseline, checksum)
    for round_no in range(1, int(rounds) + 1):
        execute_round(state, snapshot, features, k, round_no, checksum, baseline, cluster_fn)
    return finalize(state, checksum)
