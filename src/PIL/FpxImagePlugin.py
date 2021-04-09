#
# THIS IS WORK IN PROGRESS
#
# The Python Imaging Library.
# $Id$
#
# FlashPix support for PIL
#
# History:
# 97-01-25 fl   Created (reads uncompressed RGB images only)
#
# Copyright (c) Secret Labs AB 1997.
# Copyright (c) Fredrik Lundh 1997.
#
# See the README file for information on usage and redistribution.
#

from __future__ import print_function

from . import Image, ImageFile
from ._binary import i32le as i32, i8

import olefile

# __version__ is deprecated and will be removed in a future version. Use
# PIL.__version__ instead.
__version__ = "0.1"

# we map from colour field tuples to (mode, rawmode) descriptors
MODES = {
    # opacity
    (0x00007ffe): ("A", "L"),
    # monochrome
    (0x00010000,): ("L", "L"),
    (0x00018000, 0x00017ffe): ("RGBA", "LA"),
    # photo YCC
    (0x00020000, 0x00020001, 0x00020002): ("RGB", "YCC;P"),
    (0x00028000, 0x00028001, 0x00028002, 0x00027ffe): ("RGBA", "YCCA;P"),
    # standard RGB (NIFRGB)
    (0x00030000, 0x00030001, 0x00030002): ("RGB", "RGB"),
    (0x00038000, 0x00038001, 0x00038002, 0x00037ffe): ("RGBA", "RGBA"),
}


#
# --------------------------------------------------------------------

def _accept(prefix):
    return prefix[:8] == olefile.MAGIC


##
# Image plugin for the FlashPix images.

