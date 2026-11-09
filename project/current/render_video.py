#!/usr/bin/env python3
"""ArtMath offline FFmpeg renderer. See README.md for the validated CLI contract."""
from pathlib import Path
import argparse
import concurrent.futures
import json
import math
import os
import re
import shutil
import subprocess
import unicodedata
import zipfile
import tempfile
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
WORK = ROOT / 'work'
OUT = ROOT / 'outputs'
INPUT = ROOT / 'inputs'
FPS = 30
W, H, MH = 1080, 1920, 1264
FONT = INPUT / 'fonts/NotoSansThai-ExtraBold.ttf'
SOURCE_MEDIA = INPUT / 'video.mp4'
VOICE_MEDIA = INPUT / 'narration.wav'
MUSIC_MEDIA = INPUT / 'music.wav'
APPROVED_AUDIO_MEDIA = None
BRAND_IMAGE = WORK / 'brand_frame.png'
FINAL_NAME = 'ArtMath_Final_1080p.mp4'
TITLE = 'ArtMath'
KIT_FONT = FONT  # fallback font that always ships next to this script

# NNNKx2 DNA pacing / language rules used by the preflight validator
MAX_SHOT_SECONDS = 2.2
AVG_SHOT_RANGE = (1.25, 1.80)
MALE_PARTICLES_ERROR = ('ครับ',)
MALE_PARTICLES_WARN = ('ผม',)  # warning only: "ผม" can also mean "hair" (e.g. ผมทอง)


def configure_root(root):
    """Point the renderer at another episode folder (same layout: inputs/, work/, outputs/, project.json)."""
    global ROOT, WORK, OUT, INPUT, FONT, SOURCE_MEDIA, BRAND_IMAGE
    ROOT = Path(root).resolve()
    WORK = ROOT / 'work'
    OUT = ROOT / 'outputs'
    INPUT = ROOT / 'inputs'
    FONT = INPUT / 'fonts/NotoSansThai-ExtraBold.ttf'
    if not FONT.is_file():
        FONT = KIT_FONT
    SOURCE_MEDIA = INPUT / 'video.mp4'
    BRAND_IMAGE = WORK / 'brand_frame.png'


def _find_bin(name):
    found = shutil.which(name)
    if found:
        return found
    return name


FFMPEG_BIN = _find_bin('ffmpeg')
FFPROBE_BIN = _find_bin('ffprobe')


def run(args):
    cmd = list(args)
    if cmd and cmd[0] == 'ffmpeg':
        cmd[0] = FFMPEG_BIN
    elif cmd and cmd[0] == 'ffprobe':
        cmd[0] = FFPROBE_BIN
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=ROOT)
    if p.returncode:
        raise RuntimeError(' '.join(map(str, cmd[:8])) + '\n' + p.stderr[-5000:])
    return p.stdout


def clean(s):
    return ''.join(c for c in unicodedata.normalize('NFC', s) if c.isalnum() or unicodedata.category(c).startswith('M'))


def time_ass(t):
    c = round(t * 100)
    return f'{c//360000}:{c//6000%60:02d}:{c//100%60:02d}.{c%100:02d}'


def time_srt(t):
    m = round(t * 1000)
    return f'{m//3600000:02d}:{m//60000%60:02d}:{m//1000%60:02d},{m%1000:03d}'


_PROBE_CACHE = {}


def probe_media(path):
    """Return {'duration', 'width', 'height'} for a media file (cached). Missing file -> None."""
    path = Path(path)
    if not path.is_file():
        return None
    key = (str(path.resolve()), path.stat().st_mtime_ns, path.stat().st_size)
    if key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    if not path.is_file():
        return None
    data = json.loads(run([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_type,width,height',
        '-of', 'json', str(path)
    ]))
    video = next((s for s in data.get('streams', []) if s.get('codec_type') == 'video'), {})
    info = {
        'duration': float(data.get('format', {}).get('duration') or 0.0),
        'width': int(video.get('width') or 0),
        'height': int(video.get('height') or 0),
        'has_audio': any(v.get('codec_type') == 'audio' for v in data.get('streams', [])),
    }
    _PROBE_CACHE[key] = info
    return info


def _shot_source(shot):
    return (ROOT / shot['source']).resolve() if shot.get('source') else Path(SOURCE_MEDIA).resolve()


def _hl_text(h):
    return str(h.get('text', h.get('word', '')))


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def normalize_timeline(doc):
    """Derive missing fields only; validation never silently repairs conflicting input."""
    if not isinstance(doc, dict):
        return doc
    if 'duration' not in doc and 'target_duration' in doc:
        doc['duration'] = doc['target_duration']
    shots = doc.get('shots', [])
    if not isinstance(shots, list):
        return doc
    clock = 0.0
    for i, shot in enumerate(shots):
        if not isinstance(shot, dict):
            continue
        shot.setdefault('id', f'S{i+1:02d}')
        shot.setdefault('start', clock)
        if 'speed' in shot:
            shot.setdefault('playback_speed', shot['speed'])
        if number(shot.get('start')) and number(shot.get('duration')):
            shot.setdefault('end', shot['start'] + shot['duration'])
        if number(shot.get('start')) and number(shot.get('end')):
            shot.setdefault('duration', shot['end'] - shot['start'])
            shot.setdefault('frames', round(shot['end'] * FPS) - round(shot['start'] * FPS))
            clock = shot['end']
        if number(shot.get('source_in')) and number(shot.get('duration')):
            speed = shot.get('playback_speed', 1.0)
            if number(speed):
                shot.setdefault('source_out', shot['source_in'] + shot['duration'] * speed)
                if shot['duration'] > 0 and number(shot.get('source_out')):
                    shot.setdefault('playback_speed', (shot['source_out'] - shot['source_in']) / shot['duration'])
        shot.setdefault('crop_mode', 'cover')
    if 'duration' not in doc and shots:
        doc['duration'] = clock
    caps = doc.get('captions', [])
    if isinstance(caps, list):
        for cap in caps:
            if not isinstance(cap, dict):
                continue
            cap.setdefault('highlights', [])
            if isinstance(cap['highlights'], list):
                for h in cap['highlights']:
                    if isinstance(h, dict):
                        if 'word' in h:
                            h.setdefault('text', h['word'])
                        h.setdefault('start', cap.get('start'))
    return doc


