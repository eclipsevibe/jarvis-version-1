#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$ROOT/jarvis-orange-hud.png}"
PYTHON="$ROOT/.venv/bin/python"
if ! "$PYTHON" -c 'import PIL' >/dev/null 2>&1; then PYTHON="$(command -v python3)"; fi
"$PYTHON" - "$OUT" <<'PY'
import math, sys
from PIL import Image, ImageDraw, ImageFont
w,h=1920,1080; im=Image.new('RGB',(w,h),(5,5,7)); d=ImageDraw.Draw(im)
orange=(255,140,0); amber=(255,179,0); cx,cy=w//2,h//2
for r in (150,180,205,225,255,275): d.ellipse((cx-r,cy-r,cx+r,cy+r),outline=orange if r%2 else amber,width=2 if r in (180,225) else 1)
for i in range(120):
    a=math.radians(i*3); r0=280; r1=298 if i%5==0 else 287
    d.line((cx+math.cos(a)*r0,cy+math.sin(a)*r0,cx+math.cos(a)*r1,cy+math.sin(a)*r1),fill=amber if i%5==0 else (115,68,10),width=2 if i%5==0 else 1)
for box,a0,a1 in [((cx-248,cy-248,cx+248,cy+248),20,132),((cx-225,cy-225,cx+225,cy+225),200,285)]: d.arc(box,a0,a1,fill=amber,width=4)
font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',22)
d.text((cx-90,cy-12),'J.A.R.V.I.S.',fill=amber,font=font)
for x,y,t in [(60,65,'CPU  //  RAM  //  BATTERY'),(w-500,65,'NETWORK  ONLINE   •   12:00:00'),(60,h-80,'SYSTEMS NOMINAL  //  DESKTOP INTERFACE'),(w-340,h-80,'LOCAL AI  •  AUDIO READY')]: d.text((x,y),t,fill=orange,font=font)
im.save(sys.argv[1],quality=95)
PY
if command -v gsettings >/dev/null 2>&1; then
  URI="file://$OUT"
  if gsettings list-schemas | grep -qx org.gnome.desktop.background; then
    gsettings set org.gnome.desktop.background picture-uri "$URI"
    gsettings set org.gnome.desktop.background picture-uri-dark "$URI"
  fi
  if gsettings list-schemas | grep -qx org.cinnamon.desktop.background; then
    gsettings set org.cinnamon.desktop.background picture-uri "$URI"
  fi
fi
echo "Generated matching Jarvis backdrop at $OUT"
