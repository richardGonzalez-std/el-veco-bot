# urls.py
from django.urls import path
from .views import (
    SolicitarModificar,
    SoporteInlineView,  # Reemplaza a SoporteGPT
    BienvenidaView,
    CrearGuionView,
    VerificarClaveView,
    MostrarMenuView,
    SeleccionarTituloGuion,
    getFileId,
    PerfilUsuarioView,
    WebhookStatusView,
    LogoutView
)

urlpatterns = [
    # Autenticación y sesión
    path('start/', BienvenidaView.as_view(), name="bienvenida"),
    path('verificar-clave/', VerificarClaveView.as_view(), name="verificar-clave"),
    path('logout/', LogoutView.as_view(), name='logout'),
    
    # Navegación principal
    path('menu/', MostrarMenuView.as_view(), name='menu'),
    path('perfil/', PerfilUsuarioView.as_view(), name='perfil-usuario'),
    
    # Gestión de guiones
    path('modificar/', SolicitarModificar.as_view(), name='enviar-botones'),
    path('guion/', CrearGuionView.as_view(), name='generar-guion'),
    path('seleccionar-titulo/', SeleccionarTituloGuion.as_view(), name="seleccionar-titulo"),
    path('getId/', getFileId.as_view(), name='get-id'),
    
    # Soporte IA
    path('soporte/', SoporteInlineView.as_view(), name='soporte-inline'),
    
    # Monitoreo del sistema
    path('webhook-status/', WebhookStatusView.as_view(), name='webhook-status'),
]