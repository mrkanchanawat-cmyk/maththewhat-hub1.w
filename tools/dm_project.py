#!/usr/bin/env python3
"""ArtMath offline project CLI. See README.md for setup and supported commands."""
from __future__ import annotations

import argparse
import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_FONT_Candidates = [
    ROOT_DIR / 'assets' / 'fonts' / 'NotoSansThai-ExtraBold.ttf',
    Path('/usr/share/fonts/truetype/noto/NotoSansThai-ExtraBold.ttf'),
    Path('/usr/share/fonts/truetype/noto/NotoSansThai-Bold.ttf'),
    Path('C:/Windows/Fonts/leelawdb.ttf'),
    Path('C:/Windows/Fonts/tahoma.ttf'),
]

def _find_bin(name: str) -> str:
    return shutil.which(name) or name


FFMPEG_BIN = _find_bin('ffmpeg')
FFPROBE_BIN = _find_bin('ffprobe')


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def find_font(custom: str | None = None) -> Path:
    if custom:
        p = Path(custom)
        if p.exists():
            return p
    for cand in DEFAULT_FONT_Candidates:
        if cand.exists():
            return cand
    raise FileNotFoundError('Could not locate a Thai-capable font. Put NotoSansThai-ExtraBold.ttf in assets/fonts/.')


def verify_manifest(root: Path) -> int:
    manifest_path = root / 'MANIFEST.json'
    if not manifest_path.exists():
        print(f'[ERROR] Missing MANIFEST.json in {root}')
        return 2
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    missing = []
    size_mismatch = []
    hash_mismatch = []
    checked = 0
    for item in manifest.get('files', []):
        rel = item['path']
        p = root / rel
        if not p.exists():
            missing.append(rel)
            continue
        checked += 1
        sz = p.stat().st_size
        if sz != item['bytes']:
            size_mismatch.append((rel, item['bytes'], sz))
        digest = sha256_file(p)
        if digest != item['sha256']:
            hash_mismatch.append((rel, item['sha256'], digest))
    print(f'[VERIFY] root={root}')
    print(f'[VERIFY] checked={checked} missing={len(missing)} size_mismatch={len(size_mismatch)} hash_mismatch={len(hash_mismatch)}')
    if missing:
        print('Missing files:')
        for rel in missing:
            print(' -', rel)
    if size_mismatch:
        print('Size mismatches:')
        for rel, exp, got in size_mismatch:
            print(f' - {rel}: expected {exp}, got {got}')
    if hash_mismatch:
        print('SHA256 mismatches:')
        for rel, exp, got in hash_mismatch:
            print(f' - {rel}: expected {exp[:12]}..., got {got[:12]}...')
    return 0 if not (missing or size_mismatch or hash_mismatch) else 1


def trim_silence(
    inp: Path,
    out: Path,
    threshold_db: int = -38,
    min_silence: float = 0.16,
    leave_silence: float = 0.05,
    bitrate: str = '192k',
) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    af = (
        f'silenceremove=start_periods=1:start_duration=0.03:start_threshold={threshold_db}dB:'
        f'stop_periods=-1:stop_duration={min_silence}:stop_threshold={threshold_db}dB:'
        f'stop_silence={leave_silence}'
    )
    cmd = [
        FFMPEG_BIN, '-y', '-i', str(inp),
        '-af', af,
        '-c:a', 'libmp3lame', '-b:a', bitrate,
        str(out),
    ]
    subprocess.run(cmd, check=True)
    dur_in = probe_duration(inp)
    dur_out = probe_duration(out)
    print(f'[TRIM] input={inp.name} ({dur_in:.2f}s) -> output={out.name} ({dur_out:.2f}s)')
    print('[TRIM] IMPORTANT: Do NOT enable trim_voice_silences in timeline.json when using a pre-trimmed voice file!')
    return 0


def probe_duration(path: Path) -> float:
    out = subprocess.check_output([
        FFPROBE_BIN, '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', str(path)
    ], text=True).strip()
    return float(out or 0.0)


