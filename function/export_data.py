# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Full-resolution image export and reproducible per-image transform metadata."""
from __future__ import annotations
import csv
import json
import math
import re
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np
from PIL import Image,TiffImagePlugin
from app_paths import APP_VERSION
from alignment import check, render_result
from image_sources import is_tiff,describe,render_native,display_rgb
from tiff_display import display_pixels,color_pixels
from colors import valid_color
from output_names import plan as plan_names,claim as claim_name


def source_signatures(paths):
    return [[p.stat().st_size,p.stat().st_mtime_ns] for p in paths]


def validate_sources(paths,settings):
    expected = settings.get('source_signatures')
    if expected is not None and source_signatures(paths)!=expected:
        raise ValueError('元画像が変更されました。Go analyzeで読み込み直して、再度アラインしてください。')


def csv_filename(value):
    # Keep spreadsheet programs from interpreting filenames as formulas.
    return "'"+value if value.lstrip().startswith(('=','+','-','@')) or value.startswith(('\t','\r','\n')) else value


def render_composite(path,channels,display,matrix,canvas,cancel=None):
    """Full-resolution, display-colored additive merge with bounded row buffers."""
    values = np.zeros((canvas[1],canvas[0],3),np.uint8)
    for page in channels:
        check(cancel)
        state = display[page]
        with render_native(path,page,matrix,canvas) as image:
            pixels = np.asarray(image)
            for y in range(0,canvas[1],64):
                check(cancel)
                small = display_pixels(pixels[y:y+64],limits=state['limits'])[0]
                colored = color_pixels(small,state['color'])
                if colored.ndim==2: colored = colored[...,None]
                values[y:y+64] = np.minimum(values[y:y+64].astype(np.uint16)+colored,255).astype(np.uint8)
    return Image.fromarray(values)


def publish_files(staging,target,outputs,overwrite,cancel):
    """Replace only this export's files; restore previous files on failure."""
    backup = staging/'.previous'
    backup.mkdir()
    installed,previous = [],[]
    try:
        for file in outputs:
            check(cancel)
            output = target/file
            if output.is_symlink() or (output.exists() and not output.is_file()):
                raise ValueError(f'保存先に通常のファイル以外があります: {file}')
            if output.exists():
                if not overwrite: raise FileExistsError(f'同名の出力があります: {file}')
                output.replace(backup/file); previous.append(file)
            # Exclusive creation also protects files appearing after preflight.
            with output.open('xb') as out:
                installed.append(file)
                with (staging/file).open('rb') as source: shutil.copyfileobj(source,out)
    except Exception:
        try:
            for file in reversed(installed): (target/file).unlink(missing_ok=True)
            for file in previous: (backup/file).replace(target/file)
        except Exception:
            recovery = Path(tempfile.mkdtemp(prefix='.BRAIN-recovery-',dir=target.parent))
            shutil.copytree(backup,recovery,dirs_exist_ok=True)
            raise RuntimeError(f'保存の復元に失敗しました。元ファイルの退避先: {recovery}') from None
        raise


def validate_name(name):
    if not name or name in ('.','..') or name!=name.strip() or name.endswith('.') or re.search(r'[<>:"/\\|?*\x00-\x1f]',name):
        raise ValueError('保存名にはフォルダ区切りやWindowsで使用できない文字を含めないでください')
    reserved = {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}
    if name.split('.')[0].upper() in reserved or len(name)>100:
        raise ValueError('保存名を変更してください（予約語または長すぎる名前）')


def image_names(paths,name,extension='png'):
    return plan_names(paths,name,extension)


def pose(matrix,canvas):
    matrix = np.asarray(matrix,float)
    center = np.array([canvas[0]/2,canvas[1]/2,1.])
    shift = matrix @ center-center
    return {'dx_px':float(shift[0]),'dy_px':float(shift[1]),
            'rotation_deg_clockwise':float(math.degrees(math.atan2(matrix[1,0],matrix[0,0])))}