def caption_segments(text, highlights):
    """Locate each highlight inside the caption (in order) keeping original spacing.

    Returns (segments, mode, missing):
      segments = list of (substring, highlight_index or None)
      mode     = 'partition' when highlights cover every non-space character (karaoke flip), else 'keyword'
      missing  = highlight texts that were not found in order inside the caption
    """
    segments, missing, cursor = [], [], 0
    for j, h in enumerate(highlights):
        word = _hl_text(h)
        if not word:
            continue
        pos = text.find(word, cursor)
        if pos < 0:
            missing.append(word)
            continue
        if pos > cursor:
            segments.append((text[cursor:pos], None))
        segments.append((word, j))
        cursor = pos + len(word)
    if cursor < len(text):
        segments.append((text[cursor:], None))
    plain = ''.join(t for t, idx in segments if idx is None)
    mode = 'partition' if highlights and not missing and not plain.strip() else 'keyword'
    return segments, mode, missing


def ass_escape(s):
    return s.replace('\\', '＼').replace('{', '（').replace('}', '）')


def validate_timeline(doc, voice_path=None, approved_audio=None):
    errors, warnings, stats = [], [], {}
    def report():
        return {'ok': not errors, 'errors': errors, 'warnings': warnings, 'stats': stats}
    def media(path, label):
        try:
            info = probe_media(path)
            if not info:
                errors.append(f'{label}: file missing: {path}')
            return info
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(f'{label}: cannot probe media: {exc}')
            return None
    if not isinstance(doc, dict):
        errors.append('TIMELINE: expected an object')
        return report()
    total = doc.get('duration')
    if not number(total) or total <= 0:
        errors.append('TIMELINE: duration must be a positive finite number')
        return report()
    if 'target_duration' in doc and (not number(doc['target_duration']) or abs(doc['target_duration'] - total) > 1e-6):
        errors.append('TIMELINE: duration and target_duration disagree')
    for key, default, low, high in [('subtitle_font_size',82,1,300), ('subtitle_y_center',1296,0,H), ('crf',13,0,51)]:
        val = doc.get(key, default)
        if not number(val) or not low <= val <= high:
            errors.append(f'TIMELINE: invalid {key}')
    if doc.get('trim_voice_silences', False) is not False:
        errors.append('AUDIO: trim silence before caption timing; trim_voice_silences must be false')
    shots = doc.get('shots')
    if not isinstance(shots, list) or not shots:
        errors.append('TIMELINE: shots must be a non-empty array')
        return report()
    ids, lengths, frame_sum, prev_end = set(), [], 0, 0.0
    for i, shot in enumerate(shots):
        label = f'SHOT {i+1}'
        if not isinstance(shot, dict):
            errors.append(f'{label}: expected an object'); continue
        sid = shot.get('id')
        if not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', sid) or sid in ids:
            errors.append(f'{label}: id must be unique and contain only letters, digits, _ or -')
        else:
            ids.add(sid)
        fields = ('start','end','duration','source_in','source_out','frames','playback_speed')
        bad = [key for key in fields if not number(shot.get(key))]
        if bad:
            errors.append(f'{label}: missing or invalid numeric fields: {", ".join(bad)}'); continue
        start, end, duration = shot['start'], shot['end'], shot['duration']
        if start < 0 or duration <= 0 or end <= start:
            errors.append(f'{label}: times must be nonnegative with positive duration'); continue
        if abs(end-start-duration) > 1e-6:
            errors.append(f'{label}: duration does not equal end - start')
        if abs(start-prev_end) > 1e-6:
            errors.append(f'{label}: shot gap or overlap')
        prev_end = end
        frames = shot['frames']
        if not isinstance(frames, int) or frames <= 0 or frames != round(end*FPS)-round(start*FPS):
            errors.append(f'{label}: frames do not match shot boundaries')
        frame_sum += frames
        lengths.append(duration)
        source_length = shot['source_out'] - shot['source_in']
        if shot['source_in'] < 0 or source_length <= 0:
            errors.append(f'{label}: invalid source interval')
        if shot['playback_speed'] <= 0 or abs(source_length/duration-shot['playback_speed']) > 1e-6:
            errors.append(f'{label}: declared speed differs from source interval / duration')
        if 'speed' in shot and (not number(shot['speed']) or abs(shot['speed']-shot['playback_speed']) > 1e-6):
            errors.append(f'{label}: speed aliases disagree')
        if duration > MAX_SHOT_SECONDS:
            warnings.append(f'{label}: shot longer than 2.2 seconds')
        if shot.get('crop_mode') != 'cover':
            errors.append(f'{label}: crop_mode must be cover')
        if not number(shot.get('zoom',1)) or not 1 <= shot.get('zoom',1) <= 4:
            errors.append(f'{label}: zoom must be between 1 and 4')
        if 'source' in shot and (not isinstance(shot['source'], str) or not shot['source']):
            errors.append(f'{label}: source must be a non-empty path'); continue
        src = _shot_source(shot)
        if not src.is_relative_to(ROOT.resolve()):
            errors.append(f'{label}: source escapes project folder'); continue
        info = media(src, label)
        if info:
            if not info['width'] or not info['height']:
                errors.append(f'{label}: source has no video stream')
            if shot['source_out'] > info['duration'] + 1/FPS:
                errors.append(f'{label}: source interval exceeds media duration')
            shot.setdefault('crop', [0,0,info['width'],info['height']])
        crop = shot.get('crop')
        if not isinstance(crop, list) or len(crop) != 4 or not all(isinstance(v,int) and not isinstance(v,bool) for v in crop):
            errors.append(f'{label}: crop must contain four integers')
        else:
            x,y,w,h = crop
            if x < 0 or y < 0 or w < 2 or h < 2 or (info and (x+w > info['width'] or y+h > info['height'])):
                errors.append(f'{label}: crop outside video bounds')
        regions = shot.get('delogo', doc.get('delogo', []))
        if not isinstance(regions,list) or any(not isinstance(r,list) or len(r)!=4 or not all(number(v) for v in r) for r in regions):
            errors.append(f'{label}: delogo must be an array of four-number rectangles')
        else:
            shot['delogo'] = regions
    if abs(prev_end-total) > 1e-6 or frame_sum != round(total*FPS):
        errors.append('TIMELINE: shot end / frame totals do not match duration')
    caps = doc.get('captions', [])
    if not isinstance(caps,list):
        errors.append('CAPTIONS: expected an array'); caps=[]
    prev = 0.0
    for i,cap in enumerate(caps):
        label=f'CAPTION {i+1}'
        if not isinstance(cap,dict) or not all(number(cap.get(k)) for k in ('start','end')) or not isinstance(cap.get('text'),str):
            errors.append(f'{label}: requires numeric start/end and string text'); continue
        start,end,txt=cap['start'],cap['end'],cap['text']
        if start < 0 or end <= start or end > total+1e-6 or start < prev-1e-6:
            errors.append(f'{label}: invalid times, overlap or outside timeline')
        prev=end
        if not 8 <= len(txt) <= 22:
            warnings.append(f'{label}: recommended length is 8–22 Unicode characters; review Thai readability')
        if any(p in txt for p in ('ครับ','ผม')):
            warnings.append(f'{label}: review narrator language in context')
        highlights=cap.get('highlights',[])
        if not isinstance(highlights,list):
            errors.append(f'{label}: highlights must be an array'); continue
        for h in highlights:
            if not isinstance(h,dict) or not isinstance(h.get('text'),str) or not h['text'] or h['text'] not in txt:
                errors.append(f'{label}: highlight text must occur in caption'); continue
            hs,he=h.get('start',start),h.get('end',end)
            if not number(hs) or not number(he) or not start <= hs < he <= end:
                errors.append(f'{label}: highlight outside caption interval')
    try:
        ImageFont.truetype(str(FONT),82)
    except (OSError,ValueError):
        errors.append(f'FONT: missing or unreadable font: {FONT}')
    try:
        with Image.open(BRAND_IMAGE) as img:
            if img.size != (W,H):
                errors.append('BRAND: image must be 1080x1920')
            img.verify()
    except (OSError,ValueError):
        errors.append(f'BRAND: missing or unreadable image: {BRAND_IMAGE}')
    audio = approved_audio or voice_path
    if audio:
        info=media(audio,'AUDIO')
        if info:
            if not info['has_audio']:
                errors.append('AUDIO: no audio stream')
            if info['duration'] > total+0.05:
                errors.append('AUDIO: longer than timeline; narration would be cut off')
            elif info['duration'] < total-0.25:
                warnings.append('AUDIO: ends before timeline; check trailing silence')
    if MUSIC_MEDIA and not approved_audio:
        info=media(MUSIC_MEDIA,'MUSIC')
        if info and not info['has_audio']:
            errors.append('MUSIC: no audio stream')
    stats.update(duration=total,shots=len(shots),captions=len(caps),frames_expected=round(total*FPS),frames_in_shots=frame_sum)
    return report()


