import pathlib
import tempfile
import unittest
import yaml
from config_transfer import export_config, import_config

class TransferTests(unittest.TestCase):
    def test_manual_copy_is_self_contained_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            for name,data in {'voice.yaml':{'wake_word':'махи','aliases':{'демо':'demo'}},'actions.yaml':{'version':1,'actions':{}},'weather.yaml':{'latitude':1,'longitude':2}}.items():
                (root/name).write_text(yaml.safe_dump(data))
            (root/'.env').write_text('SECRET=do-not-export')
            bundle=export_config(root)
            self.assertNotIn('SECRET',str(bundle));self.assertNotIn('.env',bundle['files'])
            inode=(root/'voice.yaml').stat().st_ino
            import_config(root,bundle)
            self.assertEqual(inode,(root/'voice.yaml').stat().st_ino)
            before=(root/'voice.yaml').read_text()
            bundle['files']['voice.yaml']='wake_word: ""'
            with self.assertRaises(ValueError):import_config(root,bundle)
            self.assertEqual(before,(root/'voice.yaml').read_text())
