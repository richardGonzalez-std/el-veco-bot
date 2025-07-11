# urls.py
from django.urls import path
from .views import *

urlpatterns = [
    # Rutas para el chat embedded
    path('chat/embedded/', ChatEmbeddedView.as_view(), name='chat_embedded'),
    path('chat/session/', ChatSesionView.as_view(), name='chat_session'),
    path('chat/message/', ManejarMensajesChatView.as_view(), name='chat_message'),
    
    # Webhook principal de Telegram
    path('webhook/', TelegramWebhookView.as_view(), name='telegram_webhook'),
    
    # Rutas existentes
    path('modificar/', SolicitarModificar.as_view(), name='enviar-botones'),
    path('soporte/', SoporteGPT.as_view(), name='soporte-gpt'),
    path('start/', BienvenidaView.as_view(), name="bienvenida"),
    path('guion/', CrearGuionView.as_view(), name='generar-guion'),
    path('verificar-clave/', VerificarClaveView.as_view(), name="verificar-clave"),
    path('menu/', MostrarMenuView.as_view(), name='menu'),
    path('seleccionar-titulo/', SeleccionarTituloGuion.as_view(), name="seleccionar-titulo"),
    path('getId/', getFileId.as_view(), name='get-id'),
    
    # Rutas adicionales para testing
    path('test/chat/', ChatEmbeddedView.as_view(), name='test_chat'),
    path('status/', lambda request: JsonResponse({"status": "ok"}), name='status'),
]