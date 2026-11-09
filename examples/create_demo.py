"""Create a two-second synthetic project; no personal media or network requests."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def create_demo(destination):
    destination = Path(destination).resolve()
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        'testsrc2=size=640x360:rate=30:duration=2', '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p', str(temp/'video.mp4')], check=True)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        'sine=frequency=440:sample_rate=48000:duration=2',
                        str(temp/'tone.wav')], check=True)
        subprocess.run([sys.executable, str(ROOT/'tools/dm_project.py'), 'new',
                        '--dest', str(destination), '--video', str(temp/'video.mp4'),
                        '--voice', str(temp/'tone.wav'), '--top-title', 'ทดสอบระบบภาพ',
                        '--bottom-banner', 'สื่อจำลองเท่านั้น'], check=True)
    timeline = json.loads((destination/'timeline.json').read_text())
    timeline['captions'] = [
        {'start': 0.1, 'end': 1.0, 'text': 'ทดสอบภาษาไทย',
         'highlights': [{'start': 0.2, 'end': 0.9, 'text': 'ภาษาไทย'}]},
        {'start': 1.0, 'end': 1.9, 'text': 'ภาพและเสียงจำลอง'}]
    (destination/'timeline.json').write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding='utf-8')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dest', default=str(ROOT/'.local/demo'))
    create_demo(parser.parse_args().dest)
