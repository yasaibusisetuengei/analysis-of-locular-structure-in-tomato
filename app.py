import os

os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
os.environ["no_proxy"] = "localhost,127.0.0.1,::1"
os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"

import base64
import io
import math
import re
import tempfile
import urllib.parse
import urllib.request
import zipfile
from typing import List, Optional, Tuple

try:
    import cv2
    import numpy as np
    import pandas as pd
    from PIL import Image, ImageDraw, ImageOps
    from skimage.segmentation import find_boundaries, slic
    import streamlit as st
    import streamlit.components.v1 as components
except Exception as _imp_err:
    import glob
    import subprocess
    import sys
    import traceback

    if isinstance(_imp_err, ModuleNotFoundError) and os.name == "nt" and not os.environ.get("TOMATO_RELAUNCHED"):
        local_app = os.environ.get("LOCALAPPDATA", "")
        candidates = sorted(
            glob.glob(os.path.join(local_app, "Programs", "Python", "Python*", "python.exe")),
            reverse=True,
        )
        for py_exe in candidates:
            if os.path.normcase(py_exe) == os.path.normcase(sys.executable):
                continue
            chk = subprocess.run(
                [py_exe, "-c", "import streamlit, skimage, cv2"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if chk.returncode == 0:
                env = os.environ.copy()
                env["TOMATO_RELAUNCHED"] = "1"
                res = subprocess.run([py_exe, os.path.abspath(__file__)] + sys.argv[1:], env=env)
                sys.exit(res.returncode)

    print("起動エラーが発生しました。使用中のPython:", sys.executable)
    traceback.print_exc()
    input("\nEnterキーを押すと閉じます...")
    sys.exit(1)

try:
    import pillow_heif
except ImportError:
    pillow_heif = None

try:
    import rawpy
except ImportError:
    rawpy = None

# HEIC/HEIFサポートおよびデコンプレッションボム対策
if pillow_heif is not None:
    pillow_heif.register_heif_opener()
Image.MAX_IMAGE_PIXELS = 100_000_000

# 定数設定
DISPLAY_WIDTH = 600
BRUSH_DISPLAY_WIDTH = 460
WORK_MAX_SIDE = 1024
MAX_UPLOAD_BYTES = 100 * 1024 * 1024   # 100 MB
SAMPLE_TOMATO_NAME = "tomato_sample.jpg"

# トマト全体用・子房用のデフォルトHSV設定
DEFAULT_WHOLE_HSV = ((165, 22), (35, 255), (50, 255))
DEFAULT_LOCULE_HSV = ((10, 55), (40, 255), (20, 175))

COLOR_PRESETS = {
    "トマト全体（赤・ピンク）": ((165, 22), (35, 255), (50, 255)),
    "子房（暗緑・褐・ゼリー部）": ((10, 55), (40, 255), (20, 175)),
    "赤": ((170, 10), (70, 255), (50, 255)),
    "緑": ((35, 85), (40, 255), (40, 255)),
    "黄": ((18, 35), (70, 255), (70, 255)),
    "青": ((95, 130), (50, 255), (50, 255)),
    "白": ((0, 179), (0, 50), (200, 255)),
    "黒": ((0, 179), (0, 255), (0, 50)),
}

MASK_DISPLAY_COLORS = {
    "水色": (0, 200, 255),
    "黄": (255, 220, 0),
    "緑": (0, 220, 80),
    "赤": (255, 60, 0),
    "紫": (180, 60, 255),
}

# ------------------------------------------------------------
# 色相環カスタムコンポーネント (HTML5 Canvas ドラッグ対応)
# ------------------------------------------------------------
HUE_WHEEL_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<style>
  body {
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    font-family: sans-serif;
    background: transparent;
    user-select: none;
  }
  canvas {
    cursor: grab;
    touch-action: none;
  }
  canvas.dragging {
    cursor: grabbing;
  }
  .info {
    margin-top: 4px;
    font-size: 13px;
    color: #555;
  }
</style>
</head>
<body>
  <canvas id="wheel" width="240" height="240"></canvas>
  <div class="info" id="info">H: 165 ～ 22 (0～179)</div>
<script>
  const canvas = document.getElementById("wheel");
  const ctx = canvas.getContext("2d");
  const infoEl = document.getElementById("info");

  const cx = 120, cy = 120;
  const rOuter = 104, rInner = 64;
  let hMin = 165;
  let hMax = 22;
  let lastSyncId = -1;
  let dragging = null;
  let dragStartAngle = 0;
  let startHMin = 165, startHMax = 22;

  function sendMessage(type, data) {
    window.parent.postMessage(Object.assign({ isStreamlitMessage: true, type: type }, data), "*");
  }

  function hToAngle(h) {
    return (h / 180) * Math.PI * 2 - Math.PI / 2;
  }

  function angleToH(angle) {
    let deg = ((angle + Math.PI / 2) * 180 / Math.PI) % 360;
    if (deg < 0) deg += 360;
    return Math.round(deg / 2) % 180;
  }

  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    for (let deg = 0; deg < 360; deg++) {
      const a0 = (deg - 0.8) * Math.PI / 180 - Math.PI / 2;
      const a1 = (deg + 0.8) * Math.PI / 180 - Math.PI / 2;
      ctx.beginPath();
      ctx.arc(cx, cy, rOuter, a0, a1);
      ctx.arc(cx, cy, rInner, a1, a0, true);
      ctx.closePath();
      ctx.fillStyle = `hsl(${deg}, 100%, 50%)`;
      ctx.fill();
    }

    const aMin = hToAngle(hMin);
    const aMax = hToAngle(hMax);
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, rInner - 4, aMin, aMax, false);
    ctx.closePath();
    ctx.fillStyle = "rgba(0, 160, 255, 0.22)";
    ctx.fill();

    function drawHandle(h, color) {
      const a = hToAngle(h);
      const x2 = cx + Math.cos(a) * (rOuter + 4);
      const y2 = cy + Math.sin(a) * (rOuter + 4);

      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(x2, y2);
      ctx.strokeStyle = "#111";
      ctx.lineWidth = 3;
      ctx.stroke();
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 1.5;
      ctx.stroke();

      const hx = cx + Math.cos(a) * ((rOuter + rInner) / 2);
      const hy = cy + Math.sin(a) * ((rOuter + rInner) / 2);
      ctx.beginPath();
      ctx.arc(hx, hy, 8, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = "#111";
      ctx.stroke();
    }

    drawHandle(hMin, "#ffffff");
    drawHandle(hMax, "#ffeb3b");

    infoEl.textContent = `H (色相): ${hMin} ～ ${hMax}  (ドラッグで範囲調整)`;
  }

  function getPointerPos(e) {
    const rect = canvas.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  function handleDist(h, px, py) {
    const a = hToAngle(h);
    const hx = cx + Math.cos(a) * ((rOuter + rInner) / 2);
    const hy = cy + Math.sin(a) * ((rOuter + rInner) / 2);
    return Math.hypot(px - hx, py - hy);
  }

  canvas.addEventListener("pointerdown", (e) => {
    const { x, y } = getPointerPos(e);
    const dMin = handleDist(hMin, x, y);
    const dMax = handleDist(hMax, x, y);

    if (dMin < 18 && dMin <= dMax) {
      dragging = "min";
    } else if (dMax < 18) {
      dragging = "max";
    } else {
      const dist = Math.hypot(x - cx, y - cy);
      if (dist <= rOuter + 10) {
        dragging = "range";
        dragStartAngle = Math.atan2(y - cy, x - cx);
        startHMin = hMin;
        startHMax = hMax;
      }
    }
    if (dragging) {
      canvas.setPointerCapture(e.pointerId);
      canvas.classList.add("dragging");
    }
  });

  canvas.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const { x, y } = getPointerPos(e);
    const angle = Math.atan2(y - cy, x - cx);

    if (dragging === "min") {
      hMin = angleToH(angle);
    } else if (dragging === "max") {
      hMax = angleToH(angle);
    } else if (dragging === "range") {
      const diffRad = angle - dragStartAngle;
      const diffH = Math.round((diffRad / (Math.PI * 2)) * 180);
      hMin = ((startHMin + diffH) % 180 + 180) % 180;
      hMax = ((startHMax + diffH) % 180 + 180) % 180;
    }
    draw();
  });

  function endDrag() {
    if (!dragging) return;
    dragging = null;
    canvas.classList.remove("dragging");
    sendMessage("streamlit:setComponentValue", {
      value: { h_min: hMin, h_max: hMax },
      dataType: "json",
    });
  }

  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);

  window.addEventListener("message", (event) => {
    if (event.data && event.data.type === "streamlit:render") {
      const args = event.data.args || {};
      if (typeof args.sync_id === "number" && args.sync_id !== lastSyncId) {
        lastSyncId = args.sync_id;
        if (typeof args.h_min === "number") hMin = args.h_min;
        if (typeof args.h_max === "number") hMax = args.h_max;
        draw();
      }
      sendMessage("streamlit:setFrameHeight", { height: 270 });
    }
  });

  draw();
  sendMessage("streamlit:componentReady", { apiVersion: 1 });
  sendMessage("streamlit:setFrameHeight", { height: 270 });
