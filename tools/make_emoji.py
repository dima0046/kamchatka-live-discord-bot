"""Рисует пиксельные эмодзи пульта в bot/assets/emoji: сетка 16x16, увеличение x8 до 128x128, цвета логотипа.

Запуск: python tools/make_emoji.py bot/assets/emoji  (нужен Pillow: pip install pillow).
При следующем запуске бот сам заменит в Discord эмодзи, картинки которых поменялись.
"""
import math, sys
from pathlib import Path
from PIL import Image

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
PAL = {
    "M": "#FF2BD6", "m": "#A41CC4", "P": "#FF9CEF", "C": "#19E6FF", "c": "#1A8CFF", "B": "#2B4BFF",
    "V": "#8A4DFF", "v": "#3D1A8C", "W": "#FFFFFF", "Y": "#FFE14D", "O": "#FF8A1F", "L": "#8CFF3A",
    "G": "#2FBF4F", "R": "#FF2B6E", "D": "#160A30",
}
N, SCALE = 16, 8

def rgba(ch, a=255):
    h = PAL[ch].lstrip("#"); return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (a,)

def blank(): return [["." for _ in range(N)] for _ in range(N)]

def to_image(grid, glow=True):
    """Сетка -> 128x128 RGBA. Неоновое свечение: полупрозрачный ореол в 1 пиксель вокруг фигуры."""
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0)); px = img.load()
    for y in range(N):
        for x in range(N):
            if grid[y][x] != ".": px[x, y] = rgba(grid[y][x])
    if glow:
        halo = Image.new("RGBA", (N, N), (0, 0, 0, 0)); hp = halo.load()
        for y in range(N):
            for x in range(N):
                if grid[y][x] != ".": continue
                near = [grid[y + dy][x + dx] for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0))
                        if 0 <= y + dy < N and 0 <= x + dx < N and grid[y + dy][x + dx] not in ".D"]
                if near: hp[x, y] = rgba(max(sorted(set(near)), key=near.count), 80)
        img = Image.alpha_composite(halo, img)
    return img.resize((N * SCALE, N * SCALE), Image.NEAREST)

def parse(rows): return [list(r) for r in rows]

