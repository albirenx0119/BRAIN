# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Reference-anchored registration with local retries and explicit abstention."""
from collections import OrderedDict
import numpy as np


def run(paths,current,canvas,color,method,cancel,progress,limit,channel):
    from alignment import check,proxy,register,identity,LowCorrelation
    count=len(paths)
    matrices=[identity() for _ in paths]
    records=[None]*count
    records[current]={'score':1.,'warning':None,'parent':None,'status':'reference','attempts':[]}
    accepted={current};cache=OrderedDict()
    def plane(index):
        if index not in cache:cache[index]=proxy(paths[index],canvas,color,limit,channel)
        cache.move_to_end(index)
        while len(cache)>4:cache.popitem(last=False)
        return cache[index]
    # Alternate both directions so a damaged slice does not decide the whole run.
    order=[index for distance in range(1,count) for index in (current-distance,current+distance)
           if 0<=index<count]
    for completed,child in enumerate(order):
        check(cancel)
        moving,scale=plane(child)
        attempts=[]
        candidates=sorted(accepted,key=lambda index:(abs(child-index),abs(index-current),index))[:3]
        for attempt,parent in enumerate(candidates,1):
            check(cancel)
            if progress:progress(completed,count-1,f'画像 {child+1} → {parent+1}（照合 {attempt}/{len(candidates)}）')
            fixed,_=plane(parent)
            try:
                pair,score=register(fixed,moving,method,cancel)
            except LowCorrelation as error:
                attempts.append({'parent':parent,'score':float(error.score),'accepted':False})
                continue
            pair=np.asarray(pair,float).copy();pair[:2,2]/=scale
            matrices[child]=matrices[parent]@pair
            accepted.add(child)
            attempts.append({'parent':parent,'score':float(score),'accepted':True})
            warning=[]
            if score<.8:warning.append('相関が低いため重ね合わせを確認してください')
            if abs(child-parent)!=1:warning.append('別の位置確定済み切片との再照合で復旧しました')
            records[child]={'score':float(score),'warning':' / '.join(warning) or None,
                            'parent':parent,'status':'aligned','attempts':attempts}
            break
        if records[child] is None:
            # No accepted transform is available for this slice. Keep its own
            # original pose; never pretend that an inherited pose is a match.
            records[child]={'score':max((a['score'] for a in attempts),default=-1.),
                'warning':'自動アライン未成立：この画像の元位置を保持しています。手動補正で確認してください',
                'parent':None,'status':'unregistered','attempts':attempts}
    check(cancel)
    return matrices,records,canvas
