"""生成小鹿的入口二维码。

二维码指向线上入口（含访问码），因此**每次重新发布后，扫码看到的都是最新版本**，
不需要重新生成二维码；只有更换域名时才需要重跑本脚本。

用法（不要把访问码写进仓库文本，用环境变量或参数传入）：

    XIAOLU_URL="https://<域名>/app.html?sharecode=<访问码>" \
        python scripts/make_qr.py

产物：
    assets/qr-xiaolu.png        纯二维码（1024×1024，适合打印/贴纸）
    assets/qr-xiaolu-card.png   带标题和步骤的卡片（1240×1580，适合 README/海报）
"""

import argparse
import os
import sys
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont
from qrcode.constants import ERROR_CORRECT_Q

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

PAPER = (247, 249, 248)
INK = (20, 26, 27)
MUTED = (108, 120, 122)
ACCENT = (15, 110, 99)
LINE = (221, 227, 225)
WHITE = (255, 255, 255)

FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"
FONT_REG = "C:/Windows/Fonts/msyh.ttc"
FONT_MONO = "C:/Windows/Fonts/consola.ttf"


def font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def make_qr(url: str, size: int, border: int = 4) -> Image.Image:
    """生成纯二维码，再用 NEAREST 放大到目标尺寸，边缘保持锐利。"""
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_Q, box_size=8, border=border)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color=INK, back_color=WHITE).convert("RGB")
    return img.resize((size, size), Image.NEAREST)


def centered(draw: ImageDraw.ImageDraw, box, text, fnt, fill):
    x0, y0, x1, y1 = box
    left, top, right, bottom = draw.textbbox((0, 0), text, font=fnt)
    draw.text((x0 + (x1 - x0 - (right - left)) / 2 - left,
               y0 + (y1 - y0 - (bottom - top)) / 2 - top), text, font=fnt, fill=fill)


def build_plain(url: str) -> Image.Image:
    size = 1024
    qr = make_qr(url, size - 48)
    canvas = Image.new("RGB", (size, size), WHITE)
    canvas.paste(qr, (24, 24))
    return canvas


def build_card(url: str) -> Image.Image:
    w, h = 1240, 1580
    img = Image.new("RGB", (w, h), PAPER)
    d = ImageDraw.Draw(img)

    # 顶部一条主题色
    d.rectangle([0, 0, w, 12], fill=ACCENT)

    # 这行混排中英文，必须用中文字体，否则「小鹿」会变成方框
    d.text((90, 86), "XIAOLU  /  小鹿", font=font(FONT_BOLD, 22), fill=MUTED)
    d.text((90, 132), "扫码使用小鹿", font=font(FONT_BOLD, 58), fill=INK)
    d.text((90, 214), "个人病历档案 · 不用安装，打开就能记录和询问", font=font(FONT_REG, 26), fill=MUTED)

    # 二维码
    qr_size = 1000
    qr = make_qr(url, qr_size)
    pad = 30
    x = (w - (qr_size + pad * 2)) // 2
    y = 300
    d.rounded_rectangle([x, y, x + qr_size + pad * 2, y + qr_size + pad * 2],
                        radius=10, fill=WHITE, outline=LINE, width=2)
    img.paste(qr, (x + pad, y + pad))

    # 三步说明
    steps_y = y + qr_size + pad * 2 + 56
    d.text((90, steps_y), "① 扫码打开", font=font(FONT_BOLD, 26), fill=INK)
    d.text((370, steps_y), "② 登录或注册", font=font(FONT_BOLD, 26), fill=INK)
    d.text((690, steps_y), "③ 开始记录", font=font(FONT_BOLD, 26), fill=INK)
    d.text((90, steps_y + 46), "链接固定不变，每次发布新版本后扫码即最新版本。",
           font=font(FONT_REG, 20), fill=MUTED)

    d.text((90, h - 74), "只做病历记录，不构成诊疗建议。",
           font=font(FONT_REG, 18), fill=MUTED)
    centered(d, (w - 210, h - 74, w - 90, h - 34),
             "小鹿", font(FONT_BOLD, 22), ACCENT)
    return img


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("XIAOLU_URL", ""))
    args = ap.parse_args()
    url = (args.url or "").strip()
    if not url.startswith("http"):
        print("请用 --url 或环境变量 XIAOLU_URL 传入线上入口", file=sys.stderr)
        return 2

    ASSETS.mkdir(parents=True, exist_ok=True)
    plain = build_plain(url)
    card = build_card(url)
    plain.save(ASSETS / "qr-xiaolu.png", optimize=True)
    card.save(ASSETS / "qr-xiaolu-card.png", optimize=True)
    print("saved:", ASSETS / "qr-xiaolu.png", plain.size)
    print("saved:", ASSETS / "qr-xiaolu-card.png", card.size)
    print("url:", url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
