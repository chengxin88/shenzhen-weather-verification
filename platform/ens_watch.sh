#!/bin/bash
# After the GEFS + EC ENS 104-121E backfills: one station tick, then station counts per model -> WeCom (quiet 23-08).
wait_inactive(){ while [ "$(systemctl --user is-active "$1")" != inactive ] && [ "$(systemctl --user is-active "$1")" != failed ]; do sleep 60; done; }
wait_inactive gefs-backfill-wide.service; wait_inactive ecens-backfill-wide.service
G=$(journalctl --user -u gefs-backfill-wide --no-pager | grep FINISHED | tail -1 | sed 's/.*FINISHED/FINISHED/')
E=$(journalctl --user -u ecens-backfill-wide --no-pager | grep FINISHED | tail -1 | sed 's/.*FINISHED download/FINISHED/')
wait_inactive weather-stations.service
systemctl --user start weather-stations.service   # blocks until the tick ends
R=$(systemctl --user show weather-stations.service -p Result --value)
cd ~/verification-test/cache/stations
S=$(python3 - <<'PY'
import sqlite3,json
g=json.load(open('current.json'))['generation'];db=sqlite3.connect(f'file:snapshots/{g}/pairs.sqlite?mode=ro',uri=True)
out=[f'快照 {g}']
for var,label in (('t','气温'),('rain6','6h降水')):
    rows=db.execute("select model,count(distinct station),min(run) from pairs where var=? and valid>=datetime('now','-7 days') group by model",(var,)).fetchall()
    out.append(label+'近7天站数：'+'，'.join(f'{m} {n}' for m,n,_ in rows))
rows=db.execute("select model,min(run) from pairs where var='t' and valid>=datetime('now','-31 days') group by model").fetchall()
out.append('31天窗口最早起报：'+'，'.join(f'{m} {r[5:13]}' for m,r in rows))
print('\n'.join(out))
PY
)
MSG="站点检验：GEFS / EC集合平均 扩到104–121°E 补数据完成
GEFS ${G:-无FINISHED日志}
EC集合 ${E:-无FINISHED日志}
站点评分：${R}
${S}"
echo "$MSG"
h=$(date +%H); if [ "$h" -ge 23 ] || [ "$h" -lt 8 ]; then while [ "$(date +%H)" != 08 ]; do sleep 300; done; fi
~/wecom-claude/wecom-send --text "$MSG"
