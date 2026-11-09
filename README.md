# ArtMath / MathTheWhat — ระบบตัดต่อบน GitHub

ระบบ Python/FFmpeg สำหรับตรวจไทม์ไลน์ สร้างกรอบภาพ ซับไทย และเรนเดอร์วิดีโอแนวตั้ง 1080×1920 ที่ 30 fps โดยใช้กรอบกลาง 1080×1264 (y=332–1596)

Repository นี้เก็บเฉพาะโค้ด คู่มือ และตัวสร้างสื่อจำลอง คลิป เสียง ฟอนต์ที่ติดตั้ง และผลลัพธ์อยู่ในเครื่องหรือ Google Drive ไม่ต้องใส่ API key เพื่อเรนเดอร์

## ติดตั้ง

รองรับ Python 3.11–3.12, FFmpeg/FFprobe ที่มี libx264, libass, AAC, libmp3lame และฟอนต์ Noto Sans Thai

Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg fonts-noto-core
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
ffmpeg -version
ffprobe -version
```

Windows: ติดตั้ง Python 3.11 และ FFmpeg build ที่มี libass เพิ่ม `ffmpeg` และ `ffprobe` ลง PATH แล้วสร้าง virtual environment ด้วย `py -3.11 -m venv .venv` และเปิดใช้ `.venv\Scripts\Activate.ps1` ใน PowerShell ติดตั้ง dependency ด้วยคำสั่งเดียวกับด้านบน

หากระบบไม่มี Noto Sans Thai Bold/ExtraBold ให้ติดตั้งฟอนต์จากโครงการ Noto หรือวางไว้ที่ `assets/fonts/NotoSansThai-ExtraBold.ttf` (ไม่ commit ไฟล์ฟอนต์) คำสั่ง `new` จะคัดลอกฟอนต์เข้าโครงงาน ชื่อไฟล์ปลายทางคงที่ แต่ตัวฟอนต์อาจเป็น Bold หรือ ExtraBold ตามที่ติดตั้ง

CI ทดสอบ Linux บน Python 3.11 และ 3.12; Windows ยังไม่ได้ตรวจด้วย CI

## เริ่มจากตัวอย่างจำลอง

```bash
python examples/create_demo.py
python tools/dm_project.py validate .local/demo
python tools/dm_project.py render .local/demo
```

ตัวอย่างสร้าง test pattern, เสียง sine 440 Hz และซับไทยเป็นเวลา 2 วินาที ไม่มีภาพหรือเสียงบุคคลจริง ผลลัพธ์อยู่ใน `.local/demo/outputs/ArtMath_Final_1080p.mp4` หากทำซ้ำให้เลือกโฟลเดอร์ใหม่ด้วย `--dest` เพราะตัวสร้างจะไม่เขียนทับโครงงานเดิม

เรนเดอร์ซ้ำโครงงานเดิม:

```bash
python tools/dm_project.py render .local/demo --overwrite
```

`--overwrite` แทนที่เฉพาะผลลัพธ์เป้าหมายหลังเรนเดอร์และตรวจ MP4 สำเร็จ ไม่ลบวิดีโอเดิมล่วงหน้า ผลลัพธ์และซับถูกสร้างใน staging directory ก่อนเผยแพร่กลับมา ไฟล์อื่นใน outputs ไม่ถูกลบ รายงาน `QA_preflight.json` จะแสดงการตรวจครั้งล่าสุดแม้ตรวจไม่ผ่าน

## โครงงานจริง

```bash
python tools/dm_project.py new --dest .local/episode_02 --video /path/to/video.mp4 --voice /path/to/narration.wav --top-title "ชื่อตอน" --bottom-banner "ข้อความด้านล่าง"
```

คำสั่งนี้สร้าง `project.json`, `timeline.json`, inputs, work และ outputs พร้อมช็อตเริ่มต้นจากช่วงแรกของฟุตเทจ ต้องแทนช็อตตัวอย่างและเติมซับให้ตรงเรื่องจริง หากเสียงยาวกว่าภาพ การตรวจจะหยุดแทนการตัดเสียงทิ้ง

ไฟล์ `project.json` ใช้ path สัมพัทธ์ภายในโครงงาน:

```json
{
  "source": "inputs/video.mp4",
  "voice": "inputs/narration.wav",
  "music": null,
  "font": "inputs/fonts/NotoSansThai-ExtraBold.ttf",
  "brand": "work/brand_frame.png",
  "timeline": "timeline.json",
  "output_name": "ArtMath_Final_1080p.mp4",
  "title": "ชื่อตอน"
}
```

ใส่ `approved_audio` แทนเสียงที่มิกซ์เสร็จแล้วได้ ระบบตรวจไฟล์นี้จริงและแปลงเป็น AAC ใน MP4; ไม่ใช้ `voice` หรือเพลงซ้ำในกรณีนี้

ตัวอย่าง timeline ที่ครบระยะเวลา:

```json
{
  "duration": 2.0,
  "trim_voice_silences": false,
  "shots": [{
    "id": "S01",
    "source": "inputs/video.mp4",
    "start": 0.0, "end": 2.0,
    "duration": 2.0, "frames": 60,
    "source_in": 0.0, "source_out": 2.0,
    "playback_speed": 1.0,
    "crop_mode": "cover"
  }],
  "captions": [{
    "start": 0.1, "end": 1.9, "text": "ทดสอบภาษาไทย",
    "highlights": [{"start": 0.3, "end": 1.5, "text": "ภาษาไทย"}]
  }]
}
```

- `duration` และ `target_duration` เป็นชื่อทางเลือก หากให้ทั้งคู่ต้องเท่ากัน
- รองรับ `speed` แทน `playback_speed` และ `word` แทน highlight `text`
- เติม duration/frames/source_out/speed จากข้อมูลที่คำนวณได้เท่านั้น ความขัดแย้งที่ระบุไว้จะไม่ถูกแก้อัตโนมัติ
- `frames = round(end × 30) - round(start × 30)` และ `playback_speed = (source_out - source_in) / duration`
- `crop` เป็น `[x,y,width,height]` ของต้นฉบับ หากไม่ระบุใช้เต็มเฟรมก่อน crop แบบ cover
- ซับเริ่มต้นอยู่ที่ y=1296 ขนาดตั้งต้น 82; ต้องดูภาพจริงว่าการตัดบรรทัดและสระไทยอ่านง่าย ไม่ใช้จำนวนตัวอักษรตัดสินอย่างเดียว
- `motion: "punch_in"` คือการขยายภาพช็อตนั้น ไม่ใช่การเคลื่อนกล้องแบบ keyframe

## ตรวจรับและจัดการเสียง

```bash
python tools/dm_project.py trim-silence --in narration_raw.mp3 --out narration_trimmed.mp3 --max-pause 0.16
python tools/dm_project.py brand-frame .local/episode_02 --top-title "ชื่อตอน" --bottom-banner "ข้อความด้านล่าง"
python tools/dm_project.py validate .local/episode_02
python tools/dm_project.py render .local/episode_02 --overwrite
python tools/dm_project.py zip-if-large .local/episode_02/outputs/ArtMath_Final_1080p.mp4 --max-mb 50
```

ตัดช่องเงียบก่อนจับเวลาซับเท่านั้น ตัวเรนเดอร์ปฏิเสธ `trim_voice_silences: true` เพื่อป้องกันซับเลื่อน คำสั่ง trim-silence ส่งออก MP3

`validate` เขียนเฉพาะรายงาน ไม่สร้างซับหรือเรนเดอร์วิดีโอ รายงานมี `ok`, `errors`, `warnings`, `stats`:

| ผลตรวจ | พฤติกรรม |
|---|---|
| ผิดชนิด/ฟิลด์ขาด, เวลา/เฟรม/ความเร็วขัดกัน, ช็อตซ้อนหรือมีช่องว่าง, source/crop เกินขอบเขต, ซับ/ไฮไลต์ผิดเวลา, สื่อ/ฟอนต์/กรอบภาพหาย | Error, exit code 2 และไม่เรนเดอร์ |
| ช็อตเกิน 2.2 วินาที, ซับนอกช่วงแนะนำ 8–22 Unicode characters, คำลงท้ายที่ควรตรวจบริบท, เสียงจบก่อนคลิป | Warning ให้ผู้ตัดต่อตรวจจริง |
| ผ่านการตรวจ | exit code 0 |
| FFmpeg หรือการตรวจไฟล์ส่งออกล้มเหลว | exit code 1; เก็บ MP4/ซับเดิมไว้ |

`--force` คงรับเพื่อความเข้ากันได้กับคำสั่งเก่า แต่ไม่ข้าม errors อีกต่อไป พากย์ตัวละครหรือคำว่า “ผม” ไม่ถูกปฏิเสธโดยอัตโนมัติ เพราะต้องตัดสินจากบริบท

เมื่อขนาดเกิน 50 MiB ระบบแพ็ก ZIP พร้อมซับและ QA; `--always-zip` บังคับแพ็กได้ ZIP ไม่รับประกันว่าจะลดวิดีโอให้ต่ำกว่าเกณฑ์

## เครื่องมือและการทดสอบ

```bash
python tools/story_editor.py --help
python tools/story_editor.py catalog .local/episode_02/inputs/video.mp4 --out .local/catalog.json
python -m unittest discover -s tests -v
```

`story_editor.py` เป็นเครื่องมือสำรวจภาพและเตรียม rough cut แยกจาก renderer; schema แผนเรื่องของเครื่องมือนี้ไม่ใช่ timeline ของ renderer โดยตรง คำสั่ง `plan-api` เป็นทางเลือกที่ส่งข้อมูลไปบริการภายนอกและต้องใช้ `OPENAI_API_KEY`; ไม่ได้ถูกเรียกโดย demo, CI หรือ renderer

GitHub Actions ทดสอบข้อมูลผิดชนิด ความเข้ากันได้ของ aliases การตรวจไฟล์ประกอบ การรักษาผลลัพธ์เดิมเมื่อผิดพลาด และ MP4 ที่เรนเดอร์จากสื่อจำลองด้วย FFprobe

## ที่มาและขอบเขต

นำโค้ดฐานจากชุด ArtMath v3.1 ใน Drive มาปรับเป็นระบบทั่วไป คู่มือนี้อธิบายพฤติกรรมที่ใช้ใน repository ปัจจุบัน เอกสารส่งต่อเก่าและไฟล์สถานะของตอนจริงอยู่ใน Drive ไม่ถือเป็นผลตรวจระบบรุ่นนี้

ยังไม่รวมผลิต EP.2 โคลนเสียง อัปโหลด Drive อัตโนมัติ หรือเผยแพร่ YouTube การตรวจทางเทคนิคไม่ได้ยืนยันข้อเท็จจริงในเรื่อง คุณภาพการพากย์ หรือความดัง LUFS ของไฟล์จริง ต้องดูและฟังงานก่อนส่งมอบ
