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

Men senga personaj rasmlarini beraman. Har bir rasm uchun cards.json faylini tuzib ber.

MUHIM QOIDALAR (xato ma'lumot ketmasligi uchun):
1. FAQAT ishonching komil bo'lgan personajni yoz. Ishonmasang uni "cards" ga QO'SHMA,
   rasm fayl nomini "unknown_images" ro'yxatiga yoz. Taxmin qilma, o'ylab topma.
2. Ismlar va asar nomlari rasmiy ingliz (lotin) yozuvida bo'lsin, masalan "Naruto Uzumaki".
3. Faqat MAJBURIY maydonlarni to'ldir. Ixtiyoriy maydonlarni (tug'ilgan kun, vazn va h.k.)
   faqat 100% bilsang yoz, bilmasang UMUMAN yozma (bo'sh qoldir).

MAJBURIY maydonlar (bittasi yo'q bo'lsa karta qo'shilmaydi):
  image           - rasm fayl nomi (masalan: naruto.jpg)
  character_name  - personaj ismi
  media_type      - turi: {media}
  media_name      - asar nomi (anime / manga / kino nomi)
  rank_id         - rank raqami (faqat son):
{ranks}

IXTIYORIY maydonlar:
  description, birthday, age, height, weight, personality, history, abilities,
  aliases (ro'yxat: boshqa ismlari), base_value, drop_limit

Javobni FAQAT to'g'ri JSON ko'rinishida ber, boshqa matn yozma:
{{
  "cards": [
    {{"image": "naruto.jpg", "character_name": "Naruto Uzumaki", "media_type": "Anime",
      "media_name": "Naruto", "rank_id": 3}}
  ],
  "unknown_images": ["rasm_7.jpg"]
}}

ESLATMA: bir asardan ko'p karta bo'lsa, botdagi "⚡ Tezkor qo'shish" tezroq: asar nomi va rankni
bir marta yozasiz, keyin rasmlarni izohi bilan (personaj ismi) yuborasiz.
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