def _draw_vector_play_icon(draw, cx, cy, radius=14, color=(255, 215, 60, 255)):
    """Draw a crisp vector play triangle without relying on font emoji glyphs."""
    pts = [
        (cx - radius * 0.65, cy - radius * 0.85),
        (cx - radius * 0.65, cy + radius * 0.85),
        (cx + radius * 0.95, cy),
    ]
    draw.polygon(pts, fill=color)


def _draw_vector_heart_icon(draw, cx, cy, size=15, color=(255, 50, 80, 255)):
    """Draw a crisp vector heart icon without relying on font emoji glyphs."""
    r = size * 0.52
    draw.ellipse([cx - size * 0.95, cy - size * 0.85, cx + size * 0.05, cy + size * 0.15], fill=color)
    draw.ellipse([cx - size * 0.05, cy - size * 0.85, cx + size * 0.95, cy + size * 0.15], fill=color)
    pts = [
        (cx - size * 0.92, cy - size * 0.18),
        (cx + size * 0.92, cy - size * 0.18),
        (cx, cy + size * 0.95),
    ]
    draw.polygon(pts, fill=color)


def generate_brand_frame(
    top_text,
    red_highlight='',
    out_path=None,
    channel_name='MathTheWhat',
    sub_tagline='ฝากกดติดตามช่อง ด้วยนะคะ',
    top_color_mode='white_red',
    canva_banner_path=None,
    rainbow_border=False,
    bottom_sub_banner='',
    badge_subtitle='ANIME RECAP',
):
    """Auto-generate 1080x1920 NNNKx2-style brand_frame.png:
    - Top Header (y=0..332): 1-line 860-980px extra-bold hook with pure Red (#FF0000) or Gold (#FFD600) emphasis + thick black stroke.
    - Center Window (y=332..1596): Transparent 1080x1264 cutout (with optional Clip 06 neon border or Clip 08 sub-bar).
    - Bottom Banner (y=1596..1920): Cyber-anime graphic banner or imported Canva artwork (`canva_banner_path`), zero missing-glyph boxes.
    """
    if out_path is None:
        out_path = BRAND_IMAGE
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. Top Header (y=0..332)
    draw.rectangle([0, 0, W, 332], fill=(0, 0, 0, 255))
    font_size = 118
    font = ImageFont.truetype(str(FONT), font_size)
    while font.getlength(top_text) > 980 and font_size > 54:
        font_size -= 2
        font = ImageFont.truetype(str(FONT), font_size)
    while font.getlength(top_text) < 860 and font_size < 136:
        test_f = ImageFont.truetype(str(FONT), font_size + 2)
        if test_f.getlength(top_text) > 980:
            break
        font_size += 2
        font = test_f

    total_w = font.getlength(top_text)
    x_start = round((W - total_w) / 2)
    bbox = font.getbbox(top_text)
    text_h = bbox[3] - bbox[1]
    y_start = round((332 - text_h) / 2 - bbox[1] + 6)

    if red_highlight and red_highlight in top_text:
        idx = top_text.index(red_highlight)
        hi_color = (255, 214, 0, 255) if top_color_mode == 'white_gold' else (255, 0, 0, 255)
        segments = [
            (top_text[:idx], (255, 255, 255, 255)),
            (red_highlight, hi_color),
            (top_text[idx + len(red_highlight):], (255, 255, 255, 255)),
        ]
    elif top_color_mode == 'gold':
        segments = [(top_text, (255, 214, 0, 255))]
    elif top_color_mode == 'red':
        segments = [(top_text, (255, 0, 0, 255))]
    else:
        segments = [(top_text, (255, 255, 255, 255))]

    shadow_layer = Image.new('RGBA', (W, 332), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow_layer)
    cx = x_start
    for seg_txt, _ in segments:
        if not seg_txt:
            continue
        s_draw.text((cx + 4, y_start + 6), seg_txt, font=font, fill=(0, 0, 0, 225), stroke_width=10, stroke_fill=(0, 0, 0, 225))
        cx += font.getlength(seg_txt)
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=4))
    img.alpha_composite(shadow_layer, (0, 0))

    cx = x_start
    for seg_txt, color in segments:
        if not seg_txt:
            continue
        draw.text((cx, y_start), seg_txt, font=font, fill=color, stroke_width=8, stroke_fill=(10, 10, 10, 255))
        cx += font.getlength(seg_txt)

    # Optional Clip 06 style RGB Neon Frame Border around center panel
    if rainbow_border:
        colors = [(255, 0, 90, 255), (255, 200, 0, 255), (0, 230, 255, 255), (170, 70, 255, 255)]
        for i, col in enumerate(colors):
            pad = i * 3
            draw.rectangle([pad, 332 + pad, W - 1 - pad, 1596 - 1 - pad], outline=col, width=3)

    # Optional Clip 08 style secondary static hook bar right above bottom banner
    if bottom_sub_banner:
        sb_y0, sb_y1 = 1480, 1596
        draw.rectangle([0, sb_y0, W, sb_y1], fill=(0, 0, 0, 220))
        f_sb = ImageFont.truetype(str(FONT), 68)
        draw.text((W // 2, (sb_y0 + sb_y1) // 2), bottom_sub_banner, font=f_sb, fill=(255, 255, 255, 255), anchor='mm', stroke_width=6, stroke_fill=(0, 0, 0, 255))

    # 2. Bottom Banner (y=1596..1920, 1080x324)
    by0, by1 = 1596, 1920
    bh = by1 - by0
    if canva_banner_path and Path(canva_banner_path).is_file():
        custom = Image.open(canva_banner_path).convert('RGBA')
        if custom.size == (W, H):
            img.alpha_composite(custom.crop((0, by0, W, by1)), (0, by0))
        else:
            custom = custom.resize((W, bh), Image.Resampling.LANCZOS)
            img.alpha_composite(custom, (0, by0))
        img.save(out_path)
        return out_path

    banner = Image.new('RGBA', (W, bh), (16, 10, 34, 255))
    b_draw = ImageDraw.Draw(banner)
    for y in range(bh):
        t = y / max(1, bh - 1)
        b_draw.line([(0, y), (W, y)], fill=(int(28 * (1 - t) + 10 * t), int(12 * (1 - t) + 6 * t), int(62 * (1 - t) + 28 * t), 255))
    for gx in range(-200, W + 200, 86):
        b_draw.line([(gx, 0), (gx - 110, bh)], fill=(120, 60, 210, 40), width=2)
        b_draw.line([(gx, 0), (gx + 110, bh)], fill=(50, 160, 240, 28), width=1)
    for gy in range(26, bh, 44):
        b_draw.line([(0, gy), (W, gy)], fill=(140, 75, 230, 32), width=1)

    # Top neon divider line
    b_draw.rectangle([0, 0, W, 6], fill=(185, 105, 255, 255))
    b_draw.rectangle([0, 6, W, 11], fill=(95, 40, 190, 185))

    # Left emblem badge (x=28..214)
    b_draw.rounded_rectangle([28, 38, 214, 286], radius=26, fill=(38, 20, 76, 238), outline=(185, 120, 255, 230), width=3)
    f_badge = ImageFont.load_default(size=60)
    f_badge_sub = ImageFont.load_default(size=24) if badge_subtitle.isascii() else ImageFont.truetype(str(FONT), 24)
    b_draw.text((121, 124), 'MTW', font=f_badge, fill=(255, 215, 55, 255), anchor='mm', stroke_width=5, stroke_fill=(20, 8, 45, 255))
    b_draw.text((121, 206), badge_subtitle, font=f_badge_sub, fill=(225, 195, 255, 255), anchor='mm')

    # Center Channel Title & CTA Pill (x=232..824 -> center=528)
    center_x = 528
    f_chan = ImageFont.load_default(size=72) if channel_name.isascii() else ImageFont.truetype(str(FONT), 72)
    b_draw.text((center_x, 106), channel_name, font=f_chan, fill=(255, 235, 95, 255), anchor='mm', stroke_width=7, stroke_fill=(24, 10, 52, 255))

    pill_x0, pill_y0, pill_x1, pill_y1 = 236, 184, 820, 268
    b_draw.rounded_rectangle([pill_x0, pill_y0, pill_x1, pill_y1], radius=42, fill=(18, 10, 40, 240), outline=(165, 105, 250, 225), width=3)
    f_cta = ImageFont.truetype(str(FONT), 34)
    b_draw.text((center_x, 226), sub_tagline, font=f_cta, fill=(255, 255, 255, 255), anchor='mm')
    _draw_vector_play_icon(b_draw, pill_x0 + 38, 226, radius=13, color=(255, 215, 60, 255))
    _draw_vector_heart_icon(b_draw, pill_x1 - 38, 226, size=14, color=(255, 55, 90, 255))

    # Right Subscribe Button (x=842..1052 -> center=947)
    b_draw.rounded_rectangle([842, 72, 1052, 256], radius=26, fill=(230, 22, 38, 255), outline=(255, 255, 255, 245), width=3)
    f_bell = ImageFont.truetype(str(FONT), 42)
    f_sub = ImageFont.load_default(size=32)
    b_draw.text((947, 134), 'กดติดตาม', font=f_bell, fill=(255, 255, 255, 255), anchor='mm', stroke_width=3, stroke_fill=(115, 0, 10, 255))
    b_draw.text((947, 198), 'SUBSCRIBE', font=f_sub, fill=(255, 238, 185, 255), anchor='mm')

    img.alpha_composite(banner, (0, by0))
    img.save(out_path)
    return out_path


def write_captions(doc):
    """Write ASS and SRT captions using NNNKx2 8-clip metrics:
    - Subtitle center y=1296 (box top y=1235, height=122)
    - Base font size=82, max text width=940px (box width up to 1020px)
    - Pure red highlight (&H0000FF&) and thick black outline (4px)
    - Uses raw strings (rf'...') so ASS override tags (\an7, \bord0, \1c, \fs, \fscx, \t) are never corrupted into ASCII control chars!
    """
    head = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 2
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Noto Sans Thai,82,&H00FFFFFF,&H000000FF,&H00000000,&H78000000,-1,0,0,0,100,100,0,0,1,4,2,5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    srt = []
    y_center = int(doc.get('subtitle_y_center', 1296))
    bh = 122
    y_box = y_center - bh // 2
    subtitles_enabled = doc.get('subtitles_enabled', True)

    for i, c in enumerate(doc.get('captions', [])):
        start, end = c['start'], c['end']
        txt = c['text']
        srt.append(f'{i+1}\n{time_srt(start)} --> {time_srt(end)}\n{txt}\n')
        if not subtitles_enabled:
            continue
        size = int(doc.get('subtitle_font_size', 82))
        font = ImageFont.truetype(str(FONT), size)
        while font.getlength(txt) > 940 and size > 48:
            size -= 1
            font = ImageFont.truetype(str(FONT), size)
        bw = min(1020, round(font.getlength(txt)) + 64)
        x = round((1080 - bw) / 2)
        box = rf'{{\an7\pos({x},{y_box})\p1\bord0\shad0\1c&H000000&\1a&H68&}}m 0 0 l {bw} 0 {bw} {bh} 0 {bh}'
        events.append(f'Dialogue: 0,{time_ass(start)},{time_ass(end)},Caption,,0,0,0,,{box}')
        hi = c.get('highlights', [])
        segments, mode, _missing = caption_segments(txt, hi)
        # Active window of each highlight (frame-snapped)
        windows = {}
        for j, part in enumerate(hi):
            if mode == 'partition':
                hs = start if j == 0 else round(float(part['start']) * FPS) / FPS
                he = end if j + 1 == len(hi) else round(float(hi[j + 1]['start']) * FPS) / FPS
            else:
                hs = round(float(part.get('start', start)) * FPS) / FPS
                nxt = hi[j + 1].get('start', end) if j + 1 < len(hi) else end
                he = round(float(part.get('end', nxt)) * FPS) / FPS
            hs, he = max(start, hs), min(end, he)
            if he > hs:
                windows[j] = (hs, he)
        cuts = sorted({start, end, *[t for w in windows.values() for t in w]})
        intervals = []  # (t0, t1, active highlight index or None), merged when unchanged
        for t0, t1 in zip(cuts, cuts[1:]):
            if t1 - t0 < 1e-6:
                continue
            active = next((j for j, (a, b) in windows.items() if a <= t0 + 1e-6 and t1 <= b + 1e-6), None)
            if intervals and intervals[-1][2] == active:
                intervals[-1] = (intervals[-1][0], t1, active)
            else:
                intervals.append((t0, t1, active))
        ass_size = round(size * 1.4)
        for k, (t0, t1, active) in enumerate(intervals):
            bounce = r'\fscx86\fscy86\t(0,75,\fscx104\fscy104)\t(75,145,\fscx100\fscy100)' if k == 0 else ''
            body = rf'{{\an5\pos(540,{y_center})\fs{ass_size}{bounce}\1c&HFFFFFF&}}'
            for seg_txt, idx in segments:
                seg_txt = ass_escape(seg_txt)
                if idx is not None and idx == active:
                    body += rf'{{\1c&H0000FF&}}{seg_txt}{{\1c&HFFFFFF&}}'
                else:
                    body += seg_txt
            events.append(f'Dialogue: 1,{time_ass(t0)},{time_ass(t1)},Caption,,0,0,0,,{body}')
    (OUT / 'MathTheWhat_Thai.ass').write_text(head + '\n'.join(events) + '\n', encoding='utf-8')
    (OUT / 'MathTheWhat_Thai.srt').write_text('\n'.join(srt), encoding='utf-8')


def render_shot(shot):
    """Render a single shot at 1080x1264 (30fps).
    Default crop_mode is 'cover' (NNNKx2 full-bleed center panel, zero gblur bars).
    Supports optional 'zoom' (e.g. 1.15) and 'motion' ('punch_in', 'zoom_in', 'zoom_out').
    """
    output = WORK / 'shots' / f'{shot["id"]}.mp4'
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.stem + '.rendering.mp4')
    x, y, w, h = shot['crop']
    length = shot['source_out'] - shot['source_in']
    dur = shot['duration']
    frames = int(shot['frames'])
    source = (ROOT / shot['source']).resolve() if shot.get('source') else SOURCE_MEDIA
    if not source.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('Source path escapes project')

    crop_mode = shot.get('crop_mode', 'cover')
    zoom = float(shot.get('zoom', 1.0))
    motion = shot.get('motion', 'none')

    # Optional watermark / burned-in foreign subtitle removal, in SOURCE pixel coordinates [x, y, w, h]
    pre = ''
    regions = shot.get('delogo') or []
    if regions:
        info = probe_media(source) or {'width': 0, 'height': 0}
        sw_src, sh_src = info['width'], info['height']
        for rx, ry, rw, rh in regions:
            rx, ry = max(1, int(rx)), max(1, int(ry))
            if sw_src:
                rw = min(int(rw), sw_src - rx - 1)
                rh = min(int(rh), sh_src - ry - 1)
            if rw > 2 and rh > 2:
                pre += f'delogo=x={rx}:y={ry}:w={rw}:h={rh},'

    # Ultra-sharp anime filter parameters (configurable per shot or globally)
    sharp_preset = shot.get('sharpness', 'ultra')
    if sharp_preset == 'ultra':
        unsharp_flt = 'unsharp=5:5:0.95:3:3:0.20'
    elif sharp_preset == 'medium':
        unsharp_flt = 'unsharp=5:5:0.70:3:3:0.0'
    else:
        unsharp_flt = 'unsharp=5:5:0.95:3:3:0.20'
    scale_flags = 'lanczos+accurate_rnd+full_chroma_int+full_chroma_inp'

    if crop_mode == 'contain_blur':
        filt = (
            f'[0:v]{pre}crop={w}:{h}:{x}:{y},setpts=(PTS-STARTPTS)*{dur/length:.10f},fps=30,split[fg][b];'
            f'[b]scale=1080:1264:force_original_aspect_ratio=increase,crop=1080:1264,gblur=sigma=28,eq=brightness=-0.10:saturation=0.88[bg];'
            f'[fg]scale=1080:1264:force_original_aspect_ratio=decrease:force_divisible_by=2:flags={scale_flags},{unsharp_flt}[sharp];'
            '[bg][sharp]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v]'
        )
    else:
        # NNNKx2 8-Clip Standard: Full-Fill 1080x1264 edge-to-edge (zero blurred bars) + Ultra-Sharp Anime Mastering
        if motion == 'punch_in':
            zoom = max(zoom, 1.14)
        sw = round(1080 * max(1.0, zoom))
        sh = round(1264 * max(1.0, zoom))
        if sw % 2:
            sw += 1
        if sh % 2:
            sh += 1
        filt = (
            f'[0:v]{pre}crop={w}:{h}:{x}:{y},setpts=(PTS-STARTPTS)*{dur/length:.10f},fps=30,'
            f'scale={sw}:{sh}:force_original_aspect_ratio=increase:flags={scale_flags},'
            f'crop=1080:1264,{unsharp_flt},setsar=1,format=yuv420p[v]'
        )

    run([
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
        '-filter_complex_threads', '2', '-threads', '2',
        '-ss', str(shot['source_in']), '-t', str(length), '-i', str(source),
        '-filter_complex', filt, '-map', '[v]', '-an',
        '-frames:v', str(frames),
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '0', '-threads', '2',
        str(temporary)
    ])
    count = int(json.loads(run([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=nb_frames', '-of', 'json', str(temporary)
    ]))['streams'][0]['nb_frames'])
    if count != frames:
        raise ValueError(f'Incorrect shot frames: {shot["id"]} {count} != {frames}')
    temporary.replace(output)
    print('rendered', shot['id'], frames, crop_mode, flush=True)
    return output


def auto_zip_if_over_limit(file_path, max_mb=50.0, extra_files=None, force_zip=False):
    """If `file_path` exceeds `max_mb` (default 50 MB) or `force_zip` is True, compress it into a .zip archive."""
    file_path = Path(file_path)
    if not file_path.is_file():
        return None
    size_bytes = file_path.stat().st_size
    size_mb = size_bytes / (1024 * 1024)
    if size_mb <= max_mb and not force_zip:
        print(f'[SIZE CHECK] {file_path.name} is {size_mb:.2f} MB (<= {max_mb:.1f} MB limit) — no zip needed.', flush=True)
        return None
    zip_path = file_path.with_suffix('.zip')
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.write(file_path, file_path.name)
        for ef in (extra_files or []):
            ef_p = Path(ef)
            if ef_p.is_file():
                zf.write(ef_p, ef_p.name)
    zip_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f'[AUTO-ZIP >{max_mb:.0f}MB] {file_path.name} ({size_mb:.2f} MB) -> compressed to {zip_path.name} ({zip_mb:.2f} MB)', flush=True)
    return zip_path


