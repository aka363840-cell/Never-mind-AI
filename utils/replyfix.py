"""When another bot (or an admin) deletes a command the instant it is sent, our answer used to fail with
"message to be replied not found" and the person got nothing. This request middleware retries WITHOUT the reply link,
so the answer is still posted in the chat."""
from __future__ import annotations

import logging

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest

log = logging.getLogger(__name__)


class ReplyFallbackMiddleware(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as e:
            msg = str(e).lower()
            if "replied" not in msg or "not found" not in msg:
                raise
            upd = {k: None for k in ("reply_parameters", "reply_to_message_id") if getattr(method, k, None) is not None}
            if not upd:
                raise
            log.info("reply target was deleted - sending without the reply link")
            return await make_request(bot, method.model_copy(update=upd))
