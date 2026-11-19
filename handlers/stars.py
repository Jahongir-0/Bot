import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery

from config.settings import get_settings
from database.session import get_session
from keyboards.main_menu import back_home_kb
from keyboards.market import stars_shop_kb
from models.user import User
from services.community_service import CommunityService
from services.event_service import EventService, is_event_payload
from services.payment_service import PaymentService
from services.settings_service import SettingsService
from utils.callback_data import NavCb, StarsCb
from utils.formatting import esc, fmt_coins
from utils.i18n import get_lang, tr
from utils.tg_helpers import edit_or_answer

logger = logging.getLogger(__name__)
router = Router(name="stars")


@router.message(Command("stars"))
@router.callback_query(NavCb.filter(F.target == "stars"))
@router.callback_query(StarsCb.filter(F.action == "shop"))
async def cb_stars_shop(target: Message | CallbackQuery, db_user: User = None) -> None:
    lang = get_lang(db_user)
    if not get_settings().enable_telegram_stars:
        msg = tr(lang, "stars_disabled")
        if isinstance(target, CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg)
        return

    async with get_session() as session:
        packages = await SettingsService.get_stars_packages(session)

    text = tr(lang, "stars_title") if packages else tr(lang, "stars_empty")
    owner_id = target.from_user.id
    await edit_or_answer(target, text, reply_markup=stars_shop_kb(owner_id, packages, lang=lang))


@router.callback_query(StarsCb.filter(F.action == "buy"))
async def cb_stars_buy(callback: CallbackQuery, callback_data: StarsCb, db_user: User) -> None:
    if not get_settings().enable_telegram_stars:
        await callback.answer("Telegram Stars vaqtincha o‘chirilgan.", show_alert=True)
        return

    async with get_session() as session:
        created = await PaymentService.create_star_order(
            session=session,
            user_id=db_user.id,
            telegram_user_id=callback.from_user.id,
            package_id=callback_data.package_id,
        )
        if created is not None:
            await session.commit()

    if created is None:
        await callback.answer("Paket topilmadi.", show_alert=True)
        return

    order, payload, label = created
    # NOTE: Telegram Stars digital goods require currency="XTR" and provider_token="" (empty string).
    # TODO (Production): Ensure bot has Stars payments enabled in @BotFather if required by your account.
    try:
        await callback.bot.send_invoice(
            chat_id=callback.from_user.id,
            title=f"{fmt_coins(order.coins_amount)} Anime Coins",
            description=f"Anime Card Collection hisobingizga {fmt_coins(order.coins_amount)} coins qo‘shiladi.",
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=label, amount=order.stars_amount)],
        )
        await callback.answer("⭐ To‘lov hisob-fakturasi shaxsiy chatga yuborildi!")
    except Exception as exc:
        logger.warning("Failed to send Stars invoice to %s: %s", callback.from_user.id, exc)
        me = await callback.bot.get_me()
        if callback.message is not None and me.username:
            await callback.message.reply(
                "⭐ To‘lov hisob-fakturasini olish uchun avval botning shaxsiy chatida <b>Start</b> bosing:",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🤖 Botni ochish",
                                url=f"https://t.me/{me.username}?start=stars",
                            )
                        ]
                    ]
                ),
            )
        await callback.answer("Avval botga shaxsiy chatda /start bosing.", show_alert=True)