def render(doc, reuse_shots=False, max_mb=50.0, force_zip=False):
    if reuse_shots:
        paths = [WORK / 'shots' / f'{s["id"]}.mp4' for s in doc['shots']]
        if not all(p.is_file() for p in paths):
            raise ValueError('Missing existing shot file')
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            paths = list(pool.map(render_shot, doc['shots']))
    concat = WORK / 'concat.txt'
    concat.write_text(''.join(f"file 'shots/{p.name}'\n" for p in paths), encoding='utf-8')
    total = doc['duration']

    # NNNKx2 Audio Profile: Crisp front-and-center narration (-12.8 LUFS) + audible ducked BGM (-25 LUFS) -> Master -12.2 LUFS
    # Note: Silence removal should be performed BEFORE caption timing via `dm_project.py trim-silence`.
    trim_silence = bool(doc.get('trim_voice_silences', False))
    sil_filter = 'silenceremove=stop_periods=-1:stop_duration=0.18:stop_threshold=-38dB:stop_silence=0.05,' if trim_silence else ''
    has_music = bool(MUSIC_MEDIA) and Path(MUSIC_MEDIA).is_file()
    if has_music:
        af = (
            f'[0:a]highpass=f=80,{sil_filter}loudnorm=I=-12.8:TP=-0.8:LRA=6,apad,atrim=0:{total},asplit[voice][side];'
            f'[1:a]atrim=0:{total},asetpts=PTS-STARTPTS,loudnorm=I=-25.0:TP=-4.0:LRA=6,afade=t=in:st=0:d=0.35,afade=t=out:st={max(0, total-0.9)}:d=0.9[music];'
            '[music][side]sidechaincompress=threshold=0.022:ratio=3.0:attack=15:release=220[ducked];'
            '[voice][ducked]amix=inputs=2:normalize=0:duration=first,loudnorm=I=-12.2:TP=-0.5:LRA=5[out]'
        )
        # -stream_loop -1: a BGM shorter than the clip loops instead of leaving a silent tail
        audio_inputs = ['-i', str(VOICE_MEDIA), '-stream_loop', '-1', '-i', str(MUSIC_MEDIA)]
    else:
        af = f'[0:a]highpass=f=80,{sil_filter}loudnorm=I=-12.8:TP=-0.8:LRA=6,apad,atrim=0:{total},loudnorm=I=-12.2:TP=-0.5:LRA=5[out]'
        audio_inputs = ['-i', str(VOICE_MEDIA)]
    if APPROVED_AUDIO_MEDIA:
        audio_input = APPROVED_AUDIO_MEDIA
        audio_options = ['-c:a', 'aac', '-b:a', '192k', '-ar', '48000']
    else:
        run([
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            *audio_inputs,
            '-filter_complex', af, '-map', '[out]',
            '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s24le', str(WORK / 'mix.wav')
        ])
        audio_input = WORK / 'mix.wav'
        audio_options = ['-c:a', 'aac', '-b:a', '192k', '-ar', '48000']

    vf = (
        '[0:v]pad=1080:1920:0:332:black[base];'
        '[base][1:v]overlay=0:0:format=auto,'
        f'ass={OUT.relative_to(ROOT).as_posix()}/MathTheWhat_Thai.ass:fontsdir=inputs/fonts,format=yuv420p[v]'
    )
    final = OUT / FINAL_NAME
    temporary = final.with_name(final.stem + '.rendering.mp4')
    crf_val = str(doc.get('crf', 13))
    run([
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
        '-filter_complex_threads', '2', '-f', 'concat', '-safe', '0',
        '-i', str(concat), '-loop', '1', '-i', str(BRAND_IMAGE), '-i', str(audio_input),
        '-filter_complex', vf, '-map', '[v]', '-map', '2:a',
        '-frames:v', str(round(total * FPS)), '-t', str(total),
        '-c:v', 'libx264', '-preset', 'slow', '-tune', 'animation', '-crf', crf_val,
        '-profile:v', 'high', '-level', '4.2',
        '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
        '-threads', '3',
        '-pix_fmt', 'yuv420p', *audio_options, '-movflags', '+faststart',
        '-metadata', 'title=' + TITLE,
        '-metadata', 'comment=NNNKx2 8-Clip Full-Fill Ultra-Sharp Edition (v3.1).',
        str(temporary)
    ])
    final_frames = int(json.loads(run([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=nb_frames', '-of', 'json', str(temporary)
    ]))['streams'][0]['nb_frames'])
    if final_frames != round(total * FPS):
        raise ValueError('Final frame count differs from timeline')
    info = probe_media(temporary)
    if not info or (info['width'], info['height']) != (W, H) or not info['has_audio']:
        raise ValueError('Final output must contain 1080x1920 video and audio')
    temporary.replace(final)
    print('final', str(final), flush=True)
    extra = [OUT / 'MathTheWhat_Thai.ass', OUT / 'MathTheWhat_Thai.srt', OUT / 'QA_preflight.json']
    auto_zip_if_over_limit(final, max_mb=max_mb, extra_files=extra, force_zip=force_zip)


