"""Kartalarni anime -> personaj bo'yicha guruhlash uchun umumiy SQL ifodalar.

Guruhlash nom bo'yicha (katta-kichik harfga va chetdagi bo'shliqqa e'tibor bermasdan) bajariladi,
shuning uchun bir xil anime/personaj nomli kartalar (turli rarity yoki rasm bilan) bitta bo'limga tushadi.
"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.card import Card


def anime_key():
    return func.lower(func.trim(Card.anime_name))


def character_key():
    return func.lower(func.trim(Card.character_name))


async def get_anime_key_of(session: AsyncSession, card_id: int):
    """Karta ID bo'yicha uning anime guruh kalitini (DB hisoblagan holda) qaytaradi."""
    return (await session.execute(select(anime_key()).where(Card.id == card_id))).scalar_one_or_none()


async def get_character_key_of(session: AsyncSession, card_id: int):
    return (await session.execute(select(character_key()).where(Card.id == card_id))).scalar_one_or_none()


async def get_group_reps(session: AsyncSession, card_id: int):
    """(anime_rep_id, character_rep_id) — guruhdagi eng kichik Card.id (stabil vakil)."""
    a_key = await get_anime_key_of(session, card_id)
    c_key = await get_character_key_of(session, card_id)
    if a_key is None or c_key is None:
        return None
    anime_rep = (await session.execute(select(func.min(Card.id)).where(anime_key() == a_key))).scalar_one()
    char_rep = (
        await session.execute(
            select(func.min(Card.id)).where(anime_key() == a_key, character_key() == c_key)
        )
    ).scalar_one()
    return int(anime_rep), int(char_rep)
