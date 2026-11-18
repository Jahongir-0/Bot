"""Namuna ZIP yaratadi: cards.json + rasmlar + AI uchun ko'rsatma (PROMPT_FOR_AI.txt)."""
import io
import json
import zipfile
from typing import List, Tuple

from services.card_zip_import import rank_table
from utils.constants import MEDIA_TYPES

_SAMPLES: List[Tuple[str, str, str, str, int, Tuple[int, int, int]]] = [
    ("naruto_uzumaki.jpg", "Naruto Uzumaki", "Anime", "Naruto", 3, (255, 140, 0)),
    ("solo_leveling_jinwoo.jpg", "Sung Jinwoo", "Manhwa", "Solo Leveling", 4, (90, 60, 200)),
    ("spongebob.jpg", "SpongeBob", "Cartoon", "SpongeBob SquarePants", 1, (240, 210, 40)),
]


def _placeholder(text: str, color: Tuple[int, int, int]) -> bytes:
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (600, 840), color)
    d = ImageDraw.Draw(im)
    d.rectangle((20, 20, 580, 820), outline=(255, 255, 255), width=6)
    d.text((60, 400), text, fill=(255, 255, 255))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=85)
    return out.getvalue()


def build_prompt_text() -> str:
    ranks = "\n".join(f"  {rid} = {name}" for rid, name in rank_table())
    media = ", ".join(MEDIA_TYPES)
    return f"""KARTA ZIP TAYYORLASH KO'RSATMASI (sun'iy intellekt uchun)

Men senga personaj rasmlarini beraman. Har bir rasm uchun personaj va u chiqqan asar
haqida ma'lumotni topib, quyidagi formatdagi cards.json faylini tuzib ber.
Rasmlarni "images" papkasiga joylab, rasm nomini "image" maydoniga yoz.

MAJBURIY maydonlar (bittasi yo'q bo'lsa karta qo'shilmaydi):
  image           - rasm fayl nomi (masalan: naruto.jpg)
  character_name  - personaj ismi
  media_type      - turi: {media}
  media_name      - asar nomi (anime / manga / kino nomi)
  rank_id         - rank raqami (faqat son):
{ranks}

IXTIYORIY maydonlar (bilsang yoz, bilmasang tashlab ket):
  description, birthday, age, height, weight, personality, history, abilities,
  aliases (ro'yxat: boshqa ismlari), base_value, drop_limit

Javobni FAQAT to'g'ri JSON ro'yxat ko'rinishida ber, boshqa matn yozma.

Namuna:
[
  {{"image": "naruto.jpg", "character_name": "Naruto Uzumaki", "media_type": "Anime",
    "media_name": "Naruto", "rank_id": 3, "birthday": "10-oktabr", "personality": "Qat'iyatli, quvnoq"}}
]
"""


def build_sample_zip() -> bytes:
    records = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, char, mtype, media, rid, color in _SAMPLES:
            zf.writestr(f"images/{fname}", _placeholder(char, color))
            records.append(
                {
                    "image": fname,
                    "character_name": char,
                    "media_type": mtype,
                    "media_name": media,
                    "rank_id": rid,
                    "birthday": "—",
                    "personality": "Namuna ma'lumot",
                    "aliases": [],
                }
            )
        zf.writestr("cards.json", json.dumps(records, ensure_ascii=False, indent=2))
        zf.writestr("PROMPT_FOR_AI.txt", build_prompt_text())
    return buf.getvalue()
