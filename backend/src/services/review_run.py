"""稳定性复核任务的执行与断点续跑。

每轮样本与累计量写入 reviews.data，并逐轮提交；恢复时按轮号跳过已完成
轮次，因此中断后重跑不会重复累计。复核结论只发布一次（done），之后不再
改写；再次复核会新建一条 reviews 记录，旧结论保持不变。
"""
import copy
from datetime import datetime, timezone

from sqlalchemy.orm.attributes import flag_modified

from src.models.entities import Review
from src.services.analysis import cluster
from src.services.stability import execute_round, existing_round, finalize


class ReviewInterrupted(Exception):
    """仅用于测试/模拟中断，不代表业务失败。"""


def now():
    return datetime.now(timezone.utc).isoformat()


def _baseline(state):
    return {code: int(st['cluster_id']) for code, st in state['stats'].items()}


def run_review_job(session, review_id, cluster_fn=cluster, max_new_rounds=None, after_round=None):
    review = session.get(Review, review_id)
    if review is None:
        return None
    if review.state not in ('pending', 'queued', 'running'):
        return review
    review.state = 'running'
    review.updated = now()
    session.commit()

    if not review.data or not review.data.get('stats'):
        raise ValueError('复核缺少冻结快照，无法续跑')
    state = copy.deepcopy(review.data)
    baseline = _baseline(state)
    executed = 0
    for round_no in range(1, int(review.rounds) + 1):
        if existing_round(state, round_no) is not None:
            continue
        if max_new_rounds is not None and executed >= max_new_rounds:
            raise ReviewInterrupted(f'已完成 {executed} 轮，模拟中断')
        execute_round(state, review.snapshot, review.features, int(review.k), round_no,
                      review.checksum, baseline, cluster_fn)
        executed += 1
        review.data = state
        flag_modified(review, 'data')
        session.commit()
        if after_round is not None:
            after_round(round_no, state)

    if int(state.get('completed_rounds', 0)) == 0:
        errors = sorted({record.get('error', '') for record in state.get('samples', []) if record.get('status') == 'failed'})
        review.state = 'failed'
        review.error = '所有抽样轮重分群失败：' + ('；'.join(e for e in errors if e) or '未知原因')
    else:
        state['finished_at'] = now()
        finalize(state, review.checksum)
        review.state = 'done'
        review.error = ''
    review.data = state
    flag_modified(review, 'data')
    review.updated = now()
    session.commit()
    return review