@router.pre_checkout_query()
async def on_pre_checkout(pre_checkout_query: PreCheckoutQuery, db_user: User) -> None:
    if is_event_payload(pre_checkout_query.invoice_payload):
        async with get_session() as session:
            ok_evt, reason = await EventService.validate_pre_checkout(
                session,
                pre_checkout_query.invoice_payload,
                db_user,
                pre_checkout_query.currency,
                pre_checkout_query.total_amount,
            )
        await pre_checkout_query.answer(ok=ok_evt, error_message=None if ok_evt else reason[:200])
        return

    if pre_checkout_query.invoice_payload.startswith("vip:"):
        parts = pre_checkout_query.invoice_payload.split(":")
        ok_vip = False
        if len(parts) == 3 and parts[2] == str(pre_checkout_query.from_user.id) and pre_checkout_query.currency == "XTR":
            async with get_session() as session:
                cfg = await CommunityService.get_vip_config(session)
            tier = (cfg.get("tiers") or {}).get(parts[1])
            if tier and int(tier.get("stars", 0)) == int(pre_checkout_query.total_amount):
                ok_vip = True
        await pre_checkout_query.answer(
            ok=ok_vip,
            error_message=None if ok_vip else "VIP tarif ma'lumotlari tasdiqlanmadi.",
        )
        return

    async with get_session() as session:
        ok = await PaymentService.verify_pre_checkout(
            session=session,
            payload=pre_checkout_query.invoice_payload,
            telegram_user_id=pre_checkout_query.from_user.id,
            currency=pre_checkout_query.currency,
            total_amount=pre_checkout_query.total_amount,
        )
    if not ok:
        logger.warning(
            "Rejected invalid/tampered Stars pre_checkout from user %s",
            pre_checkout_query.from_user.id,
        )
        await pre_checkout_query.answer(
            ok=False,
            error_message="To‘lov ma'lumotlari tasdiqlanmadi yoki muddati tugagan.",
        )
        return

    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message, db_user: User) -> None:
    sp = message.successful_payment
    if is_event_payload(sp.invoice_payload):
        await _handle_event_payment(message, db_user)
        return

    if sp.invoice_payload.startswith("vip:"):
        await _handle_vip_payment(message, db_user)
        return

    already_processed = False
    async with get_session() as session:
        order = await PaymentService.complete_star_payment(
            session=session,
            payload=sp.invoice_payload,
            telegram_user_id=message.from_user.id,
            currency=sp.currency,
            total_amount=sp.total_amount,
            telegram_payment_charge_id=sp.telegram_payment_charge_id,
            provider_payment_charge_id=sp.provider_payment_charge_id,
        )
        if order is not None:
            await session.commit()
        else:
            await session.rollback()
            already_processed = await PaymentService.is_charge_processed(
                session, sp.telegram_payment_charge_id
            )

    if order is None:
        if already_processed:
            # Telegram bir xil to'lov xabarini qayta yuborgan: coin allaqachon qo'shilgan, ikkinchi marta emas
            logger.info("Duplicate Stars payment message ignored (charge %s)", sp.telegram_payment_charge_id)
            return

        logger.error(
            "Unverified Stars payment from %s (charge %s). Refunding.",
            message.from_user.id,
            sp.telegram_payment_charge_id,
        )
        refunded = False
        try:
            await message.bot.refund_star_payment(
                user_id=message.from_user.id,
                telegram_payment_charge_id=sp.telegram_payment_charge_id,
            )
            refunded = True
        except Exception as exc:
            logger.exception("Stars refund failed for charge %s: %s", sp.telegram_payment_charge_id, exc)

        if refunded:
            await message.answer("⚠️ To‘lovni tasdiqlab bo‘lmadi. Sarflangan Stars avtomatik qaytarildi.")
        else:
            await message.answer(
                "⚠️ To‘lovni tasdiqlashda muammo yuz berdi. Admin bilan bog‘laning.\n"
                f"Chek ID: <code>{sp.telegram_payment_charge_id}</code>"
            )
        return

    await message.answer(
        f"✅ <b>TO‘LOV MUVAFFAQIYATLI!</b>\n\n"
        f"⭐ Sarflandi: <b>{order.stars_amount} Stars</b>\n"
        f"🪙 Hisobingizga qo‘shildi: <b>+{fmt_coins(order.coins_amount)} coins</b>",
        reply_markup=back_home_kb(message.from_user.id, "profile"),
    )