def build_brand_frame(out, top_title, bottom_banner, channel_handle='@MathTheWhat',
                      font_path=None, canva_banner_path=None, rainbow_border=False,
                      bottom_sub_banner=None, highlight='', mode='white_red'):
    # One layout implementation for both CLI entrypoints: center window y=332..1596.
    spec=importlib.util.spec_from_file_location('artmath_renderer',_find_renderer())
    renderer=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    renderer.FONT=find_font(str(font_path) if font_path else None)
    renderer.generate_brand_frame(top_title,red_highlight=highlight,out_path=out,
                                  channel_name=channel_handle.lstrip('@'),sub_tagline=bottom_banner,
                                  top_color_mode=mode,canva_banner_path=canva_banner_path,
                                  rainbow_border=rainbow_border,bottom_sub_banner=bottom_sub_banner or '')
    print(f'[BRAND_FRAME] saved {out}')
    return 0


def new_project(dest, video_source, voice_source, top_title, bottom_banner, channel_handle, bgm_source=None):
    """Create an editable, valid initial timeline; the first source interval is only a placeholder."""
    dest=dest.resolve()
    if dest.exists() and any(dest.iterdir()):
        raise ValueError('Destination must be new or empty; existing projects are never overwritten')
    font=find_font()
    duration=round(min(probe_duration(video_source),probe_duration(voice_source))*30)/30
    if duration <= 0:
        raise ValueError('Video and voice must have positive duration')
    for directory in ('inputs/fonts','work','outputs'):
        (dest/directory).mkdir(parents=True,exist_ok=True)
    video=dest/'inputs'/('video'+video_source.suffix)
    voice=dest/'inputs'/('narration'+voice_source.suffix)
    shutil.copy2(video_source,video)
    shutil.copy2(voice_source,voice)
    shutil.copy2(font,dest/'inputs/fonts/NotoSansThai-ExtraBold.ttf')
    music=None
    if bgm_source:
        music=dest/'inputs'/('music'+bgm_source.suffix)
        shutil.copy2(bgm_source,music)
    shots=[]
    frames=round(duration*30)
    for index,start_frame in enumerate(range(0,frames,60)):
        end_frame=min(start_frame+60,frames)
        shots.append({'id':f'S{index+1:02d}','source':video.relative_to(dest).as_posix(),
                      'start':start_frame/30,'end':end_frame/30,'source_in':start_frame/30,
                      'source_out':end_frame/30,'playback_speed':1.0,'crop_mode':'cover'})
    timeline={'duration':duration,'shots':shots,'captions':[], 'trim_voice_silences':False}
    config={'source':video.relative_to(dest).as_posix(),'voice':voice.relative_to(dest).as_posix(),
            'music':music.relative_to(dest).as_posix() if music else None,'font':'inputs/fonts/NotoSansThai-ExtraBold.ttf',
            'brand':'work/brand_frame.png','timeline':'timeline.json','output_name':'ArtMath_Final_1080p.mp4','title':top_title}
    for name,value in [('project.json',config),('timeline.json',timeline)]:
        (dest/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    build_brand_frame(dest/'work/brand_frame.png',top_title,bottom_banner,channel_handle,font)
    print(f'[NEW_PROJECT] created {dest}; replace placeholder shots and add timed captions')
    return 0


def package_kit(root: Path, zip_name: str = 'ArtMath_Code.zip') -> int:
    """Package only Git-tracked source files; generated/private assets stay local."""
    result=subprocess.run(['git','ls-files','-z'],cwd=root,check=True,capture_output=True)
    paths=[root/name for name in result.stdout.decode().split('\0') if name]
    with zipfile.ZipFile(root/zip_name,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            if path.is_file():
                archive.write(path,path.relative_to(root).as_posix())
    return 0


def zip_if_over_mb(target: Path, max_mb: float = 50.0, force: bool = False) -> int:
    """Check if a file or directory exceeds `max_mb` (default 50 MB) and compress it into a .zip archive."""
    target = target.resolve()
    if not target.exists():
        raise FileNotFoundError(f'Target not found: {target}')
    if target.is_file():
        size_mb = target.stat().st_size / (1024 * 1024)
        if size_mb <= max_mb and not force:
            print(f'[ZIP-CHECK] {target.name} is {size_mb:.2f} MB (<= {max_mb:.1f} MB) — no zip needed.')
            return 0
        zip_path = target.with_suffix('.zip')
        with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            zf.write(target, target.name)
        zip_mb = zip_path.stat().st_size / (1024 * 1024)
        print(f'[ZIP-CREATED] {target.name} ({size_mb:.2f} MB) -> {zip_path.name} ({zip_mb:.2f} MB)')
        return 0
    # Directory mode
    files = [p for p in sorted(target.rglob('*')) if p.is_file() and p.suffix.lower() != '.zip']
    total_mb = sum(p.stat().st_size for p in files) / (1024 * 1024)
    if total_mb <= max_mb and not force:
        print(f'[ZIP-CHECK] folder {target.name} is {total_mb:.2f} MB (<= {max_mb:.1f} MB) — no zip needed.')
        return 0
    zip_path = target.parent / f'{target.name}.zip'
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in files:
            zf.write(p, p.relative_to(target).as_posix())
    zip_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f'[ZIP-CREATED] folder {target.name} ({total_mb:.2f} MB) -> {zip_path.name} ({zip_mb:.2f} MB)')
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='ArtMath / NNNKx2 project utility v3.1')
    sub = p.add_subparsers(dest='cmd', required=True)

    p_ver = sub.add_parser('verify', help='Verify MANIFEST.json hashes and sizes')
    p_ver.add_argument('root', nargs='?', default=str(ROOT_DIR))
    p_ver.add_argument('--allow-missing-video', action='store_true', help='Skip missing MP4/MKV/AVI')

    # trim-silence: accept BOTH positional and --in/--out forms so README examples and NEXT_CHAT_PROMPT match
    p_trim = sub.add_parser('trim-silence', help='Trim silence from narration audio using proven settings')
    p_trim.add_argument('input_pos', nargs='?', default=None, help='Input audio (positional form)')
    p_trim.add_argument('output_pos', nargs='?', default=None, help='Output audio (positional form)')
    p_trim.add_argument('--in', dest='inp', default=None)
    p_trim.add_argument('--out', dest='out', default=None)
    p_trim.add_argument('--threshold-db', type=int, default=-38)
    p_trim.add_argument('--min-silence', '--max-pause', type=float, default=0.16)
    p_trim.add_argument('--leave-silence', type=float, default=0.05)
    p_trim.add_argument('--bitrate', default='192k')

    p_bf = sub.add_parser('brand-frame', help='Generate top/bottom NNNKx2 brand overlay PNG')
    p_bf.add_argument('episode_dir', nargs='?', default=None, help='Episode folder (shorthand: writes <dir>/work/brand_frame.png)')
    p_bf.add_argument('--out', default=None)
    p_bf.add_argument('--top-title', '--top', default=None)
    p_bf.add_argument('--bottom-banner', default='ยังเอาไม่อยู่!')
    p_bf.add_argument('--highlight', default=None, help='Substring to highlight in red')
    p_bf.add_argument('--channel-handle', default='@MathTheWhat')
    p_bf.add_argument('--font', default=None)
    p_bf.add_argument('--canva-banner', default=None, help='Optional transparent PNG exported from Canva')
    p_bf.add_argument('--rainbow-border', action='store_true', help='Draw RGB neon border around middle frame')
    p_bf.add_argument('--sub-banner', default=None, help='Optional white secondary pill below main bottom banner')
    p_bf.add_argument('--mode', default='white_red', choices=['white_red', 'red', 'gold', 'white_gold'])

    p_val = sub.add_parser('validate', help='Run preflight checks on an episode folder (calls render_video.py --validate-only)')
    p_val.add_argument('episode_dir', help='Episode folder containing project.json')
    p_val.add_argument('--project', default='project.json')
    p_val.add_argument('--timeline', default=None)

    p_ren = sub.add_parser('render', help='Render an episode (calls render_video.py)')
    p_ren.add_argument('episode_dir', help='Episode folder containing project.json')
    p_ren.add_argument('--overwrite', action='store_true', help='Replace target output only after successful rendering')
    p_ren.add_argument('--project', default='project.json')
    p_ren.add_argument('--timeline', default=None)
    p_ren.add_argument('--force', action='store_true', help='Deprecated; invalid inputs still stop rendering')
    p_ren.add_argument('--max-mb', type=float, default=50.0, help='Auto-zip output if file size exceeds this MB limit (default: 50.0)')
    p_ren.add_argument('--always-zip', action='store_true', help='Always create a .zip bundle of the final output')

    p_zip = sub.add_parser('zip-if-large', help='Check file/folder size and compress into .zip if over 50MB')
    p_zip.add_argument('target', help='File or directory path to check/zip')
    p_zip.add_argument('--max-mb', type=float, default=50.0, help='Size threshold in MB (default: 50.0)')
    p_zip.add_argument('--force', action='store_true', help='Compress to .zip even if under threshold')

    p_np = sub.add_parser('new-project', aliases=['new'], help='Bootstrap a new episode project folder')
    p_np.add_argument('--dest', required=True)
    p_np.add_argument('--video', required=True)
    p_np.add_argument('--voice', required=True)
    p_np.add_argument('--bgm', default=None)
    p_np.add_argument('--top-title', '--title', required=True)
    p_np.add_argument('--bottom-banner', '--top-banner', default='ยังเอาไม่อยู่!')
    p_np.add_argument('--highlight', default=None)
    p_np.add_argument('--channel-handle', default='@MathTheWhat')

    p_pkg = sub.add_parser('package', help='Package Git-tracked source files only')
    p_pkg.add_argument('--root', default=str(ROOT_DIR))
    p_pkg.add_argument('--zip-name', default='ArtMath_Code.zip')

    return p


