import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


r = module('renderer', ROOT/'project/current/render_video.py')
demo = module('demo', ROOT/'examples/create_demo.py')


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.ep = demo.create_demo(Path(cls.temp.name)/'episode')
        cls.config = json.loads((cls.ep/'project.json').read_text())
        cls.timeline = json.loads((cls.ep/'timeline.json').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        (self.ep/'project.json').write_text(json.dumps(self.config))
        (self.ep/'timeline.json').write_text(json.dumps(self.timeline))
        r.configure_root(self.ep)
        r.FONT = self.ep/self.config['font']
        r.BRAND_IMAGE = self.ep/self.config['brand']
        r.MUSIC_MEDIA = None

    def report(self, value):
        return r.validate_timeline(r.normalize_timeline(copy.deepcopy(value)), self.ep/self.config['voice'])

    def cli(self, command, *flags):
        return subprocess.run([sys.executable, str(ROOT/'tools/dm_project.py'), command,
                               str(self.ep), *flags], capture_output=True, text=True)

    def test_good_and_legacy_aliases(self):
        self.assertTrue(self.report(self.timeline)['ok'])
        doc = copy.deepcopy(self.timeline)
        doc['target_duration'] = doc.pop('duration')
        shot = doc['shots'][0]
        shot['speed'] = shot.pop('playback_speed')
        highlight = doc['captions'][0]['highlights'][0]
        highlight['word'] = highlight.pop('text')
        self.assertTrue(self.report(doc)['ok'])

    def test_invalid_data(self):
        cases = []
        def case(fn):
            doc = copy.deepcopy(self.timeline)
            fn(doc)
            cases.append(doc)
        case(lambda d: d.update(target_duration=3))
        case(lambda d: d.update(shots='bad'))
        case(lambda d: d.update(captions={}))
        case(lambda d: d.update(trim_voice_silences=True))
        for key, value in [('start','bad'),('source_in',None),('duration',float('nan')),
                           ('frames',59),('playback_speed',2),('crop',[0,0,9000,360]),
                           ('crop',[1]),('source_in',-1),('source_out',4),('id','../escape'),
                           ('source',{}),('end',3)]:
            case(lambda d,k=key,v=value: d['shots'][0].update({k:v}))
        case(lambda d: d['shots'][0].pop('source_in'))
        case(lambda d: d['shots'].append(copy.deepcopy(d['shots'][0])))
        case(lambda d: d['captions'][0].update(end=3))
        case(lambda d: d['captions'][1].update(start=0.5))
        case(lambda d: d['captions'][0].update(start=-1))
        case(lambda d: d['captions'][0].update(highlights=[{'text':'missing','start':0.2}]))
        case(lambda d: d['captions'][0].update(highlights=[{'text':'ภาษาไทย','start':9}]))
        for doc in [None, [], {}, *cases]:
            with self.subTest(doc=doc):
                self.assertFalse(self.report(doc)['ok'])

    def test_missing_assets_report_and_exit(self):
        for field, missing in [('font','inputs/missing.ttf'),('brand','work/missing.png'),
                               ('voice','inputs/missing.wav'),('approved_audio','inputs/missing.wav'),
                               ('music','inputs/missing.wav')]:
            with self.subTest(field=field):
                config = {**self.config, field:missing}
                (self.ep/'project.json').write_text(json.dumps(config))
                result = self.cli('validate')
                self.assertEqual(result.returncode, 2, result.stderr+result.stdout)
                qa = json.loads((self.ep/'outputs/QA_preflight.json').read_text())
                self.assertFalse(qa['ok'])
                self.assertTrue(qa['errors'])
                self.assertNotIn('Traceback', result.stderr)

    def test_approved_audio_is_validated_instead_of_unused_voice(self):
        config = {**self.config, 'approved_audio': self.config['voice'], 'voice':'inputs/unused.wav'}
        (self.ep/'project.json').write_text(json.dumps(config))
        self.assertEqual(self.cli('validate').returncode,0)
        config['approved_audio'] = self.config['source']
        (self.ep/'project.json').write_text(json.dumps(config))
        self.assertEqual(self.cli('validate').returncode,2)
        self.assertIn('no audio stream',str(json.loads((self.ep/'outputs/QA_preflight.json').read_text())['errors']))

    def test_style_warning_is_not_structural_error(self):
        doc=copy.deepcopy(self.timeline)
        doc['captions'][0]['text']='ทดสอบภาษาไทยและข้อความยาวเพื่ออ่านบนหน้าจอ'
        result=self.report(doc)
        self.assertTrue(result['ok'])
        self.assertTrue(result['warnings'])

    def test_invalid_json_and_type_have_report(self):
        for value in ['{', '[]', '{"shots":[null]}']:
            (self.ep/'timeline.json').write_text(value)
            result = self.cli('validate')
            self.assertEqual(result.returncode, 2)
            self.assertFalse(json.loads((self.ep/'outputs/QA_preflight.json').read_text())['ok'])
            self.assertNotIn('Traceback', result.stderr)

    def test_overwrite_failure_preserves_deliverables(self):
        final = self.ep/'outputs'/self.config['output_name']
        unrelated = self.ep/'outputs/other_1080p.mp4'
        subtitle = self.ep/'outputs/MathTheWhat_Thai.ass'
        for p in (final, unrelated, subtitle):
            p.write_bytes(b'previous result')
        doc = copy.deepcopy(self.timeline)
        doc['shots'][0]['frames'] = 999
        (self.ep/'timeline.json').write_text(json.dumps(doc))
        self.assertEqual(self.cli('render', '--overwrite').returncode, 2)
        (self.ep/'timeline.json').write_text(json.dumps(self.timeline))
        with patch.object(r,'render',side_effect=RuntimeError('simulated FFmpeg failure')):
            self.assertEqual(r.main(['--root',str(self.ep),'--overwrite']),1)
        for p in (final, unrelated, subtitle):
            self.assertEqual(p.read_bytes(),b'previous result')
        self.assertEqual(list((self.ep/'outputs').glob('stage_*')),[])

    def test_real_render_and_zip(self):
        result = self.cli('validate')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        result = self.cli('render','--overwrite','--always-zip')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        final = self.ep/'outputs'/self.config['output_name']
        info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(final)]))
        video=next(s for s in info['streams'] if s['codec_type']=='video')
        self.assertEqual((video['width'],video['height'],video['r_frame_rate']),(1080,1920,'30/1'))
        self.assertEqual(int(video['nb_frames']),60)
        self.assertTrue(any(s['codec_type']=='audio' for s in info['streams']))
        self.assertAlmostEqual(float(info['format']['duration']),2,delta=.05)
        ass=(self.ep/'outputs/MathTheWhat_Thai.ass').read_text()
        self.assertFalse(any(ord(c)<32 and c not in '\n\r\t' for c in ass))
        self.assertIn('ภาษาไทย',ass)
        self.assertTrue(final.with_suffix('.zip').exists())
        self.assertEqual(self.cli('render').returncode,2)


if __name__ == '__main__':
    unittest.main()