async def _handle_event_payment(message: Message, db_user: User) -> None:
    """Stars bilan event kartasi: coin EMAS, karta beriladi. Berib bo'lmasa Stars avtomatik qaytariladi."""
    sp = message.successful_payment
    async with get_session() as session:
        res = await EventService.complete_stars_purchase(
            session,
            sp.invoice_payload,
            db_user,
            sp.currency,
            sp.total_amount,
            sp.telegram_payment_charge_id,
        )
        if res.success:
            await session.commit()
        else:
            await session.rollback()

    if res.success:
        await message.answer(
            f"🎉 To‘lov qabul qilindi! <b>{res.card.character_name}</b> event kartasi kolleksiyangizga qo‘shildi "
            f"({res.event.sold_count}/{res.event.total_copies})."
        )
        return
    if res.code == "duplicate":
        logger.info("Duplicate event payment message ignored (charge %s)", sp.telegram_payment_charge_id)
        return

    logger.error("Event Stars payment could not be fulfilled (%s): refunding charge %s", res.message, sp.telegram_payment_charge_id)
    try:
        parts = sp.invoice_payload.split(":")
        if len(parts) == 3:
            from sqlalchemy import update

            from models.limited_event import EventPurchase

            async with get_session() as session:
                await session.execute(
                    update(EventPurchase)
                    .where(EventPurchase.order_uid == parts[1], EventPurchase.status == "pending")
                    .values(status="failed")
                )
                await session.commit()
    except Exception:  # pragma: no cover
        logger.exception("Could not mark event purchase as failed")

    try:
        await message.bot.refund_star_payment(
            user_id=message.from_user.id, telegram_payment_charge_id=sp.telegram_payment_charge_id
        )
        await message.answer(f"⚠️ {res.message}\nSarflangan Stars avtomatik qaytarildi.")
    except Exception as exc:
        logger.exception("Event Stars refund failed: %s", exc)
        await message.answer(
            f"⚠️ {res.message}\nAdmin bilan bog‘laning. Chek ID: <code>{sp.telegram_payment_charge_id}</code>"
        )


async def _handle_vip_payment(message: Message, db_user: User) -> None:
    sp = message.successful_payment
    parts = sp.invoice_payload.split(":")
    tier_key = parts[1] if len(parts) == 3 else ""
    async with get_session() as session:
        cfg = await CommunityService.get_vip_config(session)
        tier = (cfg.get("tiers") or {}).get(tier_key)
        u = await session.get(User, db_user.id)
        if tier and u is not None and sp.currency == "XTR" and int(sp.total_amount) == int(tier.get("stars", 0)):
            days = int(tier.get("duration_days", 30))
            await CommunityService.grant_vip(session, u, tier_key, days=days)
            await CommunityService.notify_user(
                session,
                u.id,
                "vip",
                "💎 VIP obuna faollashtirildi!",
                f"{tier.get('name', tier_key)} ({days} kun) Stars orqali faollashtirildi.",
            )
            await session.commit()
            await message.answer(
                f"💎 <b>{esc(tier.get('name', tier_key))} FAOLLASHTIRILDI!</b>\n\n"
                f"⭐ To‘landi: <b>{sp.total_amount} Stars</b>\n"
                f"⏳ Muddati: <b>{days} kun</b>",
                reply_markup=back_home_kb(message.from_user.id, "profile"),
            )
            return
        await session.rollback()

    try:
        await message.bot.refund_star_payment(
            user_id=message.from_user.id, telegram_payment_charge_id=sp.telegram_payment_charge_id
        )
        await message.answer("⚠️ VIP tarif tasdiqlanmadi. Sarflangan Stars avtomatik qaytarildi.")
    except Exception as exc:
        logger.exception("VIP Stars refund failed: %s", exc)
        await message.answer(f"⚠️ VIP to‘lovida xatolik. Chek ID: <code>{sp.telegram_payment_charge_id}</code>")