</script>
</body>
</html>
"""


@st.cache_resource
def get_hue_wheel_component():
    comp_dir = tempfile.mkdtemp(prefix="st_tomato_hue_wheel_")
    os.chmod(comp_dir, 0o700)
    with open(os.path.join(comp_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(HUE_WHEEL_HTML)
    return components.declare_component("tomato_hue_wheel_v1", path=comp_dir)


# ------------------------------------------------------------
# ブラシ描画カスタムコンポーネント (HTML5 Canvas)
# ------------------------------------------------------------
BRUSH_CANVAS_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<style>
  body { margin: 0; padding: 0; background: transparent; user-select: none; }
  .wrap { position: relative; display: inline-block; width: 100%; }
  canvas {
    display: block;
    width: 100%;
    height: auto;
    cursor: crosshair;
    touch-action: none;
    border-radius: 4px;
  }
</style>
</head>
<body>
  <div class="wrap">
    <canvas id="c"></canvas>
  </div>
<script>
  const canvas = document.getElementById("c");
  const ctx = canvas.getContext("2d");

  let bgImg = new Image();
  let bgLoaded = false;
  let lastBgKey = "";
  let strokes = [];
  let currentStroke = null;
  let strokeColor = "#00FF88";
  let brushWidth = 15;

  function sendMessage(type, data) {
    window.parent.postMessage(Object.assign({ isStreamlitMessage: true, type: type }, data), "*");
  }

  function redraw() {
    if (!bgLoaded) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(bgImg, 0, 0, canvas.width, canvas.height);

    const all = currentStroke ? strokes.concat([currentStroke]) : strokes;
    for (const s of all) {
      if (!s.pts || s.pts.length === 0) continue;
      ctx.save();
      ctx.strokeStyle = s.color;
      ctx.fillStyle = s.color;
      ctx.lineWidth = s.width;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.globalAlpha = 0.75;
      if (s.pts.length === 1) {
        ctx.beginPath();
        ctx.arc(s.pts[0][0], s.pts[0][1], s.width / 2, 0, Math.PI * 2);
        ctx.fill();
      } else {
        ctx.beginPath();
        ctx.moveTo(s.pts[0][0], s.pts[0][1]);
        for (let i = 1; i < s.pts.length; i++) {
          ctx.lineTo(s.pts[i][0], s.pts[i][1]);
        }
        ctx.stroke();
      }
      ctx.restore();
    }
  }

  function updateHeight() {
    const rect = canvas.getBoundingClientRect();
    const h = Math.max(120, Math.round(rect.height || canvas.height || 320));
    sendMessage("streamlit:setFrameHeight", { height: h + 4 });
  }

  function getPos(e) {
    const rect = canvas.getBoundingClientRect();
    const sx = canvas.width / (rect.width || canvas.width);
    const sy = canvas.height / (rect.height || canvas.height);
    return [
      Math.max(0, Math.min(canvas.width - 1, (e.clientX - rect.left) * sx)),
      Math.max(0, Math.min(canvas.height - 1, (e.clientY - rect.top) * sy)),
    ];
  }

  canvas.addEventListener("pointerdown", (e) => {
    if (!bgLoaded) return;
    canvas.setPointerCapture(e.pointerId);
    currentStroke = { color: strokeColor, width: brushWidth, pts: [getPos(e)] };
    redraw();
  });

  canvas.addEventListener("pointermove", (e) => {
    if (!currentStroke) return;
    currentStroke.pts.push(getPos(e));
    redraw();
  });

  function finishStroke() {
    if (!currentStroke) return;
    strokes.push(currentStroke);
    currentStroke = null;
    redraw();
    sendMessage("streamlit:setComponentValue", {
      value: { strokes: strokes },
      dataType: "json",
    });
  }

  canvas.addEventListener("pointerup", finishStroke);
  canvas.addEventListener("pointercancel", finishStroke);

  window.addEventListener("message", (event) => {
    if (event.data && event.data.type === "streamlit:render") {
      const args = event.data.args || {};
      if (typeof args.stroke_color === "string") strokeColor = args.stroke_color;
      if (typeof args.brush_width === "number") brushWidth = args.brush_width;

      const bgKey = String(args.bg_key || "");
      if (bgKey !== lastBgKey && typeof args.bg_b64 === "string") {
        lastBgKey = bgKey;
        strokes = [];
        currentStroke = null;
        bgLoaded = false;
        bgImg = new Image();
        bgImg.onload = () => {
          canvas.width = args.width || bgImg.naturalWidth;
          canvas.height = args.height || bgImg.naturalHeight;
          bgLoaded = true;
          redraw();
          setTimeout(updateHeight, 30);
        };
        bgImg.src = "data:image/png;base64," + args.bg_b64;
      } else {
        redraw();
        setTimeout(updateHeight, 30);
      }
    }
  });

  window.addEventListener("resize", updateHeight);
  sendMessage("streamlit:componentReady", { apiVersion: 1 });
  sendMessage("streamlit:setFrameHeight", { height: 340 });
</script>
</body>
</html>
"""


