"""Regression checks for safe evaluation-bundle installation."""
import io,sys,tarfile,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from evaluation.bundle import install,digest

class BundleInstallationTests(unittest.TestCase):
    def test_checksum_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);archive=root/'input.tar.gz';archive.write_bytes(b'not an archive')
            with self.assertRaisesRegex(ValueError,'checksum'):install(archive,root/'destination','0'*64)
            self.assertFalse((root/'destination').exists())

    def test_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);archive=root/'input.tar.gz'
            with tarfile.open(archive,'w:gz') as tar:
                info=tarfile.TarInfo('observe-data/../../escape.txt');info.size=1;tar.addfile(info,io.BytesIO(b'x'))
            with self.assertRaisesRegex(ValueError,'Unsafe'):install(archive,root/'destination',digest(archive))
            self.assertFalse((root/'escape.txt').exists())

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);archive=root/'input.tar.gz'
            with tarfile.open(archive,'w:gz') as tar:
                info=tarfile.TarInfo('observe-data/link');info.type=tarfile.SYMTYPE;info.linkname='/tmp';tar.addfile(info)
            with self.assertRaisesRegex(ValueError,'Unsafe'):install(archive,root/'destination',digest(archive))

    def test_existing_bundle_never_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);archive=root/'input.tar.gz';archive.write_bytes(b'empty')
            (root/'observe-data').mkdir();marker=root/'observe-data/marker';marker.write_text('existing')
            with self.assertRaisesRegex(ValueError,'already exists'):install(archive,root,digest(archive))
            self.assertEqual(marker.read_text(),'existing')

if __name__=='__main__':unittest.main()
