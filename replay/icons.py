from PIL import Image, ImageDraw

ACCENT = (118, 185, 0)


def app_icon(size=64, state="recording"):
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=s // 4, fill=(28, 30, 34, 255))
    if state == "paused":
        w = s // 8
        d.rectangle((s * 0.32, s * 0.28, s * 0.32 + w, s * 0.72), fill=(210, 210, 210, 255))
        d.rectangle((s * 0.56, s * 0.28, s * 0.56 + w, s * 0.72), fill=(210, 210, 210, 255))
    else:
        color = (220, 60, 60, 255) if state == "error" else ACCENT + (255,)
        # круговая стрелка «повтор» + точка записи
        m = s * 0.16
        d.arc((m, m, s - m, s - m), start=-60, end=250, fill=color, width=max(2, s // 10))
        ax, ay = s * 0.80, s * 0.30
        d.polygon([(ax - s * 0.14, ay - s * 0.04), (ax + s * 0.04, ay - s * 0.08), (ax - s * 0.02, ay + s * 0.12)],
                  fill=color)
        r = s * 0.12
        d.ellipse((s / 2 - r, s / 2 - r, s / 2 + r, s / 2 + r), fill=color)
    return img


def save_ico(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    app_icon(256).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return path