# ---------- Dance: зеркальный шар ----------
def mirror_ball():
    g = blank(); cx, cy, r = 7.5, 9.0, 6.4
    ramp = ["v", "B", "c", "C", "W"]
    for y in range(N):
        for x in range(N):
            dx, dy = x - cx, y - cy; d = math.hypot(dx, dy)
            if d > r: continue
            if d > r - 1.0: g[y][x] = "V"; continue  # неоновый обод
            fx, fy = (x // 2) * 2 + 0.5 - cx, (y // 2) * 2 + 0.5 - cy
            fd = min(math.hypot(fx, fy), r * 0.99)
            nz = math.sqrt(1 - (fd / r) ** 2); light = (-fx * 0.6 - fy * 0.65) / r + nz * 0.55
            lvl = min(4, max(0, int((light + 0.25) * 3.0)))
            if ((x // 2) * 3 + (y // 2) * 5) % 7 == 0: lvl = min(4, lvl + 1)  # отдельные «зайчики»
            g[y][x] = ramp[lvl]
    for y in (0, 1): g[y][7] = "V"; g[y][8] = "V"
    for (sx, sy, col) in ((2, 2, "M"), (13, 3, "Y"), (14, 13, "M")):
        g[sy][sx] = "W"
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if g[sy + dy][sx + dx] == ".": g[sy + dy][sx + dx] = col
    return g

# ---------- Chillout: пальма на фоне закатного солнца ----------
def palm():
    g = blank()
    for y, col in ((7, "Y"), (8, "Y"), (9, "O"), (10, "M"), (12, "m")):  # солнце полосками
        for x in range(N):
            if math.hypot(x - 7.5, y - 11.5) <= 6.6: g[y][x] = col
    for x in range(2, 14): g[13][x] = "C"                                  # вода
    for y in range(4, 13):                                                 # ствол с изгибом
        x = 7 if y < 8 else 8
        g[y][x] = g[y][x + 1] = "O" if y % 2 else "m"
    fronds = [
        [(6, 3), (5, 3), (4, 3), (3, 4), (2, 5), (1, 6)],
        [(6, 2), (5, 1), (4, 1), (3, 1), (2, 2)],
        [(8, 2), (8, 1), (9, 0)],
        [(9, 2), (10, 1), (11, 1), (12, 1), (13, 2)],
        [(9, 3), (10, 3), (11, 3), (12, 4), (13, 5), (14, 6)],
    ]
    for fr in fronds:
        for i, (x, y) in enumerate(fr):
            g[y][x] = "L"
            if i and y + 1 < N and g[y + 1][x] == ".": g[y + 1][x] = "G"   # нижняя сторона листа
    g[3][7] = g[3][8] = g[2][7] = "G"                                      # крона
    return g

# ---------- House: пластинка ----------
def vinyl():
    g = blank(); cx, cy = 7.5, 7.5
    for y in range(N):
        for x in range(N):
            d = math.hypot(x - cx, y - cy)
            if d > 7.3: continue
            if d > 6.5: g[y][x] = "C"
            elif d > 3.2: g[y][x] = "V" if min(abs(d - 4.4), abs(d - 5.7)) < 0.42 else "v"  # бороздки
            elif d > 0.9: g[y][x] = "M"                                # яблоко
            else: g[y][x] = "D"
            ang = math.degrees(math.atan2(y - cy, x - cx))
            if 3.2 < d <= 6.5 and -160 < ang < -110: g[y][x] = "W" if d > 4.8 else "P"  # блик
    return g

# ---------- Стоп ----------
def stop():
    g = blank()
    for y in range(3, 13):
        for x in range(3, 13):
            edge = x in (3, 12) or y in (3, 12)
            g[y][x] = "M" if edge else "R"
    g[4][4] = g[4][5] = g[5][4] = "P"
    for c in ((3, 3), (3, 12), (12, 3), (12, 12)): g[c[1]][c[0]] = "."
    return g

# ---------- Громкость: динамик + волны ----------
SPEAKER = [
    "................",
    "................",
    "......CC........",
    ".....CWC........",
    "....CWcC........",
    ".CCCWccC........",
    ".CWWcccC........",
    ".CWccccC........",
    ".CWccccC........",
    ".CWccccC........",
    ".CCCcccC........",
    "....CccC........",
    ".....CcC........",
    "......CC........",
    "................",
    "................",
]
def speaker(waves):
    g = parse(SPEAKER); cx, cy = 7.0, 7.5
    for R in (3.0, 5.4, 7.8)[:waves]:
        for y in range(N):
            for x in range(9, N):
                d = math.hypot(x - cx, y - cy); ang = abs(math.degrees(math.atan2(y - cy, x - cx)))
                if abs(d - R) < 0.6 and ang < 52: g[y][x] = "M"
    return g

# ---------- Эквалайзер (анимация) ----------
def eq_frames():
    frames, peaks = [], [0] * 5
    T = 16
    for t in range(T):
        g = blank()
        for b in range(5):
            ph = 2 * math.pi * t / T
            h = 3.6 + 2.6 * math.sin(ph * (1 + b % 3) + b * 1.7) + 1.2 * math.sin(ph * 2 + b)
            h = max(1, min(7, round(h))) * 2
            peaks[b] = h if h >= peaks[b] else max(h, peaks[b] - 1)
            x0 = 1 + b * 3
            for s in range(h):
                y = 14 - s
                g[y][x0] = g[y][x0 + 1] = "C" if y >= 11 else "M" if y >= 6 else "Y"
            py = 13 - peaks[b]
            if peaks[b] > h and py >= 0: g[py][x0] = g[py][x0 + 1] = "L"
        frames.append(to_image(g, glow=True))
    return frames

# ---------- Трек: две ноты ----------
NOTE = [
    "................",
    ".....CCCCCCCCC..",
    ".....CCCCCCCCC..",
    ".....C.......C..",
    ".....C.......C..",
    ".....C.......C..",
    ".....C.......C..",
    ".....C.......C..",
    ".....C.......C..",
    "..MMMC....MMMC..",
    ".MMMMC...MMMMC..",
    ".MPMMM...MPMMM..",
    ".MMMMM...MMMMM..",
    "..MMM.....MMM...",
    "................",
    "................",
]

# ---------- «В канале»: наушники ----------
def headphones():
    g = blank(); cx, cy = 7.5, 9.0
    for y in range(10):                                                    # дужка
        for x in range(N):
            if 5.4 <= math.hypot(x - cx, y - cy) <= 6.9: g[y][x] = "C"
    for x0, x1, inner in ((1, 4, 4), (11, 14, 11)):                        # амбушюры
        for y in range(8, 15):
            for x in range(x0, x1 + 1):
                if not (y in (8, 14) and x in (x0, x1)): g[y][x] = "M"
        for y in range(9, 14): g[y][inner] = "V"
    for x in (1, 12): g[9][x] = g[10][x] = "P"                             # блики
    return g

# ---------- Нет прав на управление: замок ----------
LOCK = [
    "................",
    ".....CCCCCC.....",
    "....CC....CC....",
    "....C......C....",
    "....C......C....",
    "....C......C....",
    "..MMMMMMMMMMMM..",
    "..MPPMMMMMMMMM..",
    "..MPMMMMMMMMMM..",
    "..MMMMMDDMMMMM..",
    "..MMMMMDDMMMMM..",
    "..MMMMMDDMMMMM..",
    "..MMMMMMMMMMMM..",
    "..mmmmmmmmmmmm..",
    "................",
    "................",
]

# ---------- Подсказка «нажмите кнопку»: стрелка вниз ----------
ARROW = [
    "................",
    "................",
    "......WCCC......",
    "......WCCC......",
    "......WCCC......",
    "......WCCC......",
    "......WCCC......",
    "..MMMMMMMMMMMM..",
    "...MMMMMMMMMM...",
    "....MMMMMMMM....",
    ".....MMMMMM.....",
    "......MMMM......",
    ".......MM.......",
    "................",
    "................",
    "................",
]

sprites = {
    "radio_dance": mirror_ball(), "radio_chill": palm(), "radio_house": vinyl(),
    "radio_stop": stop(), "radio_quieter": speaker(1), "radio_louder": speaker(3),
    "radio_note": parse(NOTE), "radio_listen": headphones(), "radio_down": parse(ARROW), "radio_lock": parse(LOCK),
}
for name, grid in sprites.items():
    to_image(grid).save(OUT / f"{name}.png", optimize=True)

frames = eq_frames()
# GIF: прозрачность одним индексом палитры, кадры полностью перерисовываются.
pal_frames = []
for f in frames:
    alpha = f.getchannel("A")
    p = f.convert("RGB").quantize(colors=63, method=Image.Quantize.MEDIANCUT)
    p.putpalette(p.getpalette()[:63 * 3] + [0, 0, 0] + [0] * (192 - 64) * 3)
    mask = Image.eval(alpha, lambda a: 255 if a < 128 else 0)
    p.paste(63, mask=mask)
    pal_frames.append(p)
pal_frames[0].save(OUT / "radio_eq.gif", save_all=True, append_images=pal_frames[1:], duration=110,
                   loop=0, transparency=63, disposal=2, optimize=False)
for p in sorted(OUT.iterdir()): print(p.name, p.stat().st_size)
