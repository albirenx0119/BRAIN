# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Validated, data-only workspace snapshots and resumable saved analyses."""
import copy
import json
import zipfile
from pathlib import Path
import numpy as np
from alignment import check
from app_paths import APP_ROOT,APP_VERSION
from colors import valid_color
from export_data import source_signatures
from image_sources import is_tiff


def channel_states(states):
    return [{'color':s['color'].get(),'limits':list(s['limits']),'include':s.get('include').get() if s.get('include') is not None else True,
             **({'mode':s['mode'].get(),'range':s['range'].get()} if 'mode' in s else {})} for s in states]


def snapshot(app):
    paths=[image.path for image in app.analysis_files]
    state={'format':'BRAIN workspace','version':1,'software_version':APP_VERSION,
           'sources':[{'name':p.name,'size':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in paths],
           'sequence':[image.path.name if image is not None else None for image in app._analysis_source_sequence],
           'ignore_excluded':app._analysis_ignore_excluded,'preview_index':app.preview_index,
           'channels':channel_states(app.tiff_view.channels),'merged':app.tiff_view.merged,
           'alignment':None,'stack':None,'volume':None}
    if app._analysis_settings and app.alignment_matrices:
        state['alignment']={'settings':copy.deepcopy(app._analysis_settings),'canvas':list(app.alignment_canvas),
            'automatic':[m.tolist() for m in app._automatic_matrices],
            'final':[m.tolist() for m in app.alignment_matrices],'records':copy.deepcopy(app.alignment_records),
            'manual_events':copy.deepcopy(app._manual_events)}
    panel=app.stack_panel
    if panel.stack is not None:
        state['stack']={'folder':panel.folder.get(),'channels':channel_states(panel.views.channels),
            'records':[{'page':page,**{k:copy.deepcopy(v) for k,v in record.items() if k!='projection'}} for page,record in panel.channel_stacks.items()],
            'source_files':[row.get('stack_image') for row in panel.stack.params.get('images',[])]}
    panel=app.volume_panel
    if panel.volume is not None:
        state['volume']={'params':{k:copy.deepcopy(v) for k,v in panel.volume.params.items() if k!='workspace'},
                         'view':panel.view_state(),'limit':int(panel.limit.get())}
    return state


def read_json(path):
    if path.stat().st_size>16*1024*1024:raise ValueError('メタデータが大きすぎます。')
    result=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(result,dict):raise ValueError('メタデータの形式が不正です。')
    return result


def volume_metadata(path):
    with zipfile.ZipFile(path) as archive:
        info=archive.getinfo('params_json.npy')
        if info.file_size>16*1024*1024:raise ValueError('3Dメタデータが大きすぎます。')
        with archive.open(info) as stream:
            value=np.lib.format.read_array(stream,allow_pickle=False)
    result=json.loads(str(value.item()))
    if not isinstance(result,dict) or result.get('format')!='BRAIN image stack':raise ValueError('BRAINの3Dデータではありません。')
    return result


def discover(folder):
    """Search the input directory and one result-directory level, without recursion."""
    folder=Path(folder);found=[]
    directories=[folder]+sorted((p for p in folder.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith('.')),key=lambda p:p.name)[:200]
    for directory in directories:
        for path in directory.iterdir():
            if path.is_symlink() or not path.is_file():continue
            if path.name=='params.json' or path.name.endswith('_stack_params.json') or path.suffix.lower()=='.npz':
                try:
                    meta=volume_metadata(path) if path.suffix.lower()=='.npz' else read_json(path)
                    if meta.get('format') in ('BRAIN','BRAIN stack export 1','BRAIN image stack'):
                        found.append((path,meta))
                except (OSError,ValueError,KeyError,TypeError,zipfile.BadZipFile):continue
    return sorted(found,key=lambda item:item[0].stat().st_mtime_ns,reverse=True)


def legacy_state(meta):
    if meta.get('workspace'):return copy.deepcopy(meta['workspace'])
    fmt=meta.get('format');volume=meta if fmt=='BRAIN image stack' else None
    provenance=meta.get('stack_provenance',{}) if fmt=='BRAIN stack export 1' else meta.get('analysis',{}) if volume else {}
    settings=meta.get('alignment') if fmt=='BRAIN' else provenance.get('alignment')
    rows=meta.get('images',[]) if fmt=='BRAIN' else meta.get('stack_provenance',{}).get('images',[]) if not volume else []
    names=[row.get('source_file',Path(row.get('alignment_image','')).name) for row in rows] if not volume else [Path(p).name for p in volume['input_files']]
    if not names:raise ValueError('元画像の対応情報がありません。')
    canvas=meta.get('canvas',{})
    canvas=[canvas.get('width'),canvas.get('height')] if fmt=='BRAIN' else volume.get('source_canvas_xy') if volume else meta['canvas_xy']
    final=[row['final_matrix'] for row in rows] if not volume else volume['source_to_common_transforms']
    alignment={'settings':settings or {'input_folder':'','method':'rigid','scope':'Reference'},'canvas':canvas,'final':final,
               'automatic':[row.get('automatic_matrix',row['final_matrix']) for row in rows] if not volume else final,
               'records':[row.get('qc') or {'status':'restored','score':None,'warning':None} for row in rows] if not volume else [],
               'manual_events':meta.get('manual_events',provenance.get('manual_events',[]))} if settings or fmt=='BRAIN' else None
    channels=meta.get('save',{}).get('channel_display') or []
    if volume:channels=[{'color':r['color'],'limits':r['display_limits']} for r in volume.get('channel_display_settings',[]) if r.get('display_limits')]
    state={'format':'BRAIN workspace','version':1,'sources':[{'name':name,**({'size':row['source_size_bytes'],'mtime_ns':row['source_mtime_ns']} if 'source_size_bytes' in row else {})} for name,row in zip(names,rows or [{} for _ in names])],
        'sequence':(settings or {}).get('source_data_sequence') or names,'ignore_excluded':(settings or {}).get('ignore_excluded_images',False),
        'alignment':alignment,'channels':channels,'stack':None,'volume':{'params':volume,'view':volume.get('view',{}),'limit':max(volume['shape_zyx'][1:])} if volume else None}
    if fmt=='BRAIN stack export 1':
        details=meta['channels'];count=max(r['channel'] for r in details)
        states=[{'color':'Gray','limits':[0,255],'include':False,'mode':'all','range':''} for _ in range(count)]
        records=[]
        for row in details:
            page=row['channel']-1;selection=row.get('selection_at_stack') or {}
            states[page]={'color':row['color'],'limits':row['display_limits'],'include':row['channel'] in meta.get('merge_channels',[]),'mode':selection.get('mode','all'),'range':selection.get('requested_range','')}
            records.append({'page':page,'indices':row['stack_indices_zero_based'],'display_limits':row['display_limits'],'selection':selection})
        source=meta['stack_provenance'].get('images',[])
        state['stack']={'folder':str(Path(source[0]['stack_image']).parent) if source else '', 'channels':states,'records':records,'source_files':[r['stack_image'] for r in source]}
    return state


def make_plan(candidates,images):
    available={image.path.name:image for image in images}
    plans=[]
    for path,meta in candidates:
        try:
            state=legacy_state(meta);sources=state['sources'];names=[row['name'] for row in sources]
            if not names or len(set(names))!=len(names) or any(name not in available for name in names):continue
            for source in sources:
                stat=available[source['name']].path.stat()
                if 'size' in source and (stat.st_size!=source['size'] or stat.st_mtime_ns!=source['mtime_ns']):raise ValueError('元画像が保存後に変更されています。')
            state['_metadata_path']=str(path)
            if meta.get('format')=='BRAIN image stack':state['_npz_path']=str(path)
            plans.append(state)
        except (KeyError,TypeError,ValueError):continue
    if not plans:raise ValueError('元画像の名前・サイズ・更新日時が一致するメタデータがありません。元画像のフォルダを選択してください。')
    state=plans[0]
    # Combine older, matching stack/3D saves only if the exact final transforms agree.
    for other in plans[1:]:
        a,b=state.get('alignment'),other.get('alignment')
        if [r['name'] for r in state['sources']]!=[r['name'] for r in other['sources']]:continue
        if bool(a)!=bool(b) or a and not np.allclose(a['final'],b['final'],rtol=0,atol=1e-8):continue
        for key in ('stack','volume'):
            if state.get(key) is None and other.get(key) is not None:
                state[key]=other[key]
                if key=='volume' and other.get('_npz_path'):state['_npz_path']=other['_npz_path']
    return state


def install_sequence(app,state):
    available={image.path.name:image for image in app.images}
    sequence=state.get('sequence') or [s['name'] for s in state['sources']]
    if len(sequence)>100000 or any(name is not None and name not in available for name in sequence):raise ValueError('保存した画像順の元画像が不足しています。')
    real=[name for name in sequence if name is not None]
    if len(real)!=len(set(real)):raise ValueError('保存した画像順が重複しています。')
    included={s['name'] for s in state['sources']}
    if not included.issubset(real):raise ValueError('画像順と解析対象が一致しません。')
    app.file_list.delete(*app.file_list.get_children());app._file_rows.clear()
    app._excluded_paths={available[name].path for name in real if name not in included}
    for number,name in enumerate(sequence,1):
        image=available[name] if name else None
        row=app.file_list.insert('','end',values=(number,name or '空白',f'{image.size/1024:,.1f} KB' if image else '','☑' if name in included else '☐'))
        app._file_rows[row]=image
    app._resume_ignored_paths=frozenset(image.path for name,image in available.items() if name not in real)
    app.count_text.set(f'画像 {len(real)} 枚（保存した元画像一覧）')
    app.images=tuple(available[name] for name in real);app.ignore_excluded.set(bool(state.get('ignore_excluded')))
    app.refresh_usage_rows();app.refresh_order_buttons()


def matrices(values,count):
    result=np.asarray(values,dtype=float)
    if result.shape!=(count,3,3) or not np.isfinite(result).all() or not np.allclose(result[:,2,:],[0,0,1]):raise ValueError('保存された変換行列が不正です。')
    for m in result:
        if not np.allclose(m[:2,:2].T@m[:2,:2],np.eye(2),atol=1e-5) or np.linalg.det(m[:2,:2])<0:raise ValueError('剛体変換以外の行列は復元できません。')
    return [m.copy() for m in result]


def prepare(state,paths,cache,slots,total,cancel,progress):
    from stack_images import AlignedTiffStack,build_stack
    from tiff_display import channel_projection
    from volume3d import build_volume,load_volume
    result={'state':state,'stack':None,'volume':None,'projections':{}}
    if [p.name for p in paths]!=[row['name'] for row in state['sources']]:raise ValueError('保存した画像順と読み込み順が一致しません。')
    alignment=state.get('alignment');final=None;canvas=tuple(max(cache.entries[i][0][a] for i in range(len(paths))) for a in (0,1))
    for group in (state.get('channels',[]),(state.get('stack') or {}).get('channels',[])):
        for record in group:
            limits=record.get('limits',[])
            if not valid_color(record.get('color')) or len(limits)!=2 or not np.isfinite(limits).all() or limits[0]>=limits[1]:raise ValueError('保存チャンネル設定が不正です。')
    if alignment:
        final=matrices(alignment['final'],len(paths));automatic=matrices(alignment['automatic'],len(paths))
        candidate=alignment['canvas']
        if len(candidate)!=2 or any(not isinstance(n,int) or n<=0 or n>100000 for n in candidate):raise ValueError('保存キャンバスのサイズが不正です。')
        canvas=tuple(candidate);alignment['final']=final;alignment['automatic']=automatic
        records=alignment.get('records',[])
        if records and len(records)!=len(paths):raise ValueError('アライン評価の枚数が一致しません。')
        alignment['records']=[row or {'score':None,'warning':None,'status':'restored'} for row in records]
        if alignment['settings'].get('method','rigid') not in ('rigid','translation') or alignment['settings'].get('scope','Reference') not in ('Reference','Global'):raise ValueError('保存したアライン設定が不正です。')
    try:
        stack=state.get('stack')
        if stack:
            original_folder=(alignment or {}).get('settings',{}).get('input_folder')
            if all(is_tiff(p) for p in paths) and original_folder and Path(stack.get('folder') or original_folder)==Path(original_folder):stack['folder']=str(paths[0].parent)
            check(cancel);progress(0,1,'保存済みstack条件を復元しています…')
            poses=final or [np.eye(3) for _ in paths]
            if all(is_tiff(p) for p in paths) and Path(stack.get('folder') or paths[0].parent).resolve()==paths[0].parent.resolve() and (not stack.get('source_files') or [Path(p).name for p in stack['source_files']]==[p.name for p in paths]):
                obj=AlignedTiffStack(paths,poses,canvas,cache,{'alignment':alignment['settings'] if alignment else None})
            else:
                folder=Path(stack.get('folder',''))
                if not folder.is_dir():raise ValueError('stack元TIFFのフォルダが見つかりません: '+str(folder))
                obj=build_stack(paths,poses,canvas,APP_ROOT,folder,{'alignment':alignment['settings'] if alignment else None},cancel,progress)
            result['stack']=obj
            for record in stack.get('records',[]):
                page=int(record['page']);indices=tuple(record['indices'])
                if not 0<=page<obj.array.shape[1] or not indices or any(not isinstance(i,int) or not 0<=i<len(paths) for i in indices):raise ValueError('stackの保存範囲が不正です。')
                low,high=record['display_limits']
                if not np.isfinite([low,high]).all() or low>=high:raise ValueError('stackの表示範囲が不正です。')
                projection=channel_projection(obj.array,page,indices,cancel,progress)
                result['projections'][page]={**record,'indices':indices,'projection':projection}
        spec=state.get('volume')
        if spec:
            check(cancel);progress(0,1,'保存済み3Dデータを復元しています…')
            if state.get('_npz_path'):result['volume']=load_volume(state['_npz_path'])
            else:
                params=spec['params'];channels=[{'index':item['channel_index']-1,'color':item['color'],'limits':item.get('display_limits')} for item in params.get('channel_display_settings',[])] or None
                spacing=(*params['input_pixel_size_xy'],params['section_spacing'])
                result['volume']=build_volume(paths,final if params['aligned'] else None,canvas,spec['limit'],spacing,params.get('color','Gray'),params.get('analysis'),cancel,progress,
                    channel=(params.get('source_channel_index') or 1)-1,ap_order=params.get('ap_order','unspecified'),channels=channels,slice_indices=slots,total_slices=total)
            volume=result['volume']
            if list(volume.params.get('included_slice_indices',[]))!=list(slots):raise ValueError('3Dと画像一覧の切片位置が一致しません。')
            if [Path(p).name for p in volume.params.get('input_files',[])]!=[p.name for p in paths]:raise ValueError('3Dと元画像の対応が一致しません。')
            view=spec.get('view',{})
            for key in ('yaw','pitch','zoom','opacity','ap_spacing_relative_to_default','scale_bar_length_um'):
                if key in view and not np.isfinite(float(view[key])):raise ValueError('3D表示条件が不正です。')
            if len(view.get('pan_pixels',[0,0]))!=2 or not np.isfinite(view.get('pan_pixels',[0,0])).all():raise ValueError('3D表示位置が不正です。')
            from volume3d import physical_distance
            for record in view.get('distance_measurements',[]):physical_distance(volume,*record['points_zyx'])
        check(cancel);return result
    except Exception:
        if result['stack'] is not None:result['stack'].close()
        raise


def apply_channels(states,saved):
    if saved and len(saved)!=len(states):raise ValueError('保存チャンネル数が画像と一致しません。')
    for state,record in zip(states,saved):
        color=record['color'];limits=record['limits']
        if not valid_color(color) or len(limits)!=2 or not np.isfinite(limits).all() or limits[0]>=limits[1]:raise ValueError('保存チャンネル設定が不正です。')
        state['color'].set(color);state['low'].set(limits[0]);state['high'].set(limits[1]);state.update(limits=tuple(limits),manual=True)
        if 'include' in state:state['include'].set(bool(record.get('include',True)))
        if 'mode' in state:state['mode'].set(record.get('mode','all'));state['range'].set(record.get('range',''))


def apply(app,payload):
    from volume_panel import AP_ORDERS
    state=payload['state'];alignment=state.get('alignment');paths=[image.path for image in app.analysis_files]
    if alignment:
        app._analysis_settings=copy.deepcopy(alignment['settings']);app._analysis_settings['input_folder']=str(app.folder)
        app._analysis_settings['source_signatures']=source_signatures(paths)
        app.alignment_matrices=alignment['final'];app._automatic_matrices=alignment['automatic'];app.alignment_canvas=tuple(alignment['canvas'])
        app.alignment_records=alignment.get('records') or [{'status':'restored','score':None,'warning':None} for _ in paths]
        app._manual_events=copy.deepcopy(alignment.get('manual_events',[]))
        app._result_method=app._analysis_settings.get('method','rigid');app.alignment_method.set(app._result_method);app._last_alignment_method=app._result_method
        app.alignment_scope.set(app._analysis_settings.get('scope','Reference'));app.alignment_color.set(app._analysis_settings.get('color') or 'Gray')
        app.alignment_channel.set(str(app._analysis_settings.get('channel_index') or 1))
        limit=app._analysis_settings.get('proxy_max_side');app.alignment_resolution.set('original' if limit is None else 'reduced')
        if limit:app.alignment_max_side.set(str(limit))
        app.alignment_status.set('保存メタデータからアライン結果を復元しました。');app.save_button.state(['!disabled']);app.refresh_manual_controls();app.refresh_resolution_controls()
    if app._tiff_pages>1:
        app.tiff_view.ensure();apply_channels(app.tiff_view.channels,state.get('channels',[]));app.tiff_view.merged=bool(state.get('merged'))
        app.merged_button.configure(style='MergedOn.TButton' if app.tiff_view.merged else 'TButton')
        if app.tiff_view.merged:app.merged_channels.pack(side='left',padx=(8,0))
    app.show_preview(min(len(paths)-1,max(0,int(state.get('preview_index',0)))))
    panel=app.stack_panel
    panel.sync_analysis(force=True)
    if payload['stack'] is not None:
        if panel.stack is not None:panel.stack.close()
        panel.stack=payload['stack'];panel._external_source=not all(is_tiff(p) for p in paths) or Path(state['stack'].get('folder') or app.folder).resolve()!=app.folder
        panel.folder.set(state['stack'].get('folder') or str(app.folder));panel.views.ensure(panel.stack.array.shape[1])
        apply_channels(panel.views.channels,state['stack'].get('channels',[]))
        for page in range(len(panel.views.channels)):panel.views.range_mode(page)
        panel.channel_stacks=payload['projections'];panel.views.merge_ready=bool(panel.channel_stacks)
        if panel.channel_stacks:panel.views.notebook.select(panel.views.frames['stack'])
        panel.set_display_slots(app._analysis_slice_indices,app._analysis_total_slices);panel.page_count.set(f'/ {panel.total_pages()}');panel.index_input.configure(to=panel.total_pages());panel.index_input.state(['!disabled']);panel.show_page(0)
        panel.status.set('保存済みstack条件を復元しました。');panel.request_draw()
    if payload['volume'] is not None:
        app.volume_panel.restore_saved(payload['volume'],state['volume'].get('view',{}))
    app.refresh_aligned_image();app.request_preview_draw();app.add_log('保存済み解析を再開: '+state.get('_metadata_path',''))
