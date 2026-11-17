import uuid
from typing import Any


def cache_scope(session: Any) -> Any:
    """Har bir DB ulanishi (engine) uchun noyob kesh kaliti.

    id(engine) qayta ishlatilishi mumkin (testlarda engine'lar ketma-ket yaratiladi), shuning uchun
    engine'ga bir marta tasodifiy token yozib qo'yamiz.
    """
    bind = getattr(session, "bind", None)
    eng = getattr(bind, "sync_engine", bind)
    if eng is None:
        return 0
    scope = getattr(eng, "_cache_scope", None)
    if scope is None:
        scope = uuid.uuid4().hex
        try:
            eng._cache_scope = scope
        except Exception:
            scope = id(eng)
    return scope
