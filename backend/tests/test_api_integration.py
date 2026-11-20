"""HTTP 层集成测试：复核提交、轮询、触达草案与签核。

使用 SQLite 临时库与伪造 Redis 队列；worker 计算在同一测试进程内直接调用，
以便断言“待计算→完成”的状态推进与页面所需的证据字段。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip('fastapi')

from tests.test_stability import make_rows


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / 'reader.db'
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{db_path}')
    monkeypatch.setenv('JWT_SECRET', 'integration-secret')
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    monkeypatch.setenv('SEED_DEMO', 'false')
    import redis
    queued = []
    class FakeRedis:
        def rpush(self, key, value):
            queued.append((key, value)); return 1
        def blpop(self, *a, **k):
            return None
        def ping(self): return True
    monkeypatch.setattr(redis.Redis, 'from_url', staticmethod(lambda *a, **k: FakeRedis()))
    for name in [m for m in list(sys.modules) if m == 'src' or m.startswith('src.')]:
        del sys.modules[name]
    from fastapi.testclient import TestClient
    from src.main import app
    from src.config.database import Base, engine, Session
    from src.models.entities import User, Reader, Segment
    from src.utils.security import hash_password
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    with Session() as s:
        for role in ('admin', 'analyst', 'viewer'):
            s.add(User(email=role + '@t.local', role=role, password=hash_password('Demo123!')))
        rows = make_rows(45, small=3)
        for r in rows:
            s.add(Reader(code=r['code'], weekly_reading=r['weekly_reading'], completion=r['completion'],
                         night_share=r['night_share'], note='集成测试档案'))
        seg = Segment(name='集成版本', k=3, features=['weekly_reading', 'completion', 'night_share'],
                      state='done', result=None, error='')
        s.add(seg); s.commit(); seg_id = seg.id
    with TestClient(app) as c:
        tokens = {}
        for role in ('admin', 'analyst', 'viewer'):
            r = c.post('/auth/login', json={'email': role + '@t.local', 'password': 'Demo123!'})
            assert r.status_code == 200, r.text
            tokens[role] = r.json()['token']
        yield c, {'tokens': tokens, 'queued': queued, 'segment_id': seg_id, 'Session': Session}


def auth(client, tokens, role='analyst'):
    return {'Authorization': 'Bearer ' + tokens[role]}


def prepare_segment(client, ctx):
    """通过 API 跑完一次分群，得到可复核的成功版本。"""
    from src.services.analysis import cluster
    from src.models.entities import Segment
    sid = ctx['segment_id']
    with ctx['Session']() as s:
        from src.models.entities import Reader
        seg = s.get(Segment, sid)
        rows = [{'id': r.id, 'code': r.code, 'weekly_reading': r.weekly_reading,
                 'completion': r.completion, 'night_share': r.night_share}
                for r in s.query(Reader).order_by(Reader.id).all()]
        result = cluster(rows, seg.features, seg.k)
        seg.result = {**result, 'snapshot': rows}
        s.commit()
    return sid


def drain_reviews(ctx, rounds=None):
    """模拟 worker：处理 reader:reviews 队列中的复核任务。"""
    from src.models.entities import Review
    from src.services.review_run import run_review_job
    for key, rid in [q for q in ctx['queued'] if q[0] == 'reader:reviews']:
        with ctx['Session']() as s:
            run_review_job(s, rid)
    ctx['queued'][:] = [q for q in ctx['queued'] if q[0] != 'reader:reviews']


def test_review_lifecycle_and_outreach_approval(client):
    c, ctx = client
    sid = prepare_segment(c, ctx)
    # 待计算
    r = c.post('/reviews', json={'segment_id': sid, 'rounds': 4, 'threshold': 0.6}, headers=auth(c, ctx['tokens']))
    assert r.status_code == 200, r.text
    review = r.json()
    assert review['state'] == 'queued'
    assert review['samples'] == []
    rid = review['id']
    # 未完成不能起草
    draft_body = {'segment_id': sid, 'cluster_id': 0, 'review_id': rid,
                  'title': '低频读者轻阅读试点', 'note': '每周一次短篇精选，站内触达'}
    r = c.post('/outreaches', json=draft_body, headers=auth(c, ctx['tokens']))
    assert r.status_code == 409
    # worker 计算
    drain_reviews(ctx)
    r = c.get('/reviews/' + str(rid), headers=auth(c, ctx['tokens']))
    assert r.status_code == 200
    review = r.json()
    assert review['state'] == 'done', review.get('error')
    assert review['summary']['completed_rounds'] == 4
    assert review['members'] and all('reason' in m for m in review['members'])
    assert review['samples'][0]['codes'] and review['samples'][0]['seed']
    assert review['digest']
    # 导出复核依据
    r = c.get(f'/reviews/{rid}/export', headers=auth(c, ctx['tokens']))
    assert r.status_code == 200 and r.headers['content-disposition'].endswith(f'reader-review-{rid}.json"')
    # 起草：不稳定成员被剔除并留原因
    r = c.post('/outreaches', json=draft_body, headers=auth(c, ctx['tokens']))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out['basis']['review_id'] == rid
    assert out['basis']['review_digest'] == review['digest']
    stable = {m['code'] for m in review['members'] if m['stable']}
    assert set(out['members']) <= stable
    for e in out['basis']['excluded']:
        assert e['reason']
    # 观察员不能起草
    r = c.post('/outreaches', json=draft_body, headers=auth(c, ctx['tokens'], 'viewer'))
    assert r.status_code == 403
    # 管理员签核并冻结
    r = c.post(f"/outreaches/{out['id']}/approve", headers=auth(c, ctx['tokens'], 'admin'))
    assert r.status_code == 200, r.text
    approved = r.json()
    assert approved['status'] == 'approved'
    assert approved['basis']['approved_digest']
    # 重复签核被拒
    r = c.post(f"/outreaches/{out['id']}/approve", headers=auth(c, ctx['tokens'], 'admin'))
    assert r.status_code == 409
    # 导出含冻结依据
    r = c.get(f"/outreaches/{out['id']}/export", headers=auth(c, ctx['tokens']))
    assert r.status_code == 200
    body = r.json()
    assert body['basis_detail']['review_id'] == rid
    assert body['basis_detail']['excluded'] is not None
    # 列表接口含 basis，页面能显示剔除原因
    r = c.get('/outreaches', headers=auth(c, ctx['tokens']))
    listed = [o for o in r.json() if o['id'] == out['id']][0]
    assert listed['basis']['review_id'] == rid
    assert listed['basis']['evidence']


def test_review_requires_done_segment_and_valid_params(client):
    c, ctx = client
    r = c.post('/reviews', json={'segment_id': 999, 'rounds': 3, 'threshold': 0.6}, headers=auth(c, ctx['tokens']))
    assert r.status_code == 404
    sid = prepare_segment(c, ctx)
    r = c.post('/reviews', json={'segment_id': sid, 'rounds': 1, 'threshold': 0.6}, headers=auth(c, ctx['tokens']))
    assert r.status_code == 422
    r = c.post('/reviews', json={'segment_id': sid, 'rounds': 3, 'threshold': 1.5}, headers=auth(c, ctx['tokens']))
    assert r.status_code == 422
    r = c.post('/reviews', json={'segment_id': sid, 'rounds': 3, 'threshold': 0.6}, headers=auth(c, ctx['tokens'], 'viewer'))
    assert r.status_code == 403

