# urls.py
from django.urls import path
from .views import *

urlpatterns = [
    path('modificar/', SolicitarModificar.as_view(), name='enviar-botones'),
    path('soporte/', SoporteGPT.as_view(), name='soporte-gpt'),
    path('start/', BienvenidaView.as_view(),name="bienvenida"),
    path('guion/', CrearGuionView.as_view(), name='generar-guion'),
    path('verificar-clave/',VerificarClaveView.as_view(), name="verificar-clave"),
    path('menu/',MostrarMenuView.as_view(), name='menu'),
    path('seleccionar-titulo/',SeleccionarTituloGuion.as_view(),name="seleccionar-titulo" ),
    path('getId/', getFileId.as_view(), name='get-id'),
    path('api/n8n/chat/', N8nChatHandler.as_view()),               # Para enviar a n8n
    path('webhook/n8n/callback/', N8nWebhookReceiver.as_view()),
]
