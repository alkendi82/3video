import os
import json
import uuid
import subprocess
import tempfile
import anthropic
from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
from werkzeug.utils import secure_filename

app = Flask(__name__)
CORS(app)

app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
UPLOAD_FOLDER = tempfile.gettempdir()

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

LANG_NAMES = {
    "ar": "العربية", "en": "الإنجليزية", "fr": "الفرنسية",
    "es": "الإسبانية", "de": "الألمانية", "tr": "التركية",
    "hi": "الهندية", "zh": "الصينية"
}

def ms_to_srt(ms):
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    ms2 = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms2:03d}"

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/translate", methods=["POST"])
def translate():
    if "video" not in request.files:
        return jsonify({"error": "لم يتم رفع فيديو"}), 400

    file = request.files["video"]
    src_lang = request.form.get("src_lang", "auto")
    tgt_lang = request.form.get("tgt_lang", "ar")
    style = request.form.get("style", "classic")
    tgt_name = LANG_NAMES.get(tgt_lang, tgt_lang)

    uid = str(uuid.uuid4())
    ext = secure_filename(file.filename).rsplit(".", 1)[-1] if "." in file.filename else "mp4"
    input_path = os.path.join(UPLOAD_FOLDER, f"{uid}_input.{ext}")
    srt_path = os.path.join(UPLOAD_FOLDER, f"{uid}.srt")
    output_path = os.path.join(UPLOAD_FOLDER, f"{uid}_output.mp4")

    try:
        file.save(input_path)

        result = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", input_path
        ], capture_output=True, text=True)
        duration_ms = 60000
        try:
            info = json.loads(result.stdout)
            duration_ms = int(float(info["format"]["duration"]) * 1000)
        except:
            pass

        with open(input_path, "rb") as f:
            video_data = f.read()

        import base64
        b64_video = base64.b64encode(video_data).decode("utf-8")
        media_type = f"video/{ext}" if ext in ["mp4","mov","avi","mkv","webm"] else "video/mp4"

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=f"""أنت نظام لتفريغ الفيديو وترجمته. مهمتك:
1. استمع للكلام في الفيديو وفرّغه بدقة
2. قسّمه إلى أجزاء زمنية (كل 3-5 ثوانٍ)
3. ترجم كل جزء إلى {tgt_name}

أجب فقط بـ JSON صالح بدون أي نص إضافي أو backticks:
{{"segments":[{{"start":0,"end":3000,"original":"النص","translated":"الترجمة"}}]}}

- start و end بالميلي ثانية
- مدة الفيديو التقريبية: {duration_ms}ms
- اللغة المصدر: {src_lang}
- لا تضف مقاطع صامتة أو موسيقى فقط""",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {"type": "base64", "media_type": media_type, "data": b64_video}
                    },
                    {"type": "text", "text": f"فرّغ وترجم هذا الفيديو إلى {tgt_name}"}
                ]
            }]
        )

        raw = response.content[0].text
        parsed = json.loads(raw.replace("```json", "").replace("```", "").strip())
        segments = parsed.get("segments", [])

        if not segments:
            return jsonify({"error": "لم يُعثر على كلام في الفيديو"}), 400

        srt_content = "\n\n".join(
            f"{i+1}\n{ms_to_srt(seg['start'])} --> {ms_to_srt(seg['end'])}\n{seg['translated']}"
            for i, seg in enumerate(segments)
        )

        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_content)

        if style == "yellow":
            force_style = "FontName=Arial,FontSize=22,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,Outline=2,Bold=1,Alignment=2,MarginV=25"
        elif style == "shadow":
            force_style = "FontName=Arial,FontSize=22,PrimaryColour=&H00FFFFFF,Shadow=3,ShadowColour=&H80000000,Outline=0,Alignment=2,MarginV=25"
        else:
            force_style = "FontName=Arial,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Alignment=2,MarginV=25"

        sub_filter = f"subtitles={srt_path}:force_style='{force_style}'"
        ffmpeg_cmd = [
            "ffmpeg", "-i", input_path,
            "-vf", sub_filter,
            "-c:a", "copy",
            "-preset", "fast",
            "-crf", "23",
            "-y", output_path
        ]

        proc = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            return jsonify({"error": "فشل FFmpeg: " + proc.stderr[-300:]}), 500

        return send_file(
            output_path,
            mimetype="video/mp4",
            as_attachment=True,
            download_name="video_مترجم.mp4"
        )

    finally:
        for p in [input_path, srt_path]:
            try: os.remove(p)
            except: pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
