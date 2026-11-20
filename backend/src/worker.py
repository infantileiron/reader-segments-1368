import os,time,json,logging
from pathlib import Path
import redis
from src.config.database import Session
from src.models.entities import Segment,Review
from src.services.analysis import cluster
from src.services.review_run import run_review_job
r=redis.Redis.from_url(os.environ['REDIS_URL'])
def recover(s,key,model):
 for pending in s.query(model).filter(model.state.in_(['queued','running'])).order_by(model.id).all():
  pending.state='queued';r.rpush(key,pending.id)
 s.commit()
with Session() as recovery:
 recover(recovery,'reader:segments',Segment);recover(recovery,'reader:reviews',Review)
while True:
 Path('/tmp/heartbeat').touch()
 try:
  item=r.blpop(['reader:segments','reader:reviews'],timeout=3)
  if not item:continue
  key,raw=item[0].decode(),int(item[1])
  with Session() as s:
   if key=='reader:segments':
    row=s.get(Segment,raw)
    if not row or row.state!='queued':continue
    snapshot=row.result['snapshot'];row.state='running';s.commit()
    try:
     row.result={**cluster(snapshot,row.features,row.k),'snapshot':snapshot};row.state='done'
     Path('/artifacts/'+str(row.id)+'.json').write_text(json.dumps(row.result,ensure_ascii=False))
    except Exception as e:row.state='failed';row.error=str(e)
    s.commit()
   else:
    row=s.get(Review,raw)
    if not row or row.state not in ('queued','running'):continue
    try:run_review_job(s,row.id)
    except Exception:
     logging.exception('review %s',raw);s.rollback();row=s.get(Review,raw)
     if row and row.state in ('queued','running'):
      if (row.data or {}).get('completed_rounds',0)>0:
       # 已有轮次已提交，保持 running 让下次启动按轮号续跑，避免丢进度
       row.error='复核中断，已保留已完成轮次，重启后继续';s.commit()
      else:
       row.state='failed';row.error='复核启动失败，未完成任何轮次，可重新提交复核';s.commit()
 except Exception:logging.exception('worker');time.sleep(2)
