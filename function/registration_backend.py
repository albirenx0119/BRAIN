# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""NCC-driven rigid search with data-derived seeds and joint simplex refinement.

Uses an area-budget pyramid, FFT translation peaks for rotated planes, and
Nelder-Mead refinement in pixel-displacement coordinates.
"""
import math
import cv2
import numpy as np

ENGINE='ncc_fft_simplex_area_pyramid'
MIN_QUALITY=.40
REVIEW_QUALITY=.8


class RegistrationFailure(ValueError):
    def __init__(self,score=-1.):
        super().__init__('Registration failed quality checks')
        self.score=float(score)


def _index_matrix(matrix):
    result=np.asarray(matrix,float).copy()
    result[:2,2]+=result[:2,:2]@np.array([.5,.5])-.5
    return result


def _physical_matrix(matrix):
    result=np.asarray(matrix,float).copy()
    result[:2,2]+=.5-result[:2,:2]@np.array([.5,.5])
    return result


def quality(fixed,moving,matrix,check_cancel=lambda:None):
    """Pearson correlation on actual image support, with stable block statistics."""
    check_cancel()
    height,width=fixed.data.shape
    moving_values=np.ascontiguousarray(moving.data,dtype=np.float32)
    moving_mask=np.asarray(moving.mask,dtype=np.float32)
    transform=_index_matrix(matrix)
    count=0;mean=np.zeros(2);scatter=np.zeros((2,2))
    eligible=np.count_nonzero(fixed.mask)
    for row in range(0,height,256):
        check_cancel();rows=min(256,height-row)
        local=transform[:2].copy();local[1,2]-=row
        warped=cv2.warpAffine(moving_values,local,(width,rows),flags=cv2.INTER_LINEAR)
        support=cv2.warpAffine(moving_mask,local,(width,rows),flags=cv2.INTER_LINEAR)
        valid=np.asarray(fixed.mask[row:row+rows],bool)&(support>=1-1e-6)
        values=np.column_stack((fixed.data[row:row+rows][valid],warped[valid])).astype(np.float64)
        values=values[np.isfinite(values).all(axis=1)]
        batch=len(values)
        if not batch:continue
        center=values.mean(axis=0);residual=values-center
        delta=center-mean;combined=count+batch
        scatter+=residual.T@residual+np.outer(delta,delta)*(count*batch/combined)
        mean+=delta*(batch/combined);count=combined
    if count<max(12,math.ceil(eligible*.1)) or min(scatter[0,0],scatter[1,1])<=np.finfo(float).eps*count:
        return -1.
    return float(np.clip(scatter[0,1]/math.sqrt(scatter[0,0]*scatter[1,1]),-1,1))


def pyramid_scales(shape):
    """Use an area budget; protect thin images rather than fixed side cutoffs."""
    height,width=shape
    floor=min(1.,max(math.sqrt(18_000/(height*width)),20/min(shape)))
    descending=[1.]
    while descending[-1]/1.7 > floor*(1+1e-9):
        descending.append(descending[-1]/1.7)
    if floor < descending[-1]-1e-9:descending.append(floor)
    return list(reversed(descending))


def _level(image,scale):
    if scale==1.:return image
    from alignment import Proxy
    height,width=image.data.shape
    shape=(math.ceil(width*scale),math.ceil(height*scale))
    transform=np.array([[scale,0,(scale-1)/2],[0,scale,(scale-1)/2]],float)
    values=cv2.warpAffine(np.asarray(image.data,np.float32),transform,shape,flags=cv2.INTER_LINEAR)
    support=cv2.warpAffine(np.asarray(image.mask,np.float32),transform,shape,flags=cv2.INTER_LINEAR)>.999
    return Proxy(values,support)


def _signal(image):
    values=np.asarray(image.data,np.float32)
    support=np.asarray(image.mask,bool)&np.isfinite(values)
    samples=values[support]
    if samples.size<12 or float(samples.std())<1e-8:raise RegistrationFailure()
    lo,hi=np.percentile(samples,[2,98])
    if hi<=lo:lo,hi=float(samples.min()),float(samples.max())
    if hi<=lo:raise RegistrationFailure()
    normalized=np.clip((np.nan_to_num(values)-lo)/(hi-lo),0,1)
    normalized=(normalized-float(normalized[support].mean()))*support
    return normalized.astype(np.float32)


def angle_candidates(shape):
    """Angular spacing follows the displacement of an image corner."""
    radius=math.hypot(*shape)/2
    step=math.degrees(math.atan2(12.,radius))
    divisions=min(24,max(4,math.ceil(54./step)))
    return np.linspace(-54.,54.,2*divisions+1)


def _matrix(parameters,shape):
    from alignment import rigid
    height,width=shape
    radius=math.hypot(width,height)/2
    return rigid(parameters[0],parameters[1],math.degrees(parameters[2]/radius),width,height)


def _peaks(fixed,moving,number=3):
    """Linear FFT correlation avoids periodic wraparound translation guesses."""
    height,width=fixed.shape
    shape=(cv2.getOptimalDFTSize(2*height-1),cv2.getOptimalDFTSize(2*width-1))
    cross=np.fft.rfft2(fixed,s=shape)*np.conj(np.fft.rfft2(moving,s=shape))
    # Partial whitening keeps broad tissue structure as well as fine edges.
    cross/=np.maximum(np.abs(cross),1e-12)**.5
    response=np.fft.irfft2(cross,s=shape).real
    yy=np.arange(shape[0]);xx=np.arange(shape[1])
    dy=np.where(yy<height,yy,yy-shape[0])
    dx=np.where(xx<width,xx,xx-shape[1])
    allowed=(np.abs(dy[:,None])<=height*.55)&(np.abs(dx[None,:])<=width*.55)
    response[~allowed]=-np.inf
    result=[]
    separation=max(2,int(round(min(height,width)*.035)))
    for _ in range(number):
        row,column=np.unravel_index(np.argmax(response),response.shape)
        if not np.isfinite(response[row,column]):break
        result.append((float(dx[column]),float(dy[row])))
        neighborhood=(np.abs(dy[:,None]-dy[row])<=separation)&(np.abs(dx[None,:]-dx[column])<=separation)
        response[neighborhood]=-np.inf
    return result


def _seeds(fixed,moving,method,check_cancel):
    f,m=_signal(fixed),_signal(moving)
    shape=f.shape;height,width=shape;radius=math.hypot(width,height)/2
    seeds=[np.zeros(3)]
    for angle in ([0.] if method=='translation' else angle_candidates(shape)):
        check_cancel()
        p=np.array([0.,0.,math.radians(angle)*radius])
        rotated=cv2.warpAffine(m,_index_matrix(_matrix(p,shape))[:2],(width,height),flags=cv2.INTER_LINEAR)
        for dx,dy in _peaks(f,rotated):seeds.append(np.array([dx,dy,p[2]]))
    return seeds


def _simplex(objective,initial,step,tolerance,budget,dimensions,check_cancel):
    """Joint Nelder-Mead updates; stop by geometric spread, not a step table."""
    vertices=[initial.copy()]
    for axis in range(dimensions):
        vertex=initial.copy();vertex[axis]+=step;vertices.append(vertex)
    values=[objective(v) for v in vertices];used=len(values)
    def evaluate(point):
        nonlocal used
        check_cancel();used+=1
        return objective(point)
    while used<budget:
        order=np.argsort(values);vertices=[vertices[i] for i in order];values=[values[i] for i in order]
        spread=max(float(np.linalg.norm(v-vertices[0])) for v in vertices[1:])
        if spread<tolerance:break
        center=np.mean(vertices[:-1],axis=0)
        reflection=center+(center-vertices[-1]);reflected=evaluate(reflection)
        if reflected<values[0]:
            expansion=center+2*(reflection-center);expanded=evaluate(expansion)
            point,value=(expansion,expanded) if expanded<reflected else (reflection,reflected)
        elif reflected<values[-2]:point,value=reflection,reflected
        else:
            outside=reflected<values[-1]
            contraction=center+.5*((reflection if outside else vertices[-1])-center)
            contracted=evaluate(contraction)
            if contracted < (reflected if outside else values[-1]):point,value=contraction,contracted
            else:
                for index in range(1,len(vertices)):
                    vertices[index]=vertices[0]+.5*(vertices[index]-vertices[0])
                    values[index]=evaluate(vertices[index])
                continue
        vertices[-1],values[-1]=point,value
    best=int(np.argmin(values))
    return vertices[best],-values[best]


def estimate(fixed,moving,method='rigid',check_cancel=lambda:None):
    check_cancel()
    if method not in ('translation','rigid'):raise ValueError('不正なアライン方法です')
    if fixed.data.shape!=moving.data.shape or min(fixed.data.shape)<2 or fixed.data.size<12:
        raise ValueError('計算用画像の形状または有効画素数が不足しています')
    scales=pyramid_scales(fixed.data.shape)
    candidates=None;previous=None
    for level,scale in enumerate(scales):
        check_cancel();f,m=_level(fixed,scale),_level(moving,scale)
        local_radius=math.hypot(*f.data.shape)/2
        def objective(p):
            if abs(p[0])>f.data.shape[1]*.6 or abs(p[1])>f.data.shape[0]*.6 or abs(p[2]/local_radius)>math.radians(66):return 2.
            return -quality(f,m,_matrix(p,f.data.shape),check_cancel)
        if candidates is None:
            ranked=sorted(((objective(p),p) for p in _seeds(f,m,method,check_cancel)),key=lambda item:item[0])
            candidates=[]
            for value,p in ranked:
                if all(np.linalg.norm(p-q)>2.5 for q in candidates):candidates.append(p)
                if len(candidates)==3:break
        else:
            factor=scale/previous
            # Rotation is expressed as corner displacement, whose radius rounds.
            candidates=[np.array([p[0]*factor,p[1]*factor,p[2]*local_radius/previous_radius]) for p in candidates]
        fitted=[]
        for candidate in candidates:
            step=max(1.4,min(f.data.shape)/45) if level==0 else 1.4
            p,score=_simplex(objective,candidate,step,.05+.1*(1-scale),240 if level==0 else 190,
                             2 if method=='translation' else 3,check_cancel)
            fitted.append((score,p))
        fitted.sort(key=lambda item:item[0],reverse=True)
        candidates=[p for score,p in fitted[:2]]
        previous,previous_radius=scale,local_radius
    score,p=max(fitted,key=lambda item:item[0])
    if not np.isfinite(score) or score<MIN_QUALITY:raise RegistrationFailure(score)
    return _matrix(p,fixed.data.shape),float(score)