@st.cache_resource
def get_brush_canvas_component():
    comp_dir = tempfile.mkdtemp(prefix="st_tomato_brush_canvas_")
    os.chmod(comp_dir, 0o700)
    with open(os.path.join(comp_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(BRUSH_CANVAS_HTML)
    return components.declare_component("tomato_brush_canvas_v1", path=comp_dir)


# ------------------------------------------------------------
# クリック座標取得カスタムコンポーネント (HTML5)
# ------------------------------------------------------------
CLICK_CANVAS_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<style>
  body { margin: 0; padding: 0; background: transparent; user-select: none; }
  img {
    display: block;
    width: 100%;
    height: auto;
    cursor: pointer;
    border-radius: 4px;
  }
</style>
</head>
<body>
  <img id="img" />
<script>
  const img = document.getElementById("img");
  let lastBgKey = "";

  function sendMessage(type, data) {
    window.parent.postMessage(Object.assign({ isStreamlitMessage: true, type: type }, data), "*");
  }

  function updateHeight() {
    const rect = img.getBoundingClientRect();
    const h = Math.max(120, Math.round(rect.height || img.naturalHeight || 320));
    sendMessage("streamlit:setFrameHeight", { height: h + 4 });
  }

  img.addEventListener("click", (e) => {
    const rect = img.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const x = Math.max(0, Math.min(rect.width - 1, e.clientX - rect.left));
    const y = Math.max(0, Math.min(rect.height - 1, e.clientY - rect.top));
    sendMessage("streamlit:setComponentValue", {
      value: { x: x, y: y, width: rect.width, height: rect.height, seq: Date.now() },
      dataType: "json",
    });
  });

  window.addEventListener("message", (event) => {
    if (event.data && event.data.type === "streamlit:render") {
      const args = event.data.args || {};
      const bgKey = String(args.bg_key || "");
      if (bgKey !== lastBgKey && typeof args.bg_b64 === "string") {
        lastBgKey = bgKey;
        img.onload = () => setTimeout(updateHeight, 30);
        img.src = "data:image/png;base64," + args.bg_b64;
      } else {
        setTimeout(updateHeight, 30);
      }
    }
  });

  window.addEventListener("resize", updateHeight);
  sendMessage("streamlit:componentReady", { apiVersion: 1 });
  sendMessage("streamlit:setFrameHeight", { height: 340 });
</script>
</body>
</html>
"""


@st.cache_resource
def get_click_canvas_component():
    comp_dir = tempfile.mkdtemp(prefix="st_tomato_click_canvas_")
    os.chmod(comp_dir, 0o700)
    with open(os.path.join(comp_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(CLICK_CANVAS_HTML)
    return components.declare_component("tomato_click_canvas_v1", path=comp_dir)


# ------------------------------------------------------------
# セキュリティ・ファイルI/Oユーティリティ
# ------------------------------------------------------------
def sanitize_filename(filename: str, default_stem: str = "tomato") -> str:
    base = os.path.basename(filename or "")
    stem = os.path.splitext(base)[0]
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", stem).strip("._-")
    return cleaned[:80] if cleaned else default_stem


def sanitize_csv_cell(val):
    """CSV数式インジェクション対策"""
    if isinstance(val, str) and val.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + val
    return val


@st.cache_data(show_spinner=False)
def load_sample_tomato_bytes() -> bytes:
    app_dir = os.path.dirname(os.path.abspath(__file__))
    candidate_paths = [
        os.path.abspath(os.path.join(app_dir, "sample", SAMPLE_TOMATO_NAME)),
        os.path.abspath(os.path.join(app_dir, SAMPLE_TOMATO_NAME)),
    ]
    for p in candidate_paths:
        if os.path.commonpath([app_dir, p]) == app_dir and os.path.isfile(p):
            if os.path.getsize(p) > MAX_UPLOAD_BYTES:
                raise ValueError("ファイルサイズが上限を超えています。")
            with open(p, "rb") as f:
                return f.read()
    raise FileNotFoundError(f"sample/{SAMPLE_TOMATO_NAME} が見つかりません。")


@st.cache_data(show_spinner=False)
def load_image_rgb(file_bytes: bytes, filename: str) -> np.ndarray:
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError("ファイルサイズが上限(100MB)を超えています。")

    ext = os.path.splitext(os.path.basename(filename))[1].lower()
    if ext == ".cr2":
        if rawpy is None:
            raise ImportError("CR2ファイルを読み込むには rawpy のインストールが必要です。")
        with tempfile.NamedTemporaryFile(suffix=".cr2", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            with rawpy.imread(tmp_path) as raw:
                return raw.postprocess(use_camera_wb=True, no_auto_bright=False, output_bps=8)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    with Image.open(io.BytesIO(file_bytes)) as raw_image:
        transposed = ImageOps.exif_transpose(raw_image)
        if transposed.mode in ["I", "I;16", "F"]:
            np_img = np.array(transposed)
            if np_img.max() > np_img.min():
                np_img = ((np_img - np_img.min()) / (np_img.max() - np_img.min()) * 255.0).astype(np.uint8)
            else:
                np_img = np.zeros_like(np_img, dtype=np.uint8)
            return np.array(Image.fromarray(np_img).convert("RGB"), dtype=np.uint8)
        return np.array(transposed.convert("RGB"), dtype=np.uint8)


# ------------------------------------------------------------
# 画像処理・マスク演算関数
# ------------------------------------------------------------
@st.cache_data(show_spinner=False)
def downscale_for_work(img_rgb: np.ndarray, max_side: int) -> np.ndarray:
    h, w = img_rgb.shape[:2]
    if max(h, w) <= max_side:
        return img_rgb
    scale = max_side / float(max(h, w))
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    return cv2.resize(img_rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)


@st.cache_data(show_spinner=False)
def compute_superpixels(
    img_rgb: np.ndarray,
    n_segments: int,
    compactness: float,
) -> Tuple[np.ndarray, np.ndarray]:
    segments = slic(
        img_rgb,
        n_segments=n_segments,
        compactness=compactness,
        sigma=1.0,
        start_label=0,
        channel_axis=-1,
    )
    boundaries = find_boundaries(segments, mode="inner")
    return segments.astype(np.int32), boundaries


def create_hsv_mask(
    img_rgb: np.ndarray,
    h_min: int,
    h_max: int,
    s_min: int,
    s_max: int,
    v_min: int,
    v_max: int,
) -> np.ndarray:
    hsv = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]

    if h_min <= h_max:
        h_mask = (h >= h_min) & (h <= h_max)
    else:
        h_mask = (h >= h_min) | (h <= h_max)

    sv_mask = (s >= s_min) & (s <= s_max) & (v >= v_min) & (v <= v_max)
    return h_mask & sv_mask


def fill_mask_holes(mask: np.ndarray) -> np.ndarray:
    """トマト全体の外周内部にある穴（子房など）を埋めて塊にする"""
    u8 = (mask.astype(np.uint8)) * 255
    contours, _ = cv2.findContours(u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(u8)
    cv2.drawContours(filled, contours, -1, 255, thickness=cv2.FILLED)
    return filled > 0


def render_overlay(
    img_rgb: np.ndarray,
    mask: np.ndarray,
    boundaries: Optional[np.ndarray],
    mask_color: Tuple[int, int, int] = (0, 200, 255),
    mask_alpha: float = 0.45,
    boundary_color: Tuple[int, int, int] = (255, 0, 0),
    show_boundaries: bool = True,
) -> np.ndarray:
    canvas = img_rgb.astype(np.float32).copy()
    if np.any(mask):
        color_arr = np.array(mask_color, dtype=np.float32)
        canvas[mask] = canvas[mask] * (1.0 - mask_alpha) + color_arr * mask_alpha
    out = np.clip(canvas, 0, 255).astype(np.uint8)
    if show_boundaries and boundaries is not None:
        out[boundaries] = boundary_color
    return out


def resize_for_display(img: np.ndarray, max_width: int) -> Tuple[np.ndarray, float]:
    h, w = img.shape[:2]
    if w <= max_width:
        return img, 1.0
    scale = max_width / float(w)
    new_h = int(round(h * scale))
    resized = cv2.resize(
        img,
        (max_width, new_h),
        interpolation=cv2.INTER_NEAREST if img.ndim == 2 else cv2.INTER_AREA,
    )
    return resized, scale


def encode_png(img_array: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(img_array).save(buf, format="PNG")
    return buf.getvalue()


def freedraw_strokes_to_mask(
    strokes: list,
    img_w: int,
    img_h: int,
    scale_x: float,
    scale_y: float,
    default_brush: int,
) -> np.ndarray:
    """ブラシコンポーネントのストロークデータからboolマスクを生成"""
    mask_img = Image.new("L", (img_w, img_h), 0)
    draw = ImageDraw.Draw(mask_img)

    for s in strokes[:500]:
        if not isinstance(s, dict):
            continue
        try:
            raw_sw = float(s.get("width", default_brush))
            if not math.isfinite(raw_sw):
                raw_sw = float(default_brush)
        except (TypeError, ValueError):
            raw_sw = float(default_brush)

        stroke_w = max(1, min(300, int(round(raw_sw * scale_x))))
        r = max(1, stroke_w // 2)
        pts = []
        for pt in (s.get("pts") or [])[:10000]:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                try:
                    fx, fy = float(pt[0]), float(pt[1])
                    if math.isfinite(fx) and math.isfinite(fy):
                        px = int(np.clip(round(fx * scale_x), 0, img_w - 1))
                        py = int(np.clip(round(fy * scale_y), 0, img_h - 1))
                        pts.append((px, py))
                except (TypeError, ValueError):
                    pass

        if len(pts) >= 2:
            draw.line(pts, fill=255, width=stroke_w)
            for px, py in pts:
                draw.ellipse([px - r, py - r, px + r, py + r], fill=255)
        elif len(pts) == 1:
            px, py = pts[0]
            draw.ellipse([px - r, py - r, px + r, py + r], fill=255)

    return np.array(mask_img, dtype=np.uint8) > 0


def find_non_overlapping_label_box(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    cx_pos: int,
    cy_pos: int,
    box_w: int,
    box_h: int,
    img_w: int,
    img_h: int,
    mask_all: np.ndarray,
    placed_boxes: List[Tuple[int, int, int, int]],
) -> Tuple[int, int, int, int]:
    """トマト領域および既配置ラベルと重ならない最適なラベル矩形を探索"""
    margins = [8, 20, 36, 56, 80]
    candidates = []

    for m in margins:
        raw_positions = [
            (cx_pos - box_w // 2, y1 - box_h - m),
            (cx_pos - box_w // 2, y2 + m),
            (x1 - box_w - m, cy_pos - box_h // 2),
            (x2 + m, cy_pos - box_h // 2),
            (x1 - box_w - m, y1 - box_h - m),
            (x2 + m, y1 - box_h - m),
            (x1 - box_w - m, y2 + m),
            (x2 + m, y2 + m),
        ]
        for lx, ly in raw_positions:
            lx1 = max(2, min(img_w - box_w - 2, lx))
            ly1 = max(2, min(img_h - box_h - 2, ly))
            lx2 = lx1 + box_w
            ly2 = ly1 + box_h

            label_overlap = 0
            for px1, py1, px2, py2 in placed_boxes:
                ox = max(0, min(lx2, px2) - max(lx1, px1))
                oy = max(0, min(ly2, py2) - max(ly1, py1))
                label_overlap += ox * oy

            mask_overlap = int(np.sum(mask_all[ly1:ly2, lx1:lx2]))
            lcx = (lx1 + lx2) / 2.0
            lcy = (ly1 + ly2) / 2.0
            dist = math.hypot(lcx - cx_pos, lcy - cy_pos)

            score = (mask_overlap * 1000.0) + (label_overlap * 500.0) + dist
            candidates.append((score, lx1, ly1, lx2, ly2))

    candidates.sort(key=lambda c: c[0])
    _, best_x1, best_y1, best_x2, best_y2 = candidates[0]
    return best_x1, best_y1, best_x2, best_y2


def init_state():
    defaults = {
        "file_key": None,
        "whole_mask": None,
        "locule_mask": None,
        "last_click": None,
        "whole_hsv": DEFAULT_WHOLE_HSV,
        "locule_hsv": DEFAULT_LOCULE_HSV,
        "last_preset_whole": "トマト全体（赤・ピンク）",
        "last_preset_locule": "子房（暗緑・褐・ゼリー部）",
        "wheel_sync_id": 0,
        "last_wheel_val": None,
        "sp_canvas_ver": 0,
        "sp_draw_objs": [],
        "sp_n_segments": 350,
        "sp_compactness": 10.0,
        "sp_show_boundaries": True,
        "last_hsv_target": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ------------------------------------------------------------
# タブ①: HSVでマスク作成（トマト全体 & 子房）
# ------------------------------------------------------------
def render_tab1_hsv(img_rgb: np.ndarray, file_key: str) -> Tuple[int, int, int]:
    img_h, img_w = img_rgb.shape[:2]

    # 初回画像読込時に両方の初期マスクを作成
    if st.session_state.file_key != file_key or st.session_state.whole_mask is None or st.session_state.whole_mask.shape != (img_h, img_w):
        st.session_state.file_key = file_key
        wh, ws, wv = st.session_state.whole_hsv
        lh, ls, lv = st.session_state.locule_hsv
        init_whole = fill_mask_holes(create_hsv_mask(img_rgb, wh[0], wh[1], ws[0], ws[1], wv[0], wv[1]))
        init_locule = create_hsv_mask(img_rgb, lh[0], lh[1], ls[0], ls[1], lv[0], lv[1]) & init_whole
        st.session_state.whole_mask = init_whole
        st.session_state.locule_mask = init_locule
        st.session_state.last_click = None
        st.session_state.sp_canvas_ver += 1
        st.session_state.sp_draw_objs = []

    col1_ctrl, col1_view = st.columns([1, 2])

    with col1_ctrl:
        st.subheader("設定")
        target = st.radio(
            "作成するマスクの対象",
            ["トマト全体", "子房（ゼリー部）"],
            horizontal=True,
            key="tab1_target",
        )
        is_whole = target == "トマト全体"

        if st.session_state.last_hsv_target != target:
            st.session_state.last_hsv_target = target
            cur_h, cur_s, cur_v = st.session_state.whole_hsv if is_whole else st.session_state.locule_hsv
            st.session_state.tab1_s_range = cur_s
            st.session_state.tab1_v_range = cur_v
            st.session_state.wheel_sync_id += 1

        preset_keys = list(COLOR_PRESETS.keys())
        default_p = st.session_state.last_preset_whole if is_whole else st.session_state.last_preset_locule
        preset = st.selectbox(
            "色プリセット",
            preset_keys,
            index=preset_keys.index(default_p) if default_p in preset_keys else 0,
            key=f"preset_{target}",
        )

        last_preset_key = "last_preset_whole" if is_whole else "last_preset_locule"
        if preset != st.session_state[last_preset_key]:
            st.session_state[last_preset_key] = preset
            (h0, h1), s_def, v_def = COLOR_PRESETS[preset]
            if is_whole:
                st.session_state.whole_hsv = ((h0, h1), s_def, v_def)
            else:
                st.session_state.locule_hsv = ((h0, h1), s_def, v_def)
            st.session_state.tab1_s_range = s_def
            st.session_state.tab1_v_range = v_def
            st.session_state.wheel_sync_id += 1

        mask_color_name = st.selectbox("マスクの色", list(MASK_DISPLAY_COLORS.keys()))
        selected_mask_color = MASK_DISPLAY_COLORS[mask_color_name]

        cur_h, _, _ = st.session_state.whole_hsv if is_whole else st.session_state.locule_hsv
        st.markdown("**H (色相) 範囲**（色相環のハンドルまたは範囲をドラッグ）")
        wheel_val = get_hue_wheel_component()(
            h_min=cur_h[0],
            h_max=cur_h[1],
            sync_id=st.session_state.wheel_sync_id,
            key="tomato_hue_wheel_widget",
        )
        if (
            isinstance(wheel_val, dict)
            and "h_min" in wheel_val
            and "h_max" in wheel_val
            and wheel_val != st.session_state.last_wheel_val
        ):
            st.session_state.last_wheel_val = wheel_val
            new_h = (int(np.clip(int(wheel_val["h_min"]), 0, 179)), int(np.clip(int(wheel_val["h_max"]), 0, 179)))
            if is_whole:
                _, s_cur, v_cur = st.session_state.whole_hsv
                st.session_state.whole_hsv = (new_h, s_cur, v_cur)
            else:
                _, s_cur, v_cur = st.session_state.locule_hsv
                st.session_state.locule_hsv = (new_h, s_cur, v_cur)

        s_min, s_max = st.slider("S (彩度) 範囲", 0, 255, key="tab1_s_range")
        v_min, v_max = st.slider("V (明度) 範囲", 0, 255, key="tab1_v_range")

        if is_whole:
            h_cur, _, _ = st.session_state.whole_hsv
            st.session_state.whole_hsv = (h_cur, (s_min, s_max), (v_min, v_max))
            fill_holes_opt = st.checkbox("トマト内部の穴（子房部など）を自動で埋める", value=True)
            hsv_mask = create_hsv_mask(img_rgb, h_cur[0], h_cur[1], s_min, s_max, v_min, v_max)
            if fill_holes_opt:
                hsv_mask = fill_mask_holes(hsv_mask)
        else:
            h_cur, _, _ = st.session_state.locule_hsv
            st.session_state.locule_hsv = (h_cur, (s_min, s_max), (v_min, v_max))
            limit_inside_whole = st.checkbox("「トマト全体」マスクの内側のみに限定する", value=True)
            hsv_mask = create_hsv_mask(img_rgb, h_cur[0], h_cur[1], s_min, s_max, v_min, v_max)
            if limit_inside_whole and st.session_state.whole_mask is not None:
                hsv_mask = hsv_mask & st.session_state.whole_mask

    with col1_view:
        st.subheader("プレビュー")
        prev_col1, prev_col2 = st.columns(2)
        with prev_col1:
            hsv_preview = render_overlay(
                img_rgb,
                hsv_mask,
                None,
                mask_color=selected_mask_color,
                mask_alpha=0.5,
                show_boundaries=False,
            )
            st.image(hsv_preview, caption=f"元画像 + {target}マスク（{mask_color_name}）", use_container_width=True)
        with prev_col2:
            st.image((hsv_mask.astype(np.uint8)) * 255, caption=f"{target}マスク（白黒）", use_container_width=True)

        if st.button(f"この「{target}」HSVマスクを②に反映", type="primary", use_container_width=True):
            if is_whole:
                st.session_state.whole_mask = hsv_mask.copy()
            else:
                st.session_state.locule_mask = hsv_mask.copy()
            st.session_state.sp_canvas_ver += 1
            st.session_state.sp_draw_objs = []
            st.success(f"「{target}」のHSVマスクを「②スーパーピクセルで修正」に反映しました。")

    return selected_mask_color


# ------------------------------------------------------------
# タブ②: スーパーピクセルで修正（トマト全体 & 子房）
# ------------------------------------------------------------
def render_tab2_superpixel(
    img_rgb: np.ndarray,
    selected_mask_color: Tuple[int, int, int],
    export_whole_mask: np.ndarray,
    export_locule_mask: np.ndarray,
    safe_base_name: str,
) -> None:
    img_h, img_w = img_rgb.shape[:2]
    col2_ctrl, col2_main, col2_mask = st.columns([1, 1.8, 1.8])
    apply_brush_clicked = False

    with col2_ctrl:
        st.subheader("設定")
        edit_target = st.radio(
            "修正するマスク",
            ["トマト全体", "子房（ゼリー部）"],
            horizontal=True,
            key="tab2_target",
        )
        is_whole = edit_target == "トマト全体"
        active_mask = st.session_state.whole_mask if is_whole else st.session_state.locule_mask

        edit_method = st.selectbox(
            "修正方法",
            ["スーパーピクセルをクリック（反転）", "直接描いて修正（ブラシ）"],
        )
        show_boundaries = st.checkbox("スーパーピクセル境界線を表示", key="sp_show_boundaries")

        st.divider()
        if edit_method == "スーパーピクセルをクリック（反転）":
            st.markdown("**スーパーピクセルの操作**")
            n_segments = st.slider(
                "スーパーピクセルの粒度（分割数）",
                min_value=50,
                max_value=1500,
                step=10,
                key="sp_n_segments",
                help="数値が大きいほどスーパーピクセルが細かくなります。",
            )
            compactness = st.slider(
                "コンパクトネス（形状の均一さ）",
                min_value=1.0,
                max_value=40.0,
                step=1.0,
                key="sp_compactness",
            )
            brush_radius = st.slider(
                "まとめてピクセルを選択",
                min_value=0,
                max_value=100,
                value=0,
                step=5,
                help="0にするとクリックした1つのスーパーピクセルのみを反転します。",
            )
        else:
            n_segments = st.session_state.sp_n_segments
            compactness = st.session_state.sp_compactness

            st.markdown("**ブラシの操作**")
            brush_action = st.radio("ブラシ操作", ["追加", "消去"], horizontal=True)
            brush_size = st.slider("ブラシの大きさ (px)", min_value=3, max_value=60, value=15, step=1)
            snap_to_sp = st.checkbox("描いた部分をスーパーピクセル単位に合わせる", value=False)
            if st.button("描画レイヤーをクリア", use_container_width=True):
                st.session_state.sp_canvas_ver += 1
                st.session_state.sp_draw_objs = []
                st.rerun()
            apply_brush_clicked = st.button("編集画面に適用", type="primary", use_container_width=True)

        st.divider()
        dl_u8 = (export_whole_mask if is_whole else export_locule_mask).astype(np.uint8) * 255
        dl_suffix = "tomato_mask" if is_whole else "locule_mask"
        st.download_button(
            label=f"{edit_target}マスクをダウンロード (PNG)",
            data=encode_png(dl_u8),
            file_name=f"{safe_base_name}_{dl_suffix}.png",
            mime="image/png",
            type="primary",
            use_container_width=True,
        )

    with st.spinner("スーパーピクセルを計算中..."):
        segments, boundaries = compute_superpixels(img_rgb, n_segments, compactness)

    with col2_main:
        st.subheader(f"編集画面（{edit_target}）")
        overlay_img = render_overlay(
            img_rgb,
            active_mask,
            boundaries,
            mask_color=selected_mask_color,
            show_boundaries=show_boundaries,
        )
        disp_img, _ = resize_for_display(overlay_img, BRUSH_DISPLAY_WIDTH)
        disp_h, disp_w = disp_img.shape[:2]

        if edit_method == "直接描いて修正（ブラシ）":
            stroke_col = "#00FF88" if brush_action == "追加" else "#FF3333"
            bg_b64 = base64.b64encode(encode_png(disp_img)).decode("ascii")
            bg_key = f"{edit_target}_{st.session_state.sp_canvas_ver}_{n_segments}_{int(show_boundaries)}_{selected_mask_color}"

            brush_res = get_brush_canvas_component()(
                bg_b64=bg_b64,
                bg_key=bg_key,
                width=int(disp_w),
                height=int(disp_h),
                stroke_color=stroke_col,
                brush_width=int(brush_size),
                key=f"tomato_brush_{edit_target}_{st.session_state.sp_canvas_ver}",
            )

            if isinstance(brush_res, dict) and isinstance(brush_res.get("strokes"), list):
                st.session_state.sp_draw_objs = brush_res["strokes"]

            if apply_brush_clicked:
                strokes = st.session_state.get("sp_draw_objs", [])
                if len(strokes) > 0:
                    changed = False
                    for s in strokes:
                        stroke_work = freedraw_strokes_to_mask(
                            [s],
                            img_w,
                            img_h,
                            img_w / float(disp_w),
                            img_h / float(disp_h),
                            brush_size,
                        )
                        if not np.any(stroke_work):
                            continue
                        if snap_to_sp:
                            touched_labels = np.unique(segments[stroke_work])
                            target_region = np.isin(segments, touched_labels)
                        else:
                            target_region = stroke_work

                        obj_col = str(s.get("color", "")).upper()
                        val = True if obj_col == "#00FF88" else (False if obj_col == "#FF3333" else (brush_action == "追加"))
                        active_mask[target_region] = val
                        changed = True

                    if changed:
                        st.session_state.sp_canvas_ver += 1
                        st.session_state.sp_draw_objs = []
                        st.rerun()
                    else:
                        st.warning("描画領域が取得できませんでした。もう一度描画してお試しください。")
                else:
                    st.warning("編集画面にブラシで描画してから「編集画面に適用」を押してください。")
        else:
            click_b64 = base64.b64encode(encode_png(disp_img)).decode("ascii")
            click_key = f"{edit_target}_{st.session_state.sp_canvas_ver}_{n_segments}_{compactness}_{int(show_boundaries)}_{selected_mask_color}_{int(np.sum(active_mask))}"
            coords = get_click_canvas_component()(
                bg_b64=click_b64,
                bg_key=click_key,
                key=f"tomato_click_{edit_target}",
            )
            if isinstance(coords, dict) and "x" in coords and "y" in coords:
                click_sig = coords.get("seq", (coords["x"], coords["y"]))
                if st.session_state.last_click != click_sig:
                    st.session_state.last_click = click_sig

                    rendered_w = max(1, int(coords.get("width", disp_w)))
                    rendered_h = max(1, int(coords.get("height", disp_h)))
                    orig_x = int(np.clip(round(coords["x"] * img_w / rendered_w), 0, img_w - 1))
                    orig_y = int(np.clip(round(coords["y"] * img_h / rendered_h), 0, img_h - 1))

                    if brush_radius == 0:
                        target_region = segments == segments[orig_y, orig_x]
                    else:
                        yy, xx = np.ogrid[:img_h, :img_w]
                        circle = (xx - orig_x) ** 2 + (yy - orig_y) ** 2 <= brush_radius ** 2
                        target_labels = np.unique(segments[circle])
                        target_region = np.isin(segments, target_labels)

                    center_val = active_mask[orig_y, orig_x]
                    active_mask[target_region] = not center_val
                    st.rerun()

    with col2_mask:
        st.subheader(f"マスク画像（白黒：{edit_target}）")
        st.image((active_mask.astype(np.uint8)) * 255, use_container_width=True)


# ------------------------------------------------------------
# タブ③: トマト子房比率解析 & ZIPダウンロード
# ------------------------------------------------------------
def render_tab3_locule_analysis(
    img_full_rgb: np.ndarray,
    export_whole_mask: np.ndarray,
    export_locule_mask: np.ndarray,
) -> None:
    st.subheader("🍅 トマトの子房比率解析")

    up_col1, up_col2 = st.columns(2)
    with up_col1:
        whole_uploaded = st.file_uploader(
            "トマト全体マスクをアップロード（任意・白黒 .png / .jpg / .tif）",
            type=["png", "jpg", "jpeg", "tif", "tiff"],
            key="whole_mask_uploader",
            help="未アップロードの場合はタブ①・②で作成したトマト全体マスクを使用します。",
        )
    with up_col2:
        locule_uploaded = st.file_uploader(
            "子房マスクをアップロード（任意・白黒 .png / .jpg / .tif）",
            type=["png", "jpg", "jpeg", "tif", "tiff"],
            key="locule_mask_uploader",
            help="未アップロードの場合はタブ①・②で作成した子房マスクを使用します。",
        )

    orig_h, orig_w = img_full_rgb.shape[:2]

    def load_uploaded_mask(uploaded, fallback_mask: np.ndarray) -> np.ndarray:
        if uploaded is not None:
            m_rgb = load_image_rgb(uploaded.getvalue(), uploaded.name)
            m_gray = cv2.cvtColor(m_rgb, cv2.COLOR_RGB2GRAY)
            if m_gray.shape != (orig_h, orig_w):
                m_gray = cv2.resize(m_gray, (orig_w, orig_h), interpolation=cv2.INTER_NEAREST)
            return m_gray > (127 if m_gray.max() > 1 else 0)
        return fallback_mask

    try:
        whole_mask = load_uploaded_mask(whole_uploaded, export_whole_mask)
        locule_mask = load_uploaded_mask(locule_uploaded, export_locule_mask)
    except Exception:
        st.error("マスク画像の読み込みに失敗しました。ファイル形式やサイズをご確認ください。")
        return

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        min_tomato_area = st.slider(
            "トマトとして検出する最小面積 (px)",
            min_value=100,
            max_value=100000,
            value=2000,
            step=100,
        )
    with col_s2:
        erode_size = st.slider(
            "隣接トマトの分離（収縮カーネルサイズ）",
            min_value=0,
            max_value=21,
            value=0,
            step=2,
            help="トマト同士がくっついている場合に大きくすると分離しやすくなります。",
        )

    seg_input = whole_mask.astype(np.uint8) * 255
    if erode_size >= 3:
        kernel = np.ones((erode_size, erode_size), np.uint8)
        seg_input = cv2.morphologyEx(seg_input, cv2.MORPH_OPEN, kernel)

    num_labels, labels_map, stats, _ = cv2.connectedComponentsWithStats(seg_input)

    raw_components = []
    heights = []
    for lbl in range(1, num_labels):
        if int(stats[lbl, cv2.CC_STAT_AREA]) >= min_tomato_area:
            pmask = (labels_map == lbl) & whole_mask
            ys, xs = np.where(pmask)
            if len(xs) == 0:
                continue
            y1, y2 = int(ys.min()), int(ys.max()) + 1
            cx_pos = int(np.mean(xs))
            cy_pos = int(np.mean(ys))
            heights.append(y2 - y1)
            raw_components.append((cy_pos, cx_pos, pmask))

    if raw_components:
        row_tol = max(40, int(np.median(heights) * 0.6))
        raw_components.sort(key=lambda item: (item[0] // row_tol, item[1]))

    palette = [
        (0, 220, 80),
        (0, 200, 255),
        (255, 165, 0),
        (255, 0, 255),
        (100, 255, 180),
    ]

    st.divider()
    st.subheader("抽出・解析結果 ＆ ZIPダウンロード")

    annotated_arr = img_full_rgb.copy()
    results = []
    placed_label_boxes: List[Tuple[int, int, int, int]] = []
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for idx, (_, _, pmask) in enumerate(raw_components, start=1):
            tomato_id = f"Tomato_{idx:03d}"
            color_rgb = palette[(idx - 1) % len(palette)]
            loc_in_tomato = pmask & locule_mask

            ys, xs = np.where(pmask)
            x1, x2 = int(xs.min()), int(xs.max()) + 1
            y1, y2 = int(ys.min()), int(ys.max()) + 1

            # トマト全体輪郭（外枠）と子房領域（緑色半透明）を描画
            contours, _ = cv2.findContours(pmask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(annotated_arr, contours, -1, color_rgb, max(3, orig_w // 350))

            if np.any(loc_in_tomato):
                annotated_arr[loc_in_tomato] = (
                    annotated_arr[loc_in_tomato].astype(np.float32) * 0.45
                    + np.array([0, 220, 80], dtype=np.float32) * 0.55
                ).astype(np.uint8)

            whole_pixels = int(np.sum(pmask))
            locule_pixels = int(np.sum(loc_in_tomato))
            locule_ratio = round((locule_pixels / whole_pixels) * 100.0, 2) if whole_pixels > 0 else 0.0

            label_text = f"{tomato_id} ({locule_ratio:.1f}%)"
            cx_pos = int(np.mean(xs))
            cy_pos = int(np.mean(ys))

            font_scale = max(0.55, orig_w / 1200.0)
            thickness = max(2, int(round(font_scale * 2)))
            (tw, th), baseline = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
            pad = 6
            box_w = tw + pad * 2
            box_h = th + baseline + pad * 2

            lx1, ly1, lx2, ly2 = find_non_overlapping_label_box(
                x1, y1, x2, y2,
                cx_pos, cy_pos,
                box_w, box_h,
                orig_w, orig_h,
                whole_mask,
                placed_label_boxes,
            )
            placed_label_boxes.append((lx1, ly1, lx2, ly2))

            lcx = (lx1 + lx2) // 2
            lcy = (ly1 + ly2) // 2
            cv2.line(annotated_arr, (lcx, lcy), (cx_pos, cy_pos), color_rgb, max(1, thickness - 1), cv2.LINE_AA)
            cv2.rectangle(annotated_arr, (lx1, ly1), (lx2, ly2), (20, 20, 20), -1)
            cv2.rectangle(annotated_arr, (lx1, ly1), (lx2, ly2), color_rgb, max(1, thickness - 1))
            cv2.putText(
                annotated_arr,
                label_text,
                (lx1 + pad, ly1 + pad + th),
                cv2.FONT_HERSHEY_SIMPLEX,
                font_scale,
                (255, 255, 255),
                thickness,
                cv2.LINE_AA,
            )

            masked_full = np.zeros_like(img_full_rgb)
            masked_full[pmask] = img_full_rgb[pmask]
            crop_byte_arr = io.BytesIO()
            Image.fromarray(masked_full[y1:y2, x1:x2]).save(crop_byte_arr, format="PNG")
            zip_file.writestr(f"{tomato_id}.png", crop_byte_arr.getvalue())

            results.append({
                "トマトID": sanitize_csv_cell(tomato_id),
                "トマト全体面積(px)": whole_pixels,
                "子房面積(px)": locule_pixels,
                "子房比率(%)": locule_ratio,
            })

        annotated_byte_arr = io.BytesIO()
        Image.fromarray(annotated_arr).save(annotated_byte_arr, format="PNG")
        zip_file.writestr("annotated_full_image.png", annotated_byte_arr.getvalue())

        if len(results) > 0:
            df_csv = pd.DataFrame(results)
            zip_file.writestr("tomato_locule_analysis_results.csv", df_csv.to_csv(index=False).encode("utf-8-sig"))

    valid_count = len(results)
    st.image(annotated_arr, caption=f"トマト子房セグメンテーション結果（検出数: {valid_count} 個）", use_container_width=True)

    if valid_count > 0:
        st.success(f"✅ 検出されたトマトの数: **{valid_count} 個**")
        st.dataframe(pd.DataFrame(results), use_container_width=True)
    else:
        st.info("トマトが検出されませんでした。最小面積やマスクを調整してください。")

    zip_buffer.seek(0)
    st.download_button(
        label=f"📦 全体画像・CSV・個別トマト画像（{valid_count}枚）をZIPでダウンロード",
        data=zip_buffer,
        file_name="tomato_locule_analysis_results.zip",
        mime="application/zip",
        type="primary",
        key="download_tomato_zip_btn",
        use_container_width=True,
    )


# ------------------------------------------------------------
# メインエントリポイント
# ------------------------------------------------------------
def main():
    st.set_page_config(
        page_title="トマト輪切り 子房比率解析アプリ",
        page_icon="🍅",
        layout="wide",
    )

    init_state()

    st.title("🍅 トマト輪切り 子房比率解析＆セグメンテーションアプリ")
    st.caption(
        "①HSVと②スーパーピクセル／ブラシで「トマト全体」と「子房（ゼリー部）」のマスクを作成・修正し、③個体ごとの子房比率（％）を算出します。"
    )

    image_source = st.radio(
        "画像の選択方法",
        [
            "ファイルをアップロード",
            f"サンプル画像 ({SAMPLE_TOMATO_NAME}) を使用",
        ],
        horizontal=True,
    )

    if image_source == "ファイルをアップロード":
        uploaded_file = st.file_uploader(
            "輪切りトマト画像をアップロード (.png / .jpg / .jpeg / .tif / .tiff / .heic / .cr2)",
            type=["png", "jpg", "jpeg", "tif", "tiff", "heic", "heif", "cr2"],
        )
        if uploaded_file is None:
            st.info("上のアップローダーからトマトの輪切り画像を選択するか、サンプル画像を選んでください。")
            st.stop()
        file_bytes = uploaded_file.getvalue()
        file_name = uploaded_file.name
    else:
        try:
            with st.spinner("サンプル画像を読み込んでいます..."):
                file_bytes = load_sample_tomato_bytes()
                file_name = SAMPLE_TOMATO_NAME
        except Exception:
            st.error(f"サンプル画像を読み込めませんでした。app.py と同じフォルダ（または sample フォルダ）に {SAMPLE_TOMATO_NAME} を配置してください。")
            st.stop()
    file_key = f"{os.path.basename(file_name)}_{len(file_bytes)}"

    try:
        with st.spinner("画像を読み込んでいます..."):
            img_full_rgb = load_image_rgb(file_bytes, file_name)
    except Exception:
        st.error("画像の読み込みに失敗しました。ファイル形式やサイズをご確認ください。")
        st.stop()

    orig_h, orig_w = img_full_rgb.shape[:2]
    img_rgb = downscale_for_work(img_full_rgb, WORK_MAX_SIDE) if max(orig_h, orig_w) > WORK_MAX_SIDE else img_full_rgb
    img_h, img_w = img_rgb.shape[:2]

    tab1, tab2, tab3 = st.tabs([
        "① HSVでマスク作成",
        "②スーパーピクセルで修正",
        "③ 子房比率解析・ダウンロード",
    ])

    with tab1:
        selected_mask_color = render_tab1_hsv(img_rgb, file_key)

    def to_orig_mask(m: np.ndarray) -> np.ndarray:
        if (img_h, img_w) != (orig_h, orig_w):
            return cv2.resize(m.astype(np.uint8), (orig_w, orig_h), interpolation=cv2.INTER_NEAREST).astype(bool)
        return m

    export_whole_mask = to_orig_mask(st.session_state.whole_mask)
    export_locule_mask = to_orig_mask(st.session_state.locule_mask)
    safe_base_name = sanitize_filename(file_name)

    with tab2:
        render_tab2_superpixel(
            img_rgb,
            selected_mask_color,
            export_whole_mask,
            export_locule_mask,
            safe_base_name,
        )

    with tab3:
        render_tab3_locule_analysis(img_full_rgb, export_whole_mask, export_locule_mask)


if __name__ == "__main__":
    if st.runtime.exists():
        main()
    else:
        import sys
        import threading
        import webbrowser
        from streamlit.web import cli as stcli

        os.chdir(os.path.dirname(os.path.abspath(__file__)))

        cred_dir = os.path.join(os.path.expanduser("~"), ".streamlit")
        cred_path = os.path.join(cred_dir, "credentials.toml")
        if not os.path.exists(cred_path):
            try:
                os.makedirs(cred_dir, exist_ok=True)
                with open(cred_path, "w", encoding="utf-8") as cf:
                    cf.write('[general]\nemail = ""\n')
            except OSError:
                pass

        threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8501")).start()

        sys.argv = [
            "streamlit",
            "run",
            os.path.abspath(__file__),
            "--server.address=127.0.0.1",
            "--server.port=8501",
            "--server.headless=true",
            "--browser.gatherUsageStats=false",
        ]
        try:
            sys.exit(stcli.main())
        except SystemExit as e:
            if e.code not in (0, None):
                input("エラーが発生しました。Enterキーを押すと閉じます...")
            raise
        except Exception:
            import traceback
            traceback.print_exc()
            input("エラーが発生しました。Enterキーを押すと閉じます...")