class FpxImageFile(ImageFile.ImageFile):

    format = "FPX"
    format_description = "FlashPix"

    def _open(self):
        #
        # read the OLE directory and see if this is a likely
        # to be a FlashPix file

        try:
            self.ole = olefile.OleFileIO(self.fp)
        except IOError:
            raise SyntaxError("not an FPX file; invalid OLE file")

        if self.ole.root.clsid != "56616700-C154-11CE-8553-00AA00A1F95B":
            raise SyntaxError("not an FPX file; bad root CLSID")

        self._open_index(1)

    def _open_index(self, index=1):
        #
        # get the Image Contents Property Set

        prop = self.ole.getproperties([
            "Data Object Store %06d" % index,
            "\005Image Contents"
        ])

        # size (highest resolution)

        self.reported_size = prop[0x1000002], prop[0x1000003]
        temp_size = self.reported_size
        # normalize size as FPX tiles are always a multiple of 64
        if temp_size[0] % 64 != 0:
            temp_size = (64 * (temp_size[0] / 64 + 1), temp_size[1],)
        if temp_size[1] % 64 != 0:
            temp_size = (temp_size[0], 64 * (temp_size[1] / 64 + 1), )
        self._size = temp_size


        size = max(self.size)
        i = 1
        while size >= 64:
            size = size / 2
            i += 1
        self.maxid = i - 1

        # mode.  instead of using a single field for this, flashpix
        # requires you to specify the mode for each channel in each
        # resolution subimage, and leaves it to the decoder to make
        # sure that they all match.  for now, we'll cheat and assume
        # that this is always the case.

        id = self.maxid << 16

        s = prop[0x2000002 | id]

        colors = []
        for i in range(i32(s, 4)):
            # note: for now, we ignore the "uncalibrated" flag
            colors.append(i32(s, 8+i*4) & 0x7fffffff)

        self.mode, self.rawmode = MODES[tuple(colors)]

        # load JPEG tables, if any
        self.jpeg = {}
        for i in range(256):
            id = 0x3000001 | (i << 16)
            if id in prop:
                self.jpeg[i] = prop[id]

        self._open_subimage(1, self.maxid)

    def _open_subimage(self, index=1, subimage=0):
        #
        # setup tile descriptors for a given subimage

        stream = [
            "Data Object Store %06d" % index,
            "Resolution %04d" % subimage,
            "Subimage 0000 Header"
        ]

        fp = self.ole.openstream(stream)

        # skip prefix
        fp.read(28)

        # header stream
        s = fp.read(36)

        size = i32(s, 4), i32(s, 8)
        tilecount = i32(s, 12)
        tilesize = i32(s, 16), i32(s, 20)
        channels = i32(s, 24)
        offset = i32(s, 28)
        length = i32(s, 32)

        if size != self.reported_size:
            raise IOError("subimage mismatch")

        # get tile descriptors
        fp.seek(28 + offset)
        s = fp.read(i32(s, 12) * length)

        x = y = 0
        xsize, ysize = size
        xtile, ytile = tilesize
        self.tile = []

        for i in range(0, len(s), length):

            compression = i32(s, i+8)

            if compression == 0:
                self.tile.append(("raw", (x, y, x+xtile, y+ytile),
                                 i32(s, i) + 28, (self.rawmode)))

            elif compression == 1:
                single_color = bytes(s[i + 12:i + 16])
                self.tile.append(("fill", (x, y, x+xtile, y+ytile),
                                 i32(s, i), (self.rawmode, single_color)))

            elif compression == 2:
                internal_color_conversion = i8(s[14])
                jpeg_tables = i8(s[15])
                rawmode = self.rawmode

                if internal_color_conversion and rawmode == "RGBA":
                    # For "RGBA", data is stored as YCbCrA based on
                    # negative RGB. The following trick works around
                    # this problem :
                    jpegmode, rawmode = "YCbCrK", "CMYK"
                else:
                    # Trust the decoder
                    jpegmode = None

                self.tile.append(("jpeg", (x, y, x+xtile, y+ytile),
                                 i32(s, i) + 28, (rawmode, jpegmode)))

                # FIXME: jpeg tables are tile dependent; the prefix
                # data must be placed in the tile descriptor itself!

                if jpeg_tables:
                    self.tile_prefix = self.jpeg[jpeg_tables]
                else:  # Default to the first quant table if it's not specified
                    self.tile_prefix = self.jpeg[1]

            else:
                raise IOError("unknown/invalid compression")

            x = x + xtile
            if x >= xsize:
                x, y = 0, y + ytile
                if y >= ysize:
                    break  # isn't really required

        self.stream = stream
        self.fp = None

    def load(self):

        if not self.fp:
            self.fp = self.ole.openstream(self.stream[:2] +
                                          ["Subimage 0000 Data"])

        return ImageFile.ImageFile.load(self)

    def load_end(self):
        # Reset the image size after the decoding so we ignore the padding from the partial tiles.
        self._size = self.reported_size

    def crop(self, box=None):
        width = box[2]
        height = box[3]

        width = min(self.reported_size[0], width)
        height = min(self.reported_size[1], height)
        img = ImageFile.ImageFile.crop(self,(box[0],box[1],width,height))
        return img

    def resize(self, size, resample=0, box=None):
        height_ratio = float(size[1]) / self.reported_size[1]
        height_rem = 64 - self.reported_size[1] % 64

        width_ratio = float(size[0]) / self.reported_size[0]
        width_rem = 64 - self.reported_size[0] % 64

        nsize=(int(size[0]+width_rem*width_ratio), int(size[1]+height_rem*height_ratio))
        img=ImageFile.ImageFile.resize(self, nsize, resample=resample, box=box)
        img._size = (size[0], size[1])
        return img
#
# --------------------------------------------------------------------


class FillDecoder(ImageFile.PyDecoder):
    pulls_fd = True

    def __init__(self, mode, *args):
        super(FillDecoder, self).__init__(mode, *args)

    def is_monochrome(self, im):
        return im.mode == 'L'

    def decode(self, buffer):
        bounds = [self.state.xoff, self.state.yoff, self.state.xoff+self.state.xsize, self.state.yoff+self.state.ysize]
        offset = 0 if self.mode == "RGB" else 1
        rgb = self.args[1]
        rouge = i8(rgb[offset])
        vert = i8(rgb[offset + 1])
        bleu = i8(rgb[offset + 2])
        if self.is_monochrome(self.im):
            self.im.paste((ord(self.args[1][0]),), bounds)
        else:
            self.im.paste((rouge, vert, bleu), bounds)

        return len(buffer), 0


Image.register_decoder('fill', FillDecoder)

Image.register_open(FpxImageFile.format, FpxImageFile, _accept)

Image.register_extension(FpxImageFile.format, ".fpx")
