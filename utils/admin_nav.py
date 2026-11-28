"""Admin paneli: «qayerda edim» xotirasi.

Admin kartalar ro'yxatida (tab / sarlavha / sahifa) biror kartaga kirib, tahrirlab yoki
o'chirib orqaga qaytganda aynan o'sha ro'yxat va sahifaga qaytishi uchun oxirgi
ko'rilgan ro'yxat holati shu yerda saqlanadi (jarayon xotirasida, har bir admin uchun alohida).

mode:
  view — kartalarni ko'rish (karta tafsiloti)
  edit — tahrirlash (kartaga bosilsa tahrirlash maydonlari ochiladi)
  del  — o'chirish (kartaga bosilsa o'chirish tasdig'i ochiladi)
"""
from __future__ import annotations

from typing import Dict

from utils.callback_data import AdmV2Cb

_MAX_ENTRIES = 2000
_STATE: Dict[int, dict] = {}

MODE_VIEW = "view"
MODE_EDIT = "edit"
MODE_DEL = "del"

MODE_TITLES = {
    MODE_VIEW: "👁 KARTALARNI KO‘RISH",
    MODE_EDIT: "✏️ KARTA TAHRIRLASH",
    MODE_DEL: "🗑 KARTA O‘CHIRISH",
}
# Ro'yxatdagi karta tugmasi qaysi amalni ochadi
MODE_CARD_ACT = {MODE_VIEW: "card_det", MODE_EDIT: "edit_pick", MODE_DEL: "del_one"}
MODE_CARD_PREFIX = {MODE_VIEW: "", MODE_EDIT: "✏️ ", MODE_DEL: "🗑 "}


def _slot(owner_id: int) -> dict:
    if len(_STATE) > _MAX_ENTRIES:
        for k in list(_STATE.keys())[: _MAX_ENTRIES // 2]:
            _STATE.pop(k, None)
    return _STATE.setdefault(int(owner_id), {"mode": MODE_VIEW, "last": None})


def set_mode(owner_id: int, mode: str) -> None:
    st = _slot(owner_id)
    st["mode"] = mode if mode in MODE_CARD_ACT else MODE_VIEW
    st["last"] = None


def get_mode(owner_id: int) -> str:
    return _slot(owner_id)["mode"]


def remember(owner_id: int, act: str, tid: int = 0, ext: str = "", p: int = 1) -> None:
    """Oxirgi ko'rilgan ro'yxat holatini eslab qoladi."""
    _slot(owner_id)["last"] = {"act": act, "tid": int(tid), "ext": str(ext), "p": max(1, int(p))}


def back_callback(owner_id: int) -> str:
    """«Ro'yxatga qaytish» tugmasi uchun callback_data (oxirgi ro'yxat yoki kartalar menyusi)."""
    last = _slot(owner_id).get("last")
    if not last:
        return AdmV2Cb(owner_id=owner_id, sec="cards", act="v").pack()
    return AdmV2Cb(
        owner_id=owner_id, sec="cards", act=last["act"], tid=last["tid"], ext=last["ext"], p=last["p"]
    ).pack()
