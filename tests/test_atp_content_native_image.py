"""Optional native read-only decoder coordinate test; no author image changes."""
import base64
import json
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path
import r031_evidence.atp_plot_pixels as pixels


class NativeImage(unittest.TestCase):
    @unittest.skipUnless(shutil.which('swift'),'optional macOS image reader unavailable')
    def test_original_pixel_orientation_agrees_with_png_and_vision_coordinates(self):
        def chunk(k,b):return struct.pack('>I',len(b))+k+b+struct.pack('>I',zlib.crc32(k+b)&0xffffffff)
        # Original synthetic 2x2: red/green at top, blue/white at bottom.
        raw=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2,2,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0\xff\0\0\0\xff\0\0\0\0\xff\xff\xff\xff'))+chunk(b'IEND',b'')
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'test.png';p.write_bytes(raw)
            run=subprocess.run([shutil.which('swift'),str(Path(pixels.__file__).parent/'data/atp_read_image.swift'),str(p)],capture_output=True,text=True,timeout=30,check=True)
            native=json.loads(run.stdout);rgba=base64.b64decode(native['rgba_base64'])
            image={**native,'rows':[rgba[:8],rgba[8:]]}
            expected=pixels.read_png(raw)
            for x in range(2):
                for y in range(2):self.assertEqual(bytes(pixels.pixel(image,x,y)),bytes(pixels.pixel(expected,x,y)))
            self.assertEqual(p.read_bytes(),raw);self.assertFalse(native['source_modified'])