def _find_renderer() -> Path:
    """Locate render_video.py (shipped in project/current/ or beside this script)."""
    candidates = [
        ROOT_DIR / 'project' / 'current' / 'render_video.py',
        Path(__file__).resolve().parent / 'render_video.py',
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError('render_video.py not found — expected in project/current/')


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.cmd == 'verify':
        return verify_manifest(Path(args.root).resolve())

    if args.cmd == 'trim-silence':
        inp = Path(args.inp or args.input_pos or '').resolve()
        out = Path(args.out or args.output_pos or '').resolve()
        if not inp.is_file():
            raise FileNotFoundError(f'Input not found: {inp}')
        return trim_silence(inp, out, args.threshold_db, args.min_silence, args.leave_silence, args.bitrate)

    if args.cmd == 'brand-frame':
        out_path = Path(args.out).resolve() if args.out else (Path(args.episode_dir).resolve() / 'work' / 'brand_frame.png' if args.episode_dir else None)
        if not out_path:
            raise ValueError('Provide --out or an episode_dir positional argument')
        title = args.top_title
        if not title:
            raise ValueError('Provide --top-title')
        return build_brand_frame(
            out_path,
            title,
            args.bottom_banner,
            args.channel_handle,
            Path(args.font).resolve() if args.font else None,
            Path(args.canva_banner).resolve() if args.canva_banner else None,
            args.rainbow_border,
            args.sub_banner,
            args.highlight or "",
            args.mode,
        )

    if args.cmd == 'validate':
        renderer = _find_renderer()
        cmd = [sys.executable, str(renderer), '--root', str(Path(args.episode_dir).resolve()), '--validate-only']
        if args.project != 'project.json':
            cmd += ['--project', args.project]
        if args.timeline:
            cmd += ['--timeline', args.timeline]
        return subprocess.call(cmd)

    if args.cmd == 'render':
        renderer = _find_renderer()
        ep = Path(args.episode_dir).resolve()
        cmd = [sys.executable, str(renderer), '--root', str(ep)]
        if args.overwrite:
            cmd.append('--overwrite')
        if args.project != 'project.json':
            cmd += ['--project', args.project]
        if args.timeline:
            cmd += ['--timeline', args.timeline]
        if args.force:
            cmd.append('--force')
        if args.max_mb != 50.0:
            cmd += ['--max-mb', str(args.max_mb)]
        if args.always_zip:
            cmd.append('--always-zip')
        return subprocess.call(cmd)

    if args.cmd == 'zip-if-large':
        return zip_if_over_mb(Path(args.target), args.max_mb, args.force)

    if args.cmd in ('new-project', 'new'):
        return new_project(
            Path(args.dest),
            Path(args.video).resolve(),
            Path(args.voice).resolve(),
            args.top_title,
            args.bottom_banner,
            args.channel_handle,
            Path(args.bgm).resolve() if args.bgm else None,
        )

    if args.cmd == 'package':
        return package_kit(Path(args.root).resolve(), args.zip_name)

    raise ValueError(f'Unknown subcommand: {args.cmd}')


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'[ERROR] {exc}', file=sys.stderr)
        raise SystemExit(2)