def make_params(paths,automatic,final,records,canvas,settings,manual_events,cancel=None):
    if len(paths)!=len(final) or len(paths)!=len(automatic) or not settings:
        raise ValueError('アライン実行後に保存してください')
    validate_sources(paths,settings)
    images = []
    for i,path in enumerate(paths):
        check(cancel)
        with Image.open(path) as source:
            width,height = source.size
            orientation = int(source.getexif().get(274,1))
            mode = source.mode
            if orientation in (5,6,7,8): width,height = height,width
        auto,result = np.asarray(automatic[i],float),np.asarray(final[i],float)
        if auto.shape!=(3,3) or result.shape!=(3,3) or not np.all(np.isfinite(auto)) or not np.all(np.isfinite(result)):
            raise ValueError(f'画像 {i+1} の変換が不正です')
        manual = result @ np.linalg.inv(auto)
        pad = np.array([[1.,0.,(canvas[0]-width)//2],[0.,1.,(canvas[1]-height)//2],[0.,0.,1.]])
        stat = path.stat()
        images.append({'index':i+1,'source_file':path.name,'source_path':str(path.resolve()),
                       'source_width':width,'source_height':height,'source_mode':mode,'exif_orientation':orientation,
                       'source_size_bytes':stat.st_size,'source_mtime_ns':stat.st_mtime_ns,
                       'automatic_matrix':auto.tolist(),'manual_matrix':manual.tolist(),'final_matrix':result.tolist(),
                       'source_to_output_matrix':(result @ pad).tolist(),
                       'automatic_pose':pose(auto,canvas),'manual_pose':pose(manual,canvas),'final_pose':pose(result,canvas),
                       'qc':records[i] if i<len(records) else None})
    return {'format':'BRAIN','version':1,'software_version':APP_VERSION,'saved_at':datetime.now(timezone(timedelta(hours=9))).isoformat(),
            'input_folder':settings['input_folder'],'alignment':dict(settings),'canvas':{'width':canvas[0],'height':canvas[1]},
            'coordinates':{'origin':'top-left','x':'right','y':'down','units':'original pixels',
                           'source_pixels':'after EXIF orientation normalization',
                           'matrix':'3x3 source-to-output, column vectors; source_to_output_matrix includes center padding',
                           'rotation_center':[canvas[0]/2,canvas[1]/2],'positive_rotation':'clockwise'},
            'manual_events':list(manual_events),'images':images}


def export_data(paths,automatic,final,records,canvas,settings,manual_events,directory,name,
                create_folder=True,include_images=True,include_table=True,cancel=None,progress=None,image_format='PNG',
                merge_mode='separate',selected_channels=None,channel_display=None,overwrite=False):
    validate_sources(paths,settings)
    validate_name(name)
    if image_format not in ('PNG','TIFF','JPEG'): raise ValueError('保存形式をPNG/TIFF/JPEGから選択してください')
    if merge_mode not in ('separate','all','selected'): raise ValueError('チャンネルの保存方法が不正です')
    merged = include_images and image_format in ('PNG','JPEG') and merge_mode!='separate'
    destination = Path(directory).expanduser().resolve(strict=True)
    if not destination.is_dir(): raise ValueError('保存先ディレクトリを選択してください')
    target = destination/name if create_folder else destination
    extension = {'PNG':'png','TIFF':'tif','JPEG':'jpg'}[image_format]
    base_names = image_names(paths,name,extension)
    plan = []
    used = set()
    merge_channels = {}
    for i,path in enumerate(paths):
        check(cancel)
        info = describe(path)
        if merged and info['pages']>1:
            channels = tuple(range(info['pages'])) if merge_mode=='all' else tuple(sorted(set(selected_channels or ())))
            if not channels or any(not isinstance(p,int) or not 0<=p<info['pages'] for p in channels):
                raise ValueError('合成するチャンネルを1つ以上選択してください')
            if channel_display is None or len(channel_display)<info['pages']:
                raise ValueError('チャンネルの表示色・表示範囲が未設定です')
            for p in channels:
                state = channel_display[p]
                if not valid_color(state['color']) or len(state['limits'])!=2 or not np.all(np.isfinite(state['limits'])) or state['limits'][0]>=state['limits'][1]:
                    raise ValueError('チャンネルの表示色・表示範囲が不正です')
            merge_channels[i] = channels
        pages = info['pages'] if image_format!='TIFF' and i not in merge_channels else 1
        for page in range(pages if include_images else 0):
            base = Path(base_names[i]).stem+(f'_ch{page+1:02d}' if pages>1 else '')
            file=claim_name(base+'.'+extension,used,f'{paths[i].resolve()}|page={page}')
            plan.append((i,page,file))
    names = [row[2] for row in plan]
    outputs = names+([f'{name}_transforms.csv'] if include_table else [])+['params.json']
    if target.is_symlink() or (create_folder and target.resolve().parent!=destination):
        raise ValueError('不正な保存先です')
    if create_folder and target.exists() and not target.is_dir(): raise ValueError('保存先と同名のファイルが存在します')
    if create_folder and target.exists() and not overwrite:
        raise FileExistsError('同名の保存フォルダが存在します。保存名を変更してください')
    if not create_folder and not overwrite and any((target/file).exists() for file in outputs):
        raise FileExistsError('保存先に同名の出力があります。別の保存先または新しいフォルダを選んでください')
    sources = {p.resolve() for p in paths}
    if any((target/file).resolve() in sources for file in outputs): raise ValueError('元画像と同じパスへは上書きできません')
    params = make_params(paths,automatic,final,records,canvas,settings,manual_events,cancel)
    params['save'] = {'directory':str(destination),'output_directory':str(target),'name':name,
                      'create_folder':bool(create_folder),'include_images':bool(include_images),
                      'include_movement_table':bool(include_table),'image_prefix':name+'_',
                      'image_format':image_format,'output_bit_depth':'see output_page_modes' if any(is_tiff(p) for p in paths) and image_format!='JPEG' else 8,
                      'output_color_mode':'native per page' if image_format=='TIFF' else 'RGB or grayscale','files':outputs,
                      'multi_page_policy':'preserve pages' if image_format=='TIFF' else 'separate channel files',
                      'jpeg_quality':95 if image_format=='JPEG' else None,
                      'non_integer_png_policy':'32-bit integer/float TIFF converted to display-scaled 8-bit'}
    params['save'].update(merge_mode=merge_mode if image_format!='TIFF' else 'preserve pages',
                          merged_channels={str(i+1):[p+1 for p in channels] for i,channels in merge_channels.items()},
                          channel_display=channel_display if merge_channels else None,
                          overwrite=bool(overwrite))
    if merge_channels:
        params['save'].update(output_bit_depth=8,output_color_mode='RGB',multi_page_policy='merged channel image')
    for i,row in enumerate(params['images']):
        row['output_images'] = [file for source,page,file in plan if source==i]
        row['output_image'] = row['output_images'][0] if row['output_images'] else None
        row['source_pages'] = describe(paths[i])['pages']
        positions=settings.get('original_image_indices',[])
        row['source_sequence_index']=positions[i] if len(positions)==len(paths) else i+1
        data_positions=settings.get('data_image_indices',[])
        row['data_sequence_index']=data_positions[i] if len(data_positions)==len(paths) else row['source_sequence_index']
        modes = describe(paths[i])['modes']
        row['source_page_modes'] = modes
        row['output_page_modes'] = modes if image_format=='TIFF' else [
            'I;16' if image_format=='PNG' and mode.startswith('I;16') else
            'RGB' if image_format=='JPEG' or mode in ('I','F') else mode for mode in modes]
        if i in merge_channels: row['output_page_modes'] = ['RGB']
    with tempfile.TemporaryDirectory(prefix='.BRAIN-export-',dir=destination) as temporary:
        staging = Path(temporary).resolve()
        if staging.parent!=destination: raise ValueError('不正な一時保存先です')
        for number,(i,page,file) in enumerate(plan):
            check(cancel)
            stat = paths[i].stat()
            row = params['images'][i]
            if (stat.st_size,stat.st_mtime_ns)!=(row['source_size_bytes'],row['source_mtime_ns']):
                raise ValueError('保存中に元画像が変更されました')
            if image_format=='TIFF':
                info = describe(paths[i])
                estimate = canvas[0]*canvas[1]*sum({'L':1,'RGB':3,'RGBA':4}.get(mode,2 if mode.startswith('I;16') else 4) for mode in info['modes'])
                if estimate>4_000_000_000: raise ValueError('1ファイルが4GBを超えます。現在の保存はBigTIFF未対応です。PNG等のチャンネル別出力を選んでください')
                tags = {}
                if is_tiff(paths[i]):
                    with Image.open(paths[i]) as source:
                        tags = {key:source.tag_v2[key] for key in (270,282,283,296) if key in source.tag_v2}
                # Explicit orientation tells ImageJ to follow each page's IFD.
                # Pillow interleaves IFDs and pixels; ImageJ's contiguous-stack
                # shortcut for an inherited images= description would misread them.
                tags[274] = 1
                with TiffImagePlugin.AppendingTiffWriter(str(staging/file),new=True) as writer:
                    for channel in range(info['pages']):
                        check(cancel)
                        with render_native(paths[i],channel,final[i],canvas) as image:
                            image.save(writer,format='TIFF',tiffinfo=tags,compression='raw')
                            writer.newFrame()
            elif i in merge_channels:
                with render_composite(paths[i],merge_channels[i],channel_display,final[i],canvas,cancel) as image:
                    image.save(staging/file,format=image_format,**({'quality':95} if image_format=='JPEG' else {'compress_level':3}))
            elif is_tiff(paths[i]):
                with render_native(paths[i],page,final[i],canvas) as image:
                    if image_format=='PNG' and image.mode not in ('I','F'):
                        image.save(staging/file,format='PNG',compress_level=3)
                    else:
                        with display_rgb(image) as converted:
                            converted.save(staging/file,format=image_format,**({'quality':95} if image_format=='JPEG' else {'compress_level':3}))
            else:
                with render_result(paths[i],final[i],canvas) as image:
                    image.save(staging/file,format=image_format,**({'quality':95} if image_format=='JPEG' else {'compress_level':3}))
            if progress: progress(number+1,len(names),file)
        check(cancel)
        if include_table:
            with (staging/f'{name}_transforms.csv').open('w',encoding='utf-8-sig',newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['index','source_file','automatic_dx_px','automatic_dy_px','automatic_rotation_deg_clockwise',
                                 'manual_dx_px','manual_dy_px','manual_rotation_deg_clockwise',
                                 'final_dx_px','final_dy_px','final_rotation_deg_clockwise'])
                for row in params['images']:
                    writer.writerow([row['index'],csv_filename(row['source_file']),
                                     *[row[part][key] for part in ('automatic_pose','manual_pose','final_pose')
                                       for key in ('dx_px','dy_px','rotation_deg_clockwise')]])
        (staging/'params.json').write_text(json.dumps(params,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        check(cancel)
        if create_folder and not target.exists():
            if target.resolve().parent!=destination: raise ValueError('不正な保存先です')
            staging.rename(target)
        else:
            if create_folder and not overwrite: raise FileExistsError('同名の保存フォルダが作成されました。再度保存してください')
            publish_files(staging,target,outputs,overwrite,cancel)
    return target
