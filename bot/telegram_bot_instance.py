
from telegram import Bot
from telegram.request import HTTPXRequest
from django.conf import settings

# Aumentamos el pool para manejar más conexiones simultáneas
request = HTTPXRequest(connection_pool_size=50, read_timeout=15.0)

# Bot global reutilizable
telegramBot = Bot(token=settings.BOT_TOKEN, request=request)