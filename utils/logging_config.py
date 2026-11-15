import logging
import re
import sys
from typing import Optional


class SensitiveTokenFilter(logging.Filter):
    """Masks Telegram bot tokens and sensitive secrets from all log records."""

    TOKEN_PATTERN = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{25,50}\b")
    SECRET_PATTERN = re.compile(r"(BOT_TOKEN|STARS_PAYMENT_SECRET|password)=([^\s,;]+)", re.IGNORECASE)

    def __init__(self, token: Optional[str] = None) -> None:
        super().__init__()
        self._token = token or ""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._mask(record.msg)
        if record.args:
            masked_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    masked_args.append(self._mask(arg))
                else:
                    masked_args.append(arg)
            record.args = tuple(masked_args)
        return True

    def _mask(self, text: str) -> str:
        if self._token and self._token in text:
            text = text.replace(self._token, "[REDACTED_TOKEN]")
        text = self.TOKEN_PATTERN.sub("[REDACTED_BOT_TOKEN]", text)
        text = self.SECRET_PATTERN.sub(r"\1=[REDACTED]", text)
        return text


def setup_logging(level: str = "INFO", bot_token: Optional[str] = None) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(SensitiveTokenFilter(bot_token))

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(log_level)
    root.addHandler(handler)

    # Keep third-party HTTP/DB loggers clean
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
