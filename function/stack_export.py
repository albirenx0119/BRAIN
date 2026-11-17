# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Full-resolution stack/merge exports with reproducible per-channel provenance."""
import copy
import json
import re
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
import numpy as np
from PIL import Image
from alignment import check
from colors import color_rgb
from export_data import publish_files,validate_name
from scale_bars import burn_scale_bar


def range_name(slots,all_selected=False):
    if all_selected:return 'all'
    values=sorted(set(map(int,slots)));runs=[]
    for value in values:
        if runs and value==runs[-1][-1]+1:runs[-1].append(value)
        else:runs.append([value])
    return '-'.join(str(run[0]) if len(run)==1 else f'{run[0]}to{run[-1]}' for run in runs)


def export_plan(stack,records,states,mapping,base,save_stacks,save_merge,format):
    validate_name(base)
    if not save_stacks and not save_merge:raise ValueError('保存するstack画像またはmerge画像を選択してください。')
    selected=[p for p,state in enumerate(states) if state['include']]
    if save_merge and (not selected or any(p not in records for p in selected)):
        raise ValueError('mergeに選択したすべてのチャンネルで先にstackを実行してください。')
    pages=sorted(set(records) if save_stacks else set())
    if save_merge:pages=sorted(set(pages)|set(selected))
    if not pages:raise ValueError('先にstackを実行してください。')
    aligned=bool(stack.params.get('aligned',bool(stack.params.get('analysis',{}).get('alignment'))))
    tokens={p:(f'ch{p+1}' if states[p]['color']=='Gray' else states[p]['color'].lower().lstrip('#')) for p in pages}
    counts=Counter(tokens.values())
    details={};names={};ext={'PNG':'.png','TIFF':'.tif','JPEG':'.jpg'}[format]
    for page in pages:
        record=records[page];indices=tuple(map(int,record['indices']))
        slots=[i+1 for i,value in enumerate(mapping) if value in indices]
        if not indices or any(i<0 or i>=stack.array.shape[0] for i in indices):raise ValueError('stackの保存範囲が不正です。')
        token=tokens[page] if counts[tokens[page]]==1 else f'ch{page+1}_{tokens[page]}'
        suffix=token+('_aligned' if aligned else '')+'_stacked-'+range_name(slots,set(indices)==set(range(stack.array.shape[0])))
        names[page]=suffix
        details[page]={'channel':page+1,'color':states[page]['color'],'rgb':list(color_rgb(states[page]['color'])),
            'display_limits':list(record['display_limits']),'stack_indices_zero_based':list(indices),'source_slice_numbers':slots,
            'selection_at_stack':copy.deepcopy(record.get('selection')),'projection':'maximum','aligned':aligned,
            'source_images':[copy.deepcopy(stack.params['images'][i]) for i in indices],
            'stack_filename':base+'_'+suffix+ext if save_stacks else None}
    merged='merged_'+base+'_'+'_'.join(names[p] for p in selected)+ext if save_merge else None
    outputs=[details[p]['stack_filename'] for p in pages if save_stacks]
    if merged:outputs.append(merged)
    outputs.append(base+'_stack_params.json')
    if any(len(name)>240 for name in outputs):raise ValueError('ファイル名が長すぎます。保存名を短くしてください。')
    return {'channels':details,'pages':pages,'merge_channels':selected if save_merge else [],'merge_filename':merged,
        'outputs':outputs,'format':format,'base':base,'save_stacks':save_stacks,'save_merge':save_merge}