def main(argv=None):
    global OUT, SOURCE_MEDIA, VOICE_MEDIA, MUSIC_MEDIA, BRAND_IMAGE, APPROVED_AUDIO_MEDIA, FINAL_NAME, TITLE, FONT
    ap = argparse.ArgumentParser(description='ArtMath 1080x1920 offline renderer')
    ap.add_argument('--root', default=str(ROOT))
    ap.add_argument('--project', default='project.json')
    ap.add_argument('--timeline')
    ap.add_argument('--validate-only', action='store_true')
    ap.add_argument('--reuse-shots', action='store_true')
    ap.add_argument('--force', action='store_true', help='Deprecated: invalid inputs are never rendered')
    ap.add_argument('--overwrite', action='store_true')
    ap.add_argument('--max-mb', type=float, default=None)
    ap.add_argument('--always-zip', action='store_true')
    cli = ap.parse_args(argv)
    configure_root(cli.root)
    OUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    def local(value):
        if not isinstance(value,str) or not value:
            raise ValueError('Expected a non-empty project-relative file path')
        path=(ROOT/value).resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError('Path escapes project folder')
        return path
    try:
        config = json.loads(local(cli.project).read_text(encoding='utf-8-sig'))
        if not isinstance(config,dict):
            raise ValueError('project.json must be an object')
        SOURCE_MEDIA = local(config['source'])
        APPROVED_AUDIO_MEDIA = local(config['approved_audio']) if config.get('approved_audio') else None
        VOICE_MEDIA = local(config['voice']) if config.get('voice') else None
        if not VOICE_MEDIA and not APPROVED_AUDIO_MEDIA:
            raise ValueError('voice or approved_audio is required')
        MUSIC_MEDIA = local(config['music']) if config.get('music') else None
        BRAND_IMAGE = local(config['brand'])
        FONT = local(config.get('font','inputs/fonts/NotoSansThai-ExtraBold.ttf'))
        FINAL_NAME = config.get('output_name','ArtMath_Final_1080p.mp4')
        if not isinstance(FINAL_NAME,str) or not re.fullmatch(r'[A-Za-z0-9_-]+[.]mp4',FINAL_NAME):
            raise ValueError('output_name must be a simple .mp4 filename')
        TITLE = str(config.get('title','ArtMath'))
        doc = normalize_timeline(json.loads(local(cli.timeline or config['timeline']).read_text(encoding='utf-8-sig')))
        report = validate_timeline(doc, VOICE_MEDIA, APPROVED_AUDIO_MEDIA)
        if not isinstance(doc, dict):
            raise ValueError('Timeline must be an object')
        max_mb = cli.max_mb if cli.max_mb is not None else doc.get('auto_zip_over_mb',config.get('auto_zip_over_mb',50.0))
        if not number(max_mb) or max_mb <= 0:
            report['errors'].append('auto_zip_over_mb must be a positive finite number')
        report['ok'] = not report['errors']
    except (OSError,ValueError,TypeError,KeyError,RuntimeError) as exc:
        report = {'ok':False,'errors':[f'PREFLIGHT: {exc}'],'warnings':[],'stats':{}}
    (OUT/'QA_preflight.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for kind in ('errors','warnings'):
        for msg in report[kind]:
            print(f'[{kind.upper()}] {msg}',flush=True)
    if not report['ok']:
        return 2
    if cli.validate_only:
        return 0
    destination = OUT
    if (destination/FINAL_NAME).exists() and not cli.overwrite:
        print('[ERROR] Output exists; pass --overwrite to replace this file after a successful render')
        return 2
    try:
        # Stage captions and final output together. No previous deliverable is touched on render failure.
        with tempfile.TemporaryDirectory(prefix='stage_',dir=destination) as stage:
            OUT=Path(stage)
            (OUT/'QA_preflight.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            write_captions(doc)
            render(doc,reuse_shots=cli.reuse_shots,max_mb=max_mb,
                   force_zip=cli.always_zip or bool(doc.get('always_zip_output',config.get('always_zip_output',False))))
            for artifact in OUT.iterdir():
                artifact.replace(destination/artifact.name)
        return 0
    except (OSError,ValueError,RuntimeError) as exc:
        print(f'[RENDER ERROR] {exc}',flush=True)
        return 1
    finally:
        OUT=destination


if __name__ == '__main__':
    raise SystemExit(main())
