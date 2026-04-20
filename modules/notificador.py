"""
Notificações via Telegram.
Configure telegram_bot_token e telegram_chat_id no config.json.
Para criar um bot: https://t.me/BotFather
Para obter seu chat_id: envie uma mensagem para o bot e acesse
https://api.telegram.org/bot<TOKEN>/getUpdates
"""

import httpx
from modules.logger import Logger

logger = Logger("notificador")


async def notificar(config: dict, mensagem: str) -> bool:
    """
    Envia mensagem via Telegram. Retorna True se enviou com sucesso.
    Silencioso se as credenciais não estiverem configuradas.
    """
    token   = config.get("telegram_bot_token",  "").strip()
    chat_id = config.get("telegram_chat_id",    "").strip()
    if not token or not chat_id:
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": mensagem, "parse_mode": "HTML"},
            )
        if resp.status_code == 200:
            logger.debug("Notificação Telegram enviada")
            return True
        logger.warning(f"Telegram retornou {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"Falha ao enviar notificação Telegram: {e}")
    return False
