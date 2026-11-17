# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Aligned image planes stacked along the anatomical AP axis."""
from __future__ import annotations
import json
import math
import tempfile
from pathlib import Path
import numpy as np
from PIL import Image
from alignment import check, identity, read_rgb
from image_sources import read_page, display_array
from tiff_display import color_pixels
from colors import valid_color,color_weights
from view_cube import camera_rotation


class Volume:
    def __init__(self,data,valid,spacing_xyz,params,rgb=None,channel_data=None):
        self.data = data
        self.valid = valid
        self.spacing_xyz = tuple(spacing_xyz)
        self.params = params
        self.rgb = rgb
        self.channel_data = channel_data


def build_volume(paths,matrices=None,canvas=None,limit=256,spacing=(1.,1.,1.),color='Gray',
                 provenance=None,cancel=None,progress=None,channel=None,ap_order='unspecified',channels=None,
                 slice_indices=None,total_slices=None):
    if len(paths)<2:
        raise ValueError('3D構築には2枚以上の画像が必要です')
    slots=tuple(range(1,len(paths)+1)) if slice_indices is None else tuple(slice_indices)
    if len(slots)!=len(paths) or any(not isinstance(i,int) or i<1 for i in slots) or tuple(sorted(set(slots)))!=slots:
        raise ValueError('元の切片番号は重複のない昇順で指定してください')
    depth=max(slots) if total_slices is None else total_slices
    if not isinstance(depth,int) or depth<max(slots):raise ValueError('全切片数が不正です')
    if not valid_color(color) or not 16<=limit<=1024:
        raise ValueError('色または計算サイズが不正です')
    if len(spacing)!=3 or any(not math.isfinite(v) or v<=0 for v in spacing):
        raise ValueError('画素サイズと切片間隔には0より大きい値を入力してください')
    if ap_order not in ('unspecified','A_to_P','P_to_A'): raise ValueError('AP方向が不正です')
    if channels is not None:
        channels = [dict(item) for item in channels]
        if not channels: raise ValueError('合成するチャンネルを選択してください')
        seen = set()
        for item in channels:
            page = item.get('index')
            if not isinstance(page,int) or page<0 or page in seen or not valid_color(item.get('color')):
                raise ValueError('合成チャンネルの設定が不正です')
            limits = item.get('limits')
            if limits is not None and (len(limits)!=2 or not all(math.isfinite(v) for v in limits) or limits[0]>=limits[1]):
                raise ValueError('チャンネルの表示範囲が不正です')
            seen.add(page)
    sizes = []
    colored = []
    for path in paths:
        check(cancel)
        with Image.open(path) as image:
            if channels is not None:
                if image.format!='TIFF': raise ValueError('全チャンネル合成にはTIFF画像が必要です')
                size = None
                for item in channels:
                    image.seek(item['index'])
                    if size is not None and image.size!=size: raise ValueError('チャンネル間で画像サイズが異なります')
                    size = image.size
            image.seek(channels[0]['index'] if channels is not None else channel or 0)
            colored.append(image.mode in ('RGB','RGBA','P','CMYK'))
            w,h = image.size
            if image.getexif().get(274,1) in (5,6,7,8): w,h = h,w
            sizes.append((w,h))
    if canvas is None:
        canvas = (max(s[0] for s in sizes),max(s[1] for s in sizes))
    if len(canvas)!=2 or min(canvas)<=0:
        raise ValueError('キャンバスサイズが不正です')
    if matrices is not None and (len(matrices)!=len(paths) or any(m is None for m in matrices)):
        raise ValueError('全画像のアライン結果が必要です')
    scale = min(1.,float(limit)/max(canvas))
    width,height = max(1,round(canvas[0]*scale)),max(1,round(canvas[1]*scale))
    if depth*width*height>64_000_000:
        raise ValueError('ボリュームが大きすぎます。計算サイズを小さくしてください（上限6400万ボクセル）')
    data = np.zeros((depth,height,width),np.uint8)
    valid = np.zeros_like(data,dtype=bool)
    textures = np.zeros((*data.shape,3),np.uint8)
    if channels is not None and len(channels)*data.size>192_000_000:
        raise ValueError('チャンネルのデータが大きすぎます。計算用画像の最大辺を小さくしてください。')
    channel_data = np.zeros((len(channels),*data.shape),np.uint8) if channels is not None else None
    yy,xx = np.mgrid[:height,:width]
    output = np.diag([width/canvas[0],height/canvas[1],1.])
    transforms = []
    for index,path in enumerate(paths):
        slot=slots[index]-1
        check(cancel)
        if progress and channels is None: progress(index,len(paths),path.name)
        matrix = identity() if matrices is None else np.asarray(matrices[index],float)
        if matrix.shape!=(3,3) or not np.all(np.isfinite(matrix)):
            raise ValueError(f'画像 {index+1} の変換が不正です')
        source_width,source_height = sizes[index]
        pad = np.array([[1.,0.,(canvas[0]-source_width)//2],[0.,1.,(canvas[1]-source_height)//2],[0.,0.,1.]])
        inv = np.linalg.inv(output @ matrix @ pad)
        if channels is not None:
            merged = np.zeros((height,width,3),np.uint32)
            for p,item in enumerate(channels):
                check(cancel)
                if progress: progress(index*len(channels)+p,len(paths)*len(channels),f'{path.name} · Ch {item["index"]+1}')
                with read_page(path,item['index']) as page:
                    working = page.convert('I') if page.mode.startswith('I;16') else page
                    try:
                        with working.transform((width,height),Image.Transform.AFFINE,tuple(inv[:2].ravel()),Image.Resampling.BILINEAR,fillcolor=0) as small:
                            converted = display_array(np.asarray(small),item.get('limits'))
                            gray = converted if converted.ndim==2 else np.rint(converted[...,:3].astype(float) @ np.array([.2126,.7152,.0722])).astype(np.uint8)
                            channel_data[p,slot] = gray
                            pixels = color_pixels(converted,item['color'])
                            merged += pixels[:,:,None] if pixels.ndim==2 else pixels
                    finally:
                        if working is not page: working.close()
            textures[slot] = np.clip(merged,0,255).astype(np.uint8)
            data[slot] = np.rint(textures[slot].astype(np.float32) @ np.array([.2126,.7152,.0722])).astype(np.uint8)
        else:
            with read_rgb(path,channel or 0) as source:
                with source.transform((width,height),Image.Transform.AFFINE,tuple(inv[:2].ravel()),
                                      Image.Resampling.BILINEAR,fillcolor='white') as small:
                    rgb = np.asarray(small,dtype=np.float32)
                    values = rgb[:,:,:3] @ color_weights(color)
                    data[slot] = np.clip(np.floor(values+.5),0,255).astype(np.uint8)
                    tinted = color_pixels(data[slot],color)
                    textures[slot] = rgb.astype(np.uint8) if colored[index] else tinted[:,:,None] if tinted.ndim==2 else tinted
        sx = inv[0,0]*(xx+.5)+inv[0,1]*(yy+.5)+inv[0,2]
        sy = inv[1,0]*(xx+.5)+inv[1,1]*(yy+.5)+inv[1,2]
        valid[slot] = (sx>=0)&(sy>=0)&(sx<source_width)&(sy<source_height)
        transforms.append(matrix.tolist())
    check(cancel)
    effective_spacing = (spacing[0]*canvas[0]/width,spacing[1]*canvas[1]/height,spacing[2])
    params = {'format':'BRAIN image stack','version':2,'axis_order':'ZYX','dtype':'uint8','unit':'micrometer',
              'input_files':[str(p.resolve()) for p in paths],'slice_indices':list(range(1,depth+1)),
              'included_slice_indices':list(slots),'excluded_slice_indices':[i for i in range(1,depth+1) if i not in slots],
              'source_canvas_xy':list(canvas),'shape_zyx':list(data.shape),'spacing_xyz':list(effective_spacing),
              'input_pixel_size_xy':list(spacing[:2]),'section_spacing':spacing[2],'color':color,
              'aligned':matrices is not None,'source_channel_index':channel+1 if channel is not None and channels is None else None,
              'merged_channels':channels is not None,
              'channel_display_settings':[{'channel_index':item['index']+1,'color':item['color'],'display_limits':item.get('limits')} for item in channels] if channels is not None else [],
              'source_to_common_transforms':transforms,'analysis':provenance or {},
              'origin':'first section z=0; voxel centers in XY; image Y increases downward',
              'preview':'alpha-composited aligned RGB image planes; no threshold or contour extraction',
              'anatomical_axis_z':'AP','ap_order':ap_order,'ap_reference':'first image; no absolute bregma coordinate',
              'rgb_shape_zyxc':list(textures.shape)}
    if progress: progress(len(paths)*(len(channels) if channels else 1),len(paths)*(len(channels) if channels else 1),'画像スタック構築完了')
    return Volume(data,valid,effective_spacing,params,textures,channel_data)


def display_channels(volume):
    if volume.channel_data is not None:
        return [(f'Ch {item["channel_index"]} ({item["color"]})',item['color']) for item in volume.params['channel_display_settings']]
    if volume.params.get('source_channel_index') is not None:
        return [(f'Ch {volume.params["source_channel_index"]}',volume.params.get('color','Gray'))]
    return [('Red','Red'),('Green','Green'),('Blue','Blue')]


def image_plane(volume,axis,index,channels=None,channel_opacities=None):
    """Return a selected-color section without copying the full RGB volume."""
    selection = (index,slice(None),slice(None)) if axis=='XY' else (slice(None),index,slice(None)) if axis=='XZ' else (slice(None),slice(None),index)
    base = volume.rgb[selection] if volume.rgb is not None else np.repeat(volume.data[selection][...,None],3,axis=-1)
    count = len(display_channels(volume))
    selected = tuple(range(count)) if channels is None else tuple(channels)
    if any(not isinstance(c,(int,np.integer)) or not 0<=c<count for c in selected): raise ValueError('表示チャンネルが不正です')
    if channel_opacities is not None:
        weights=channel_weights(volume,channel_opacities)
        if any(weights[c]!=1 for c in selected):
            result=np.zeros(base.shape,np.float32)
            for c in selected:
                if volume.channel_data is not None:
                    values=volume.channel_data[(c,*selection)]
                    pixels=color_pixels(values,volume.params['channel_display_settings'][c]['color'])
                    if pixels.ndim==2:pixels=pixels[...,None]
                elif count==1:pixels=base
                else:
                    pixels=np.zeros_like(base);pixels[...,c]=base[...,c]
                result+=pixels*weights[c]
            return result.clip(0,255).astype(np.uint8)
    if set(selected)==set(range(count)): return base
    if volume.channel_data is not None:
        result = np.zeros(base.shape,np.uint32)
        for c in selected:
            values = volume.channel_data[(c,*selection)]
            pixels = color_pixels(values,volume.params['channel_display_settings'][c]['color'])
            result += pixels[...,None] if pixels.ndim==2 else pixels
        return result.clip(0,255).astype(np.uint8)
    if count==1: return base if selected else np.zeros_like(base)
    mask = np.array([c in selected for c in range(3)],np.uint8)
    return base*mask


def channel_weights(volume,weights=None):
    count=len(display_channels(volume))
    values=np.ones(count,float) if weights is None else np.asarray(weights,float)
    if values.shape!=(count,) or not np.isfinite(values).all() or np.any(values<0) or np.any(values>1):
        raise ValueError('チャンネル不透明度は0〜1で指定してください。')
    return values


def stack_plane_style(volume,z,channels,weights,opacity,highlight_section):
    selected=tuple(range(len(weights))) if channels is None else tuple(channels)
    gain=max((weights[c] for c in selected),default=0.)
    rgb=image_plane(volume,'XY',z,channels,weights)
    if 0<gain<1:rgb=np.clip(np.rint(rgb.astype(np.float32)/gain),0,255).astype(np.uint8)
    section_alpha=opacity if highlight_section is None or opacity<=0 else max(opacity,.85) if z==highlight_section else opacity*.2
    return rgb,float(section_alpha*gain)


def physical_distance(volume,first,second):
    points=np.asarray([first,second],float)
    if points.shape!=(2,3) or not np.isfinite(points).all():raise ValueError('測定点が不正です。')
    shape=np.asarray(volume.data.shape)
    if np.any(points<0) or np.any(points>=shape):raise ValueError('測定点が画像外です。')
    for point in points:
        if not volume.valid[tuple(np.floor(point).astype(int))]:raise ValueError('画像のない位置は測定点にできません。')
    spacing=np.asarray(volume.spacing_xyz,float)
    if not np.isfinite(spacing).all() or np.any(spacing<=0):raise ValueError('実際の画素サイズ・切片間隔を設定してください。')
    coordinates=points[:,::-1]*spacing
    coordinates[:,:2]+=spacing[:2]/2
    components=np.abs(coordinates[1]-coordinates[0])
    return {'points_zyx':points.tolist(),'points_xyz_um':coordinates.tolist(),
        'distance_um':float(np.linalg.norm(components)),
        'components_um':dict(zip(('ML','DV','AP'),map(float,components))),
        'spacing_xyz_um':spacing.tolist(),'coordinate_reference':'XY voxel centers; first section AP=0; AP follows acquisition order'}


def _sections(volume,sections):
    indices = tuple(range(volume.data.shape[0])) if sections is None else tuple(sections)
    if len(indices)!=len(set(indices)) or any(not isinstance(z,(int,np.integer)) or not 0<=z<volume.data.shape[0] for z in indices):
        raise ValueError('表示する切片番号が不正です')
    return indices


def stack_geometry(volume,width,height,yaw=55.,pitch=20.,zoom=1.,ap_spacing_scale=1.,pan=(0.,0.)):
    """Project image X/Y and section AP/Z in physical units."""
    rotation = camera_rotation(yaw,pitch)
    depth,height_px,width_px = volume.data.shape
    sx,sy,sz = volume.spacing_xyz
    if not math.isfinite(ap_spacing_scale) or ap_spacing_scale<=0: raise ValueError('表示間隔の倍率が不正です')
    extent = np.array([width_px*sx,height_px*sy,(depth-1)*sz])
    scale = .78*min(width,height)/max(float(np.linalg.norm(extent)),1e-6)*zoom
    extent[2] *= ap_spacing_scale
    # Display AP increases from anterior to posterior, regardless of file order.
    # Only plane positions change; the image X/Y axes and pixel arrays stay intact.
    ap_sign = -1. if volume.params.get('ap_order')=='P_to_A' else 1.
    ap_positions = (np.arange(depth)*sz*ap_spacing_scale-extent[2]/2)*ap_sign
    origins = np.column_stack((np.full(depth,-extent[0]/2),np.full(depth,-extent[1]/2),ap_positions))
    projected = origins @ rotation.T
    projected[:,:2] += np.asarray(pan)/scale
    centers = np.column_stack((np.zeros(depth),np.zeros(depth),ap_positions)) @ rotation.T
    centers = centers[:,:2]*scale+np.array([width/2,height/2])+np.asarray(pan)
    return rotation,extent,scale,projected,centers


def project_image_stack(volume,width,height,yaw=55.,pitch=20.,zoom=1.,opacity=.15,black_transparent=False,ap_spacing_scale=1.,cancel=None,sections=None,channels=None,pan=(0.,0.),channel_opacities=None,highlight_section=None):
    """Render every section as an image plane, back to front, without segmentation."""
    if not math.isfinite(opacity) or not 0<=opacity<=1: raise ValueError('不透明度が不正です')
    width,height = max(1,int(width)),max(1,int(height))
    # Bound rendering resolution, keeping every AP section in the preview.
    factor = min(1.,512/max(width,height))
    rw,rh = max(1,round(width*factor)),max(1,round(height*factor))
    rotation,extent,scale,origins,centers = stack_geometry(volume,rw,rh,yaw,pitch,zoom,ap_spacing_scale,pan=np.asarray(pan)*factor)
    depth,h,w = volume.data.shape
    sx,sy,sz = volume.spacing_xyz
    screen = Image.new('RGBA',(rw,rh),(24,30,36,255))
    visible = set(_sections(volume,sections))
    weights=channel_weights(volume,channel_opacities)
    if highlight_section not in visible:highlight_section=None
    basis = rotation[:2,:2] @ np.diag([sx,sy])*scale
    # At exact side views, zero-thickness sections project to colored lines.
    if abs(np.linalg.det(basis))>1e-10:
        inverse = np.linalg.inv(basis)
        # Camera depth increases away from the viewer: paint far planes first.
        for z in np.argsort(origins[:,2])[::-1]:
            check(cancel)
            if z not in visible or (channels is not None and len(channels)==0): continue
            origin = origins[z,:2]*scale+np.array([rw/2,rh/2])
            corners = np.array([[0,0],[w,0],[w,h],[0,h]]) @ basis.T+origin
            left,top = np.maximum(np.floor(corners.min(axis=0)).astype(int),0)
            right,bottom = np.minimum(np.ceil(corners.max(axis=0)).astype(int),[rw,rh])
            if left>=right or top>=bottom: continue
            offset = inverse @ (np.array([left,top])-origin)
            coefficients = (inverse[0,0],inverse[0,1],offset[0],inverse[1,0],inverse[1,1],offset[1])
            rgb,section_alpha = stack_plane_style(volume,int(z),channels,weights,opacity,highlight_section)
            if section_alpha<=0:continue
            rgba = np.empty((h,w,4),np.uint8)
            rgba[:,:,:3] = rgb
            alpha = rgb.max(axis=-1).astype(np.float32)*section_alpha if black_transparent else np.full((h,w),255*section_alpha)
            rgba[:,:,3] = np.rint(alpha*volume.valid[z]).astype(np.uint8)
            with Image.fromarray(rgba) as texture:
                with texture.transform((int(right-left),int(bottom-top)),Image.Transform.AFFINE,
                                       coefficients,Image.Resampling.BILINEAR) as plane:
                    screen.alpha_composite(plane,(int(left),int(top)))
    else:
        yy,xx = np.mgrid[:h,:w]
        local = np.stack(((xx+.5)*sx,(yy+.5)*sy),axis=-1) @ rotation[:2,:2].T*scale
        for z in np.argsort(origins[:,2])[::-1]:
            check(cancel)
            if z not in visible or (channels is not None and len(channels)==0):continue
            rgb,section_alpha = stack_plane_style(volume,int(z),channels,weights,opacity,highlight_section)
            if section_alpha<=0:continue
            positions = np.floor(local+origins[z,:2]*scale+[rw/2,rh/2]).astype(int)
            px,py = positions[...,0],positions[...,1]
            mask = volume.valid[z]&(px>=0)&(px<rw)&(py>=0)&(py<rh)
            if not np.any(mask):continue
            # Max aggregation represents overlapping samples on a section edge;
            # alpha is applied once per section, independent of sampling density.
            edge = np.zeros((rh*rw,3),np.uint8)
            indices = py[mask]*rw+px[mask]
            np.maximum.at(edge,indices,rgb[mask])
            occupied = np.zeros(rh*rw,bool);occupied[indices] = True
            alpha = edge.max(axis=-1)*section_alpha if black_transparent else np.full(rh*rw,255*section_alpha)
            rgba = np.column_stack((edge,np.rint(alpha*occupied).astype(np.uint8))).reshape(rh,rw,4)
            with Image.fromarray(rgba) as plane:screen.alpha_composite(plane)
    check(cancel)
    result = screen.convert('RGB'); screen.close()
    if (rw,rh)!=(width,height):
        resized = result.resize((width,height),Image.Resampling.BILINEAR); result.close(); result = resized
    return result,rotation


def pick_stack_point(volume,x,y,width,height,yaw=55.,pitch=20.,zoom=1.,opacity=.15,black_transparent=False,ap_spacing_scale=1.,sections=None,channels=None,pan=(0.,0.),channel_opacities=None,highlight_section=None):
    """Pick the nearest displayed, nontransparent image pixel: return Z,Y,X."""
    if opacity<=0 or (channels is not None and len(channels)==0): return None
    rotation,extent,scale,origins,centers = stack_geometry(volume,width,height,yaw,pitch,zoom,ap_spacing_scale,pan=pan)
    basis = rotation[:2,:2] @ np.diag(volume.spacing_xyz[:2])*scale
    if abs(np.linalg.det(basis))<1e-10: return None
    inverse = np.linalg.inv(basis); visible = set(_sections(volume,sections))
    weights=channel_weights(volume,channel_opacities)
    if highlight_section not in visible:highlight_section=None
    for z in np.argsort(origins[:,2]):
        if z not in visible: continue
        origin = origins[z,:2]*scale+[width/2,height/2]
        u,v = inverse @ (np.array([x,y])-origin)
        if not 0<=u<volume.data.shape[2] or not 0<=v<volume.data.shape[1]: continue
        px,py = int(u),int(v)
        if not volume.valid[z,py,px]: continue
        rgb,section_alpha=stack_plane_style(volume,int(z),channels,weights,opacity,highlight_section)
        if section_alpha<=0:continue
        if black_transparent and round(float(rgb[py,px].max())*section_alpha)==0: continue
        return int(z),py,px
    return None


def save_volume(volume,path,view=None,cancel=None):
    path = Path(path).resolve()
    parent = path.parent.resolve(strict=True)
    params = dict(volume.params)
    params['view'] = view or {}
    check(cancel)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.BRAIN-volume-',suffix='.tmp',dir=parent,delete=False) as stream:
            temporary = Path(stream.name).resolve()
            if temporary.parent!=parent: raise ValueError('不正な保存先です')
            channel_arrays = {'channel_intensity':volume.channel_data} if volume.channel_data is not None else {}
            np.savez_compressed(stream,volume=volume.data,valid_mask=volume.valid,image_volume_rgb=volume.rgb if volume.rgb is not None else np.repeat(volume.data[...,None],3,axis=-1),**channel_arrays,
                                spacing_xyz=np.array(volume.spacing_xyz),params_json=np.array(json.dumps(params,ensure_ascii=False)))
        check(cancel)
        temporary.replace(path)
        return path
    finally:
        if temporary is not None and temporary.exists(): temporary.unlink()


def load_volume(path):
    """Read our numeric NPZ format without unpickling or trusting declared shapes."""
    import zipfile
    with zipfile.ZipFile(path) as archive:
        if sum(info.file_size for info in archive.infolist())>1024**3:raise ValueError('3Dデータが大きすぎます（展開後1GB上限）。')
    with np.load(path,allow_pickle=False) as saved:
        params=json.loads(str(saved['params_json'].item()))
        if params.get('format')!='BRAIN image stack':raise ValueError('BRAINの3Dデータではありません。')
        data=saved['volume'];valid=saved['valid_mask'];rgb=saved['image_volume_rgb'];spacing=saved['spacing_xyz']
        channels=saved['channel_intensity'] if 'channel_intensity' in saved else None
    if data.dtype!=np.uint8 or data.ndim!=3 or not all(data.shape) or data.size>64000000:raise ValueError('3D画像のサイズ・型が不正です。')
    if valid.dtype!=bool or valid.shape!=data.shape or rgb.dtype!=np.uint8 or rgb.shape!=(*data.shape,3):raise ValueError('3D画像の形状が一致しません。')
    if spacing.shape!=(3,) or not np.isfinite(spacing).all() or np.any(spacing<=0):raise ValueError('3D画像の実寸情報が不正です。')
    if channels is not None and (channels.dtype!=np.uint8 or channels.ndim!=4 or channels.shape[1:]!=data.shape or len(channels)!=len(params.get('channel_display_settings',[]))):raise ValueError('3Dチャンネル情報が不正です。')
    return Volume(data,valid,spacing,params,rgb,channels)
