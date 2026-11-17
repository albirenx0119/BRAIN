# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""An independently interactive orientation cube in image X/Y and AP coordinates."""
import itertools
import math
import tkinter as tk
import numpy as np
from PIL import Image,ImageTk

FACES = {
    'FRONT':((0,0,-1),'FRONT\nA'), 'BACK':((0,0,1),'BACK\nP'),
    'TOP':((0,-1,0),'TOP'), 'BOTTOM':((0,1,0),'BOTTOM'),
    'LEFT':((-1,0,0),'LEFT'), 'RIGHT':((1,0,0),'RIGHT'),
}
VERTICES = np.array(list(itertools.product((-1.,1.),repeat=3)))


def camera_rotation(yaw,pitch):
    a,b = math.radians(yaw),math.radians(pitch)
    ry = np.array([[math.cos(a),0,math.sin(a)],[0,1,0],[-math.sin(a),0,math.cos(a)]])
    rx = np.array([[1,0,0],[0,math.cos(b),-math.sin(b)],[0,math.sin(b),math.cos(b)]])
    return rx @ ry


def direction_angles(direction):
    normal = np.asarray(direction,float)
    normal /= np.linalg.norm(normal)
    yaw = math.degrees(math.atan2(normal[0],-normal[2])) if abs(normal[1])<1-1e-12 else 0.
    pitch = math.degrees(math.asin(float(np.clip(-normal[1],-1,1))))
    return yaw,pitch


def cube_geometry(yaw,pitch,width=150,height=150):
    rotation = camera_rotation(yaw,pitch)
    projected = VERTICES @ rotation.T
    scale = min(width,height-22)/4.1
    points = projected[:,:2]*scale+[width/2,(height-22)/2+5]
    faces = []
    for name,(normal,label) in FACES.items():
        normal = np.asarray(normal,float)
        if (rotation @ normal)[2]>=-1e-8: continue
        axis = int(np.flatnonzero(normal)[0])
        indices = np.flatnonzero(VERTICES[:,axis]==normal[axis])
        polygon = points[indices]
        center = polygon.mean(axis=0)
        order = np.argsort(np.arctan2(polygon[:,1]-center[1],polygon[:,0]-center[0]))
        indices = indices[order]
        faces.append({'name':name,'label':label,'normal':normal,'indices':indices,
                      'polygon':points[indices],'center':center,'depth':float(projected[indices,2].mean())})
    return points,sorted(faces,key=lambda face:face['depth'],reverse=True)


def inside(point,polygon):
    x,y = point; result = False
    for a,b in zip(polygon,np.roll(polygon,-1,axis=0)):
        if (a[1]>y)!=(b[1]>y) and x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:result = not result
    return result


def cube_target(x,y,yaw,pitch,width=150,height=150):
    points,faces = cube_geometry(yaw,pitch,width,height)
    if not faces: return None
    visible = set(int(i) for face in faces for i in face['indices'])
    point = np.array([x,y],float)
    nearest = min(visible,key=lambda i:np.linalg.norm(points[i]-point))
    if np.linalg.norm(points[nearest]-point)<=9:return tuple(VERTICES[nearest])
    edges = set()
    for face in faces:
        indices = face['indices']
        for a,b in zip(indices,np.roll(indices,-1)):edges.add(tuple(sorted((int(a),int(b)))))
    best = None
    for a,b in edges:
        start,end = points[a],points[b]; delta = end-start
        if delta @ delta<1e-8:continue
        t = np.clip((point-start) @ delta/(delta @ delta),0,1)
        distance = np.linalg.norm(point-(start+t*delta))
        if distance<=6 and (best is None or distance<best[0]):best = (distance,(VERTICES[a]+VERTICES[b])/2)
    if best is not None:return tuple(best[1])
    for face in reversed(faces):
        if inside(point,face['polygon']):return tuple(face['normal'])
    return None


class ViewCube(tk.Canvas):
    def __init__(self,parent,command):
        super().__init__(parent,width=150,height=150,highlightthickness=0,
                         borderwidth=0,background=parent.cget('background'),cursor='hand2',takefocus=True)
        self.background_photo=None
        self.command = command
        self.yaw,self.pitch = 55.,20.
        self._drag = None
        self._moved = False
        self.bind('<Configure>',lambda event:self.draw())
        self.bind('<ButtonPress-1>',self.press)
        self.bind('<B1-Motion>',self.drag)
        self.bind('<ButtonRelease-1>',self.release)
        self.bind('<Double-Button-1>',self.home)
        self.bind('<Motion>',self.hover)
        self.bind('<Leave>',lambda event:self.draw())

    def set_view(self,yaw,pitch):
        self.yaw,self.pitch = float(yaw),float(pitch)
        self.draw()

    def set_background(self,image):
        width,height=max(1,self.winfo_width()),max(1,self.winfo_height())
        with Image.new('RGB',(width,height),self.master.cget('background')) as tile:
            tile.paste(image,(-self.winfo_x(),-self.winfo_y()))
            self.background_photo=ImageTk.PhotoImage(tile,master=self)
        self.draw()

    def draw(self,highlight=None):
        self.delete('all')
        width,height = max(1,self.winfo_width()),max(1,self.winfo_height())
        if self.background_photo is not None:self.create_image(0,0,image=self.background_photo,anchor='nw')
        points,faces = cube_geometry(self.yaw,self.pitch,width,height)
        for face in faces:
            shade = {'TOP':'#eeeeee','BOTTOM':'#aaaaaa','LEFT':'#bbbbbb','RIGHT':'#cccccc','FRONT':'#dddddd','BACK':'#bbbbbb'}[face['name']]
            active = highlight is not None and np.array_equal(highlight,face['normal'])
            self.create_polygon(*face['polygon'].ravel(),fill='#ffffff' if active else shade,
                                outline='#555555',width=2 if active else 1)
            self.create_text(*face['center'],text=face['label'],fill='#151515',font=('Segoe UI',8,'bold'))
        if highlight is not None and np.count_nonzero(highlight)>1:
            point = np.asarray(highlight) @ camera_rotation(self.yaw,self.pitch).T
            xy = point[:2]*(min(width,height-22)/4.1)+[width/2,(height-22)/2+5]
            self.create_oval(xy[0]-4,xy[1]-4,xy[0]+4,xy[1]+4,fill='white',outline='#222222')

    def target(self,event):return cube_target(event.x,event.y,self.yaw,self.pitch,max(1,self.winfo_width()),max(1,self.winfo_height()))

    def hover(self,event):
        if self._drag is None:self.draw(self.target(event))

    def press(self,event):
        self.focus_set();self._drag = (event.x,event.y,self.yaw,self.pitch);self._moved = False
        return 'break'

    def drag(self,event):
        if self._drag is None:return 'break'
        x,y,yaw,pitch = self._drag
        if abs(event.x-x)+abs(event.y-y)<4:return 'break'
        self._moved = True
        self.set_view((yaw-(event.x-x)*.5+180)%360-180,(pitch+(event.y-y)*.5+180)%360-180)
        self.command(self.yaw,self.pitch)
        return 'break'

    def release(self,event):
        clicked = self._drag is not None and not self._moved
        self._drag = None
        if clicked:
            direction = self.target(event)
            if direction is not None:
                self.set_view(*direction_angles(direction));self.command(self.yaw,self.pitch)
        return 'break'

    def home(self,event=None):
        self._drag = None;self.set_view(55.,20.);self.command(self.yaw,self.pitch)
        return 'break'
