from .helper import unittest, PillowTestCase

try:
    from PIL import FpxImagePlugin, Image
except ImportError:
    olefile_installed = False
else:
    olefile_installed = True


@unittest.skipUnless(olefile_installed, "olefile package not installed")
class TestFileFpx(PillowTestCase):

    def test_invalid_file(self):
        # Test an invalid OLE file
        invalid_file = "Tests/images/flower.jpg"
        self.assertRaises(SyntaxError,
                          FpxImagePlugin.FpxImageFile, invalid_file)

        # Test a valid OLE file, but not an FPX file
        ole_file = "Tests/images/test-ole-file.doc"
        self.assertRaises(SyntaxError,
                          FpxImagePlugin.FpxImageFile, ole_file)

    def test_open_valid_tall_fpx(self):
        valid_file = "Tests/images/tall_example.fpx"
        reloaded = Image.open(valid_file)
        print(reloaded.tile)
        reloaded.load()
        reloaded.save("/tmp/tall_example.jpg", "JPEG")

    def test_open_valid_wide_fpx(self):
        valid_file = "Tests/images/wide_example.fpx"
        reloaded = Image.open(valid_file)
        data = reloaded.tile[-1]
        print(reloaded.size)
        # reloaded.size=(data[1][2], data[1][3])
        reloaded.load()
        reloaded.save("/tmp/wide_example.jpg", "JPEG")
