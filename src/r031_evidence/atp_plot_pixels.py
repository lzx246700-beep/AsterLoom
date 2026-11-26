"""Read-only RGB/RGBA PNG pixels for bounded ATP graph analysis.

No image is modified or rendered. Unsupported PNG formats fail closed. Graph
digitization is an estimate and cannot turn into an author-reported number.
"""
import struct
import zlib


def read_image(path):
    from pathlib import Path
    import base64,json,shutil,subprocess
    p=Path(path);raw=p.read_bytes()
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):return read_png(raw)
    if not raw.startswith(b'\xff\xd8'):raise ValueError('ATP_SOURCE_RASTER_FORMAT_HOLD')
    swift=shutil.which('swift')
    if not swift:raise ValueError('ATP_OPTIONAL_MACOS_IMAGEIO_BACKEND_UNAVAILABLE')
    script=Path(__file__).parent/'data/atp_read_image.swift'
    run=subprocess.run([swift,str(script),str(p.resolve())],capture_output=True,text=True,timeout=30,check=True)
    result=json.loads(run.stdout);w,h=result['width'],result['height']
    if not 0<w*h<=6_000_000 or result['bpp']!=4 or result['source_modified'] is not False:
        raise ValueError('ATP_IMAGEIO_PIXEL_BUDGET_HOLD')
    pixels=base64.b64decode(result['rgba_base64'],validate=True)
    if len(pixels)!=w*h*4:raise ValueError('ATP_IMAGEIO_PIXEL_LENGTH_HOLD')
    return {'width':w,'height':h,'bpp':4,'rows':[pixels[i*w*4:(i+1)*w*4] for i in range(h)],
            'pixel_backend':result['backend']}


def read_png(raw, max_pixels=6_000_000):
    if not raw.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('ATP_PLOT_PNG_ONLY_HOLD')
    pos=8;data=bytearray();header=None;end=False
    while pos+12<=len(raw):
        n=struct.unpack('>I',raw[pos:pos+4])[0];kind=raw[pos+4:pos+8]
        if pos+12+n>len(raw):raise ValueError('ATP_PNG_TRUNCATED')
        body=raw[pos+8:pos+8+n];crc=struct.unpack('>I',raw[pos+8+n:pos+12+n])[0]
        if zlib.crc32(kind+body)&0xffffffff!=crc:raise ValueError('ATP_PNG_CRC_MISMATCH')
        if kind==b'IHDR':header=struct.unpack('>IIBBBBB',body)
        elif kind==b'IDAT':data.extend(body)
        elif kind==b'IEND':end=True;break
        pos+=12+n
    if not header or not end:raise ValueError('ATP_PNG_STRUCTURE_HOLD')
    width,height,depth,color,compression,filter_method,interlace=header
    if depth!=8 or color not in (2,6) or compression or filter_method or interlace:
        raise ValueError('ATP_PNG_RGB8_NONINTERLACED_REQUIRED')
    if not 0<width*height<=max_pixels:raise ValueError('ATP_PNG_PIXEL_BUDGET_HOLD')
    bpp=3 if color==2 else 4;stride=width*bpp;expected=height*(stride+1)
    dec=zlib.decompressobj();stream=dec.decompress(bytes(data),expected+1)
    if len(stream)!=expected or not dec.eof or dec.unused_data or dec.unconsumed_tail:
        raise ValueError('ATP_PNG_DECOMPRESSED_SIZE_HOLD')
    rows=[];previous=bytearray(stride)
    for y in range(height):
        start=y*(stride+1);mode=stream[start];row=bytearray(stream[start+1:start+1+stride])
        if mode not in range(5):raise ValueError('ATP_PNG_FILTER_HOLD')
        if mode:
            for x in range(stride):
                a=row[x-bpp] if x>=bpp else 0;b=previous[x];c=previous[x-bpp] if x>=bpp else 0
                if mode==1:predict=a
                elif mode==2:predict=b
                elif mode==3:predict=(a+b)//2
                else:
                    p=a+b-c;pa,pb,pc=abs(p-a),abs(p-b),abs(p-c)
                    predict=a if pa<=pb and pa<=pc else b if pb<=pc else c
                row[x]=(row[x]+predict)&255
        rows.append(row);previous=row
    return {'width':width,'height':height,'bpp':bpp,'rows':rows}


def pixel(image,x,y):
    # Pixel coordinates use the same bottom-left origin as Vision evidence.
    row=image['rows'][image['height']-1-y];start=x*image['bpp']
    return row[start:start+3]


def dark_symbol(image,cx,cy,halfwidth=10,halfheight=10):
    w,h=image['width'],image['height'];coords=[]
    for y in range(max(0,cy-halfheight),min(h,cy+halfheight+1)):
        for x in range(max(0,cx-halfwidth),min(w,cx+halfwidth+1)):
            rgb=pixel(image,x,y)
            if max(rgb)<220:coords.append((x,y))
    if not coords:return {'symbol':'UNKNOWN','reason':'NO_GLYPH_PIXELS'}
    xmin=min(x for x,y in coords);xmax=max(x for x,y in coords)
    ymin=min(y for x,y in coords);ymax=max(y for x,y in coords);bw=xmax-xmin+1;bh=ymax-ymin+1
    result={'pixel_bbox':[xmin,ymin,bw,bh],'dark_pixels':len(coords),'threshold_max_rgb_exclusive':220,'symbol':'UNKNOWN'}
    if bh<=4 and bw>=4:result['symbol']='ABSENT'
    elif 5<=bh<=18 and 5<=bw<=20 and .45<=bh/bw<=1.8:
        # Adjacent-pixel projection tolerates antialiased/slanted glyph strokes.
        # Both strokes must cross the middle; a T or X is not a plus symbol.
        middle_x=range(xmin+max(1,bw//4),xmax-max(1,bw//4)+1)
        middle_y=range(ymin+max(1,bh//4),ymax-max(1,bh//4)+1)
        horizontal=max((len({x for x,y in coords if abs(y-cy)<=1}) for cy in middle_y),default=0)
        vertical=max((len({y for x,y in coords if abs(x-cx)<=1}) for cx in middle_x),default=0)
        if horizontal>=bw*.8 and vertical>=bh*.8:result['symbol']='PRESENT'
    return result