def export_stack(stack,plan,target,scale,spacing,overwrite=False,cancel=None,progress=None):
    target=Path(target).resolve();target.mkdir(parents=True,exist_ok=True)
    h,w=stack.array.shape[2:4]
    protected={Path(record[key]).resolve() for record in stack.params['images'] for key in ('stack_image','alignment_image') if record.get(key)}
    for name in plan['outputs']:
        if (target/name).resolve() in protected:raise ValueError('元画像を上書きする名前は使用できません。')
        if (target/name).exists() and not overwrite:raise FileExistsError('同名ファイルがあります: '+name)
    if scale['visible'] and (not spacing or scale['length_um'] is None):raise ValueError('スケールバーの長さと画素サイズを設定してください。')
    total=sum(len(plan['channels'][p]['stack_indices_zero_based']) for p in plan['pages']);done=0
    with tempfile.TemporaryDirectory(prefix='.BRAIN-export-',dir=target) as work:
        staging=Path(work);maps=[]
        try:
            merged=None
            if plan['save_merge']:
                merged=np.memmap(staging/'merge.dat',dtype=np.uint8,mode='w+',shape=(h,w,3));merged[:]=0;maps.append(merged)
            for page in plan['pages']:
                check(cancel);detail=plan['channels'][page]
                indices=detail['stack_indices_zero_based']
                projection=np.memmap(staging/'projection.dat',dtype=stack.array.dtype,mode='w+',shape=(h,w)+tuple(stack.array.shape[4:]));maps.append(projection)
                for position,index in enumerate(indices):
                    check(cancel);pixels=stack.array[index,page]
                    for y in range(0,h,128):
                        check(cancel)
                        if position==0:projection[y:y+128]=pixels[y:y+128]
                        else:np.fmax(projection[y:y+128],pixels[y:y+128],out=projection[y:y+128])
                    del pixels;done+=1
                    if progress:progress(done,total,f'保存用stack Ch {page+1}: {position+1}/{len(indices)}枚')
                colored=np.memmap(staging/'colored.dat',dtype=np.uint8,mode='w+',shape=(h,w,3));maps.append(colored)
                low,high=detail['display_limits'];rgb=np.array(detail['rgb'],np.float32)
                for y in range(0,h,128):
                    check(cancel);chunk=np.asarray(projection[y:y+128],np.float32)
                    if chunk.ndim==3:
                        chunk=chunk[...,:3]
                        chunk=np.clip(np.nan_to_num((chunk-low)*255/max(high-low,1e-12),nan=0,posinf=255,neginf=0),0,255).astype(np.uint8)
                        chunk=np.rint(chunk.astype(np.float32)@np.array([.2126,.7152,.0722],np.float32))
                    else:chunk=np.clip(np.nan_to_num((chunk-low)*255/max(high-low,1e-12),nan=0,posinf=255,neginf=0),0,255)
                    # Match the stack display's 8-bit mapping and color rounding.
                    gray=chunk.astype(np.uint8)
                    colored[y:y+128]=((gray.astype(np.uint16)[...,None]*rgb.astype(np.uint16)+127)//255).astype(np.uint8)
                    if merged is not None and page in plan['merge_channels']:
                        merged[y:y+128]=np.minimum(merged[y:y+128].astype(np.uint16)+colored[y:y+128],255).astype(np.uint8)
                if plan['save_stacks']:
                    with Image.fromarray(colored) as image:
                        with burn_scale_bar(image,scale,spacing) as output:
                            output.save(staging/detail['stack_filename'],format=plan['format'],**({'quality':95} if plan['format']=='JPEG' else {}))
                projection._mmap.close();maps=[array for array in maps if array is not projection]
                colored._mmap.close();maps=[array for array in maps if array is not colored]
                (staging/'projection.dat').unlink();(staging/'colored.dat').unlink()
            if merged is not None:
                check(cancel)
                with Image.fromarray(merged) as image:
                    with burn_scale_bar(image,scale,spacing) as output:
                        output.save(staging/plan['merge_filename'],format=plan['format'],**({'quality':95} if plan['format']=='JPEG' else {}))
            metadata={'format':'BRAIN stack export 1','saved_at':datetime.now().astimezone().isoformat(),
                'image_format':plan['format'],'pixel_format':'RGB uint8; native canvas resolution; display-mapped intensities',
                'canvas_xy':[w,h],'spacing_xy_um':list(spacing) if spacing else None,'scale_bar':scale,
                'channels':[plan['channels'][p] for p in plan['pages']],
                'merge_channels':[p+1 for p in plan['merge_channels']],'merge_filename':plan['merge_filename'],
                'stack_provenance':copy.deepcopy(stack.params)}
            (staging/(plan['base']+'_stack_params.json')).write_text(json.dumps(metadata,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
            check(cancel);publish_files(staging,target,plan['outputs'],overwrite,cancel)
        finally:
            for array in maps:array._mmap.close()
    return target
