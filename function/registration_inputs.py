# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Native-coordinate fitting planes and resource-based admission checks."""
import math
from contextlib import ExitStack
import os
import numpy as np
from PIL import Image
from image_sources import read_page,is_tiff
from colors import color_weights


def available_memory():
    if os.name=='nt':
        import ctypes
        class Memory(ctypes.Structure):
            _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[
                (name,ctypes.c_ulonglong) for name in ('total','available','page_total','page_available','virtual_total','virtual_available','extended')]
        state=Memory();state.length=ctypes.sizeof(state)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
            return int(state.available)
    try:return int(os.sysconf('SC_AVPHYS_PAGES')*os.sysconf('SC_PAGE_SIZE'))
    except (ValueError,AttributeError,OSError):return None


def grid_shape(canvas,limit):
    if limit is not None and (isinstance(limit,bool) or not isinstance(limit,(int,np.integer)) or limit<2):
        raise ValueError('計算用画像の最大辺は2以上の整数を指定してください')
    scale=1. if limit is None else min(1.,limit/max(canvas))
    width,height=(max(1,math.floor(side*scale+1e-9)) for side in canvas)
    return (width,height),scale


def validate_plan(sizes,limit):
    canvas=tuple(max(size[axis] for size in sizes) for axis in (0,1))
    shape,scale=grid_shape(canvas,limit)
    if min(shape)<2 or math.prod(shape)<12:
        raise ValueError('計算用画像の有効画素が不足します。最大辺を大きくしてください')
    # Current OpenCV sampling kernels use signed-short internal coordinates.
    # This checks the working plane, not the original/export canvas.
    coordinate_limit=int(np.iinfo(np.int16).max)
    if max(shape)>=coordinate_limit:
        raise ValueError('計算用画像が補間処理の座標範囲を超えます。縮小解像度を選んでください')
    estimated=32*max(math.prod(size) for size in sizes)+24*math.prod(shape)
    available=available_memory()
    if available is not None and estimated>available*.60:
        raise ValueError(f'計算用メモリの見積もり約{estimated/1024**3:.1f} GBが現在の空き容量に対して大きすぎます。解像度を下げるか他のソフトを閉じてください')
    return canvas,{'working_size':list(shape),'scale':scale,'estimated_bytes':estimated,
                   'available_bytes_at_start':available,'memory_fraction':.60}


def prepare(path,canvas,color,limit=None,channel=None):
    """Resample finite/opaque native samples without a center-padding frame."""
    from alignment import Proxy
    shape,scale=grid_shape(canvas,limit)
    with ExitStack() as stack:
        source=stack.enter_context(read_page(path,channel or 0))
        sw,sh=source.size
        if source.mode in ('P','LA'):source=stack.enter_context(source.convert('RGBA'))
        raw=np.asarray(source)
        values=raw.astype(np.float32)
        alpha=np.ones((sh,sw),np.float32)
        if values.ndim==3:
            if values.shape[-1]==4:alpha=values[...,3]/255.
            values=values[...,:3] @ color_weights('Gray' if channel is not None else color)
        if not is_tiff(path):values=values/255.
        support=np.isfinite(values).astype(np.float32)*alpha
        numerator=np.nan_to_num(values,nan=0.,posinf=0.,neginf=0.)*support
        transform=(1/scale,0,0,0,1/scale,0)
        # Resample coverage with the same kernel as the signal; no fixed inset.
        with Image.fromarray(numerator) as plane,Image.fromarray(support) as coverage:
            with plane.transform(shape,Image.Transform.AFFINE,transform,Image.Resampling.BILINEAR,fillcolor=0) as small:
                data=np.array(small,dtype=np.float32)
            with coverage.transform(shape,Image.Transform.AFFINE,transform,Image.Resampling.BILINEAR,fillcolor=0) as small:
                weights=np.array(small,dtype=np.float32)
    valid=weights>=.995
    np.divide(data,weights,out=data,where=valid)
    data[~valid]=0
    offset=(((canvas[0]-sw)//2)*scale,((canvas[1]-sh)//2)*scale)
    return Proxy(data,valid,offset),scale


def change_basis(matrix,fixed,moving,to_canvas):
    """Convert fitting/native plane coordinates to the existing export frame."""
    result=np.asarray(matrix,float).copy()
    fixed_offset=np.asarray(fixed.canvas_offset,float)
    moving_offset=np.asarray(moving.canvas_offset,float)
    correction=fixed_offset-result[:2,:2]@moving_offset
    result[:2,2]+=correction if to_canvas else -correction
    return result
