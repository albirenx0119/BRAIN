# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
# SPDX-License-Identifier: Apache-2.0
# Maintained by Ren Y Sato.
"""Offline, masked multiresolution correlation with rigid image geometry."""
from __future__ import annotations
import math
from dataclasses import dataclass
import numpy as np
from PIL import Image, ImageOps
from image_sources import is_tiff,read_page,display_rgb,describe
from colors import valid_color,color_weights


class Cancelled(Exception):
    pass


class LowCorrelation(ValueError):
    def __init__(self, score):
        super().__init__('模様または類似度が不足しています')
        self.score = score


def check(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def identity():
    return np.eye(3, dtype=float)


def rigid(x, y, angle, width, height):
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    cx, cy = width/2, height/2
    return np.array([[c,-s,cx+x-c*cx+s*cy],[s,c,cy+y-s*cx-c*cy],[0.,0.,1.]])


@dataclass
class Proxy:
    data: np.ndarray
    mask: np.ndarray
    canvas_offset: tuple = (0.,0.)


def read_rgb(path,page=0):
    if is_tiff(path):
        with read_page(path,page) as source: return display_rgb(source)
    with Image.open(path) as source:
        if source.format not in ('PNG','JPEG'):
            raise ValueError(f'{path.name}: PNG/JPEGではありません')
        with ImageOps.exif_transpose(source) as oriented:
            rgba = oriented.convert('RGBA')
    rgb = Image.new('RGB', rgba.size, 'white')
    rgb.paste(rgba, mask=rgba.getchannel('A'))
    rgba.close()
    return rgb


def proxy(path,canvas,color,limit=None,channel=None):
    from registration_inputs import prepare
    return prepare(path,canvas,color,limit,channel)


class NCC:
    """All-supported-pixel Pearson score, evaluated in bounded blocks."""
    def __init__(self,fixed,moving,cancel=None):
        self.fixed,self.moving,self.cancel=fixed,moving,cancel

    def __call__(self,matrix):
        from registration_backend import quality
        from registration_inputs import change_basis
        sample_matrix=change_basis(matrix,self.fixed,self.moving,False)
        return quality(self.fixed,self.moving,sample_matrix,lambda:check(self.cancel))


def register(fixed,moving,method='rigid',cancel=None):
    from registration_backend import estimate,RegistrationFailure
    from registration_inputs import change_basis
    try:
        matrix,score=estimate(fixed,moving,method,lambda:check(cancel))
        return change_basis(matrix,fixed,moving,True),score
    except RegistrationFailure as error:raise LowCorrelation(error.score) from error


def align_files(paths,current,scope,color,method,existing=None,cancel=None,progress=None,proxy_limit=None,channel=None):
    from registration_inputs import grid_shape,validate_plan
    grid_shape((2,2),proxy_limit)
    if len(paths)<2 or scope not in ('Reference','Global') or not valid_color(color):
        raise ValueError('画像枚数または設定が不正です')
    if not 0<=current<len(paths):
        raise ValueError('画像番号が不正です')
    sizes = []
    for path in paths:
        check(cancel)
        if channel is not None:
            info = describe(path)
            if not is_tiff(path) or not 0<=channel<info['pages']: raise ValueError('指定チャンネルが存在しません')
        with Image.open(path) as source:
            w,h = source.size
            if source.getexif().get(274,1) in (5,6,7,8):
                w,h = h,w
            sizes.append((w,h))
    canvas,plan=validate_plan(sizes,proxy_limit)
    if scope=='Global':
        result=align_global(paths,canvas,color,method,cancel,progress,proxy_limit,channel)
    else:
        from reference_registration import run
        result=run(paths,current,canvas,color,method,cancel,progress,proxy_limit,channel)
    result[1][current]['resource_plan']=plan
    return result


def wrap_angle(angle):
    return (angle+np.pi)%(2*np.pi)-np.pi


def solve_differences(count,left,right,weights,values,cancel=None,initial=None):
    """Solve a graph Laplacian in the zero-mean subspace, without an anchor."""
    def accumulate(edge_values):
        return (np.bincount(right,weights=edge_values,minlength=count)
                -np.bincount(left,weights=edge_values,minlength=count))
    def multiply(x):
        return accumulate(weights*(x[right]-x[left]))
    b = accumulate(weights*values)
    x = np.zeros(count) if initial is None else initial.copy()-initial.mean()
    residual = b-multiply(x)
    direction = residual.copy()
    norm = float(residual @ residual)
    threshold = max(1e-18,float(b @ b)*1e-18)
    for iteration in range(max(100,min(10000,count*8))):
        if norm<=threshold:
            break
        check(cancel)
        product = multiply(direction)
        denominator = float(direction @ product)
        if denominator<=0:
            raise ValueError('全体最適化の数値計算に失敗しました')
        step = norm/denominator
        x += step*direction
        residual -= step*product
        next_norm = float(residual @ residual)
        direction = residual+(next_norm/norm)*direction
        norm = next_norm
    else:
        raise ValueError('全体最適化が収束しませんでした')
    return x-x.mean()


def optimize_graph(count,edges,canvas,method,cancel=None):
    """Jointly balance measured rigid poses; mean angle and center shift are zero."""
    if not edges:
        raise ValueError('全体最適化に使える画像の対応がありません')
    neighbors = [[] for _ in range(count)]
    for i,j,matrix,score in edges:
        angle = math.atan2(matrix[1,0],matrix[0,0]) if method=='rigid' else 0.
        neighbors[i].append((j,angle))
        neighbors[j].append((i,-angle))
    angles = np.zeros(count)
    visited = {0}
    pending = [0]
    for i in pending:
        check(cancel)
        for j,angle in neighbors[i]:
            if j not in visited:
                visited.add(j); pending.append(j)
                angles[j] = angles[i]+angle
    if len(visited)!=count:
        missing = ', '.join(str(i+1) for i in range(count) if i not in visited)
        raise ValueError(f'画像の対応が途切れています（画像 {missing}）。色や画像を確認してください')
    angles -= angles.mean()
    left = np.array([e[0] for e in edges],dtype=int)
    right = np.array([e[1] for e in edges],dtype=int)
    measured = np.array([math.atan2(e[2][1,0],e[2][0,0]) for e in edges])
    center = np.array([canvas[0]/2,canvas[1]/2,1.])
    shifts = np.array([(e[2] @ center-center)[:2] for e in edges])
    base_weights = np.array([max(.01,e[3])**2 for e in edges])
    weights = base_weights.copy()
    offsets = np.zeros((count,2))
    radius = max(canvas)/2
    for iteration in range(40):
        check(cancel)
        before_angles,before_offsets = angles.copy(),offsets.copy()
        if method=='rigid':
            residual = wrap_angle(angles[right]-angles[left]-measured)
            angles += solve_differences(count,left,right,weights,-residual,cancel)
            angles -= angles.mean()
        c,s = np.cos(angles[left]),np.sin(angles[left])
        target = np.column_stack((c*shifts[:,0]-s*shifts[:,1],s*shifts[:,0]+c*shifts[:,1]))
        for axis in range(2):
            offsets[:,axis] = solve_differences(count,left,right,weights,target[:,axis],cancel,offsets[:,axis])
        errors = offsets[right]-offsets[left]-target
        angular_error = wrap_angle(angles[right]-angles[left]-measured)*radius if method=='rigid' else 0.
        residual_norm = np.sqrt(np.sum(errors**2,axis=1)+angular_error**2)
        updated = base_weights*np.minimum(1.,3./np.maximum(residual_norm,1e-12))
        difference = max(float(np.max(np.abs(offsets-before_offsets))),
                         float(np.max(np.abs(angles-before_angles)))*radius)
        stable = np.max(np.abs(updated-weights))<1e-6
        weights = updated
        if difference<1e-5 and stable:
            break
    else:
        raise ValueError('全体最適化が収束しませんでした。画像の対応を確認してください')
    matrices = [rigid(*offsets[i],math.degrees(angles[i]),*canvas) for i in range(count)]
    return matrices,residual_norm


def align_global(paths,canvas,color,method,cancel,progress,proxy_limit=None,channel=None):
    # Local overlapping constraints form loops without comparing distant anatomy.
    pairs = [(i,j) for i in range(len(paths)) for j in range(i+1,min(len(paths),i+3))]
    edges = []
    failures = set()
    cached = {}
    for number,(i,j) in enumerate(pairs):
        check(cancel)
        if progress: progress(number,len(pairs),f'画像 {i+1} ↔ {j+1}')
        for index in (i,j):
            if index not in cached:
                cached[index] = proxy(paths[index],canvas,color,proxy_limit,channel)
        fixed,scale = cached[i]
        moving,_ = cached[j]
        try:
            matrix,score = register(fixed,moving,method,cancel)
            matrix[:2,2] /= scale
            edges.append((i,j,matrix,score))
        except LowCorrelation:
            failures.update((i,j))
        cached = {index:value for index,value in cached.items() if index>=i}
    if progress: progress(len(pairs),len(pairs),'全画像の位置・回転を最適化中')
    matrices,errors = optimize_graph(len(paths),edges,canvas,method,cancel)
    records = []
    for index in range(len(paths)):
        related = [k for k,e in enumerate(edges) if index in e[:2]]
        score = float(np.mean([edges[k][3] for k in related]))
        warning = []
        if score<.8: warning.append('相関が低いため画像を確認してください')
        if index in failures: warning.append('近傍画像との一部の比較で失敗しています')
        if any(errors[k]>3. for k in related): warning.append('画像間の整合性を確認してください')
        records.append({'score':score,'warning':' / '.join(warning) or None,'parent':None})
    check(cancel)
    return matrices,records,canvas


def render_result(path,matrix,canvas):
    with read_rgb(path) as source:
        left,top = (canvas[0]-source.width)//2,(canvas[1]-source.height)//2
        inv = np.linalg.inv(matrix)
        coeff = (inv[0,0],inv[0,1],inv[0,2]-left,inv[1,0],inv[1,1],inv[1,2]-top)
        return source.transform(canvas,Image.Transform.AFFINE,coeff,Image.Resampling.BILINEAR,fillcolor='white')
