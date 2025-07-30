# views.py - Versión final combinada
import logging
import time
import io

import requests
from typing import Dict, List, Optional
from datetime import datetime

from rest_framework.views import APIView
from rest_framework.response import Response
from asgiref.sync import async_to_sync
from rest_framework import status
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from openai import OpenAI
from django.conf import settings
from django.core.cache import cache
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Configuración de logging
logger = logging.getLogger(__name__)

# URLs de webhook
N8N_CHAT_WEBHOOK_URL = "https://conexionai.app.n8n.cloud/webhook/2305f427-849e-4111-a040-25e5de928328/chat"

# Constantes para cache y timeouts
CACHE_TIMEOUT = 3600  # 1 hora
USER_SESSION_TIMEOUT = 7200  # 2 horas
MAX_RETRY_ATTEMPTS = 3
WEBHOOK_TIMEOUT = 30  # Timeout para requests al webhook
SCOPES = ["https://www.googleapis.com/auth/drive"]

# Cliente OpenAI (mantenido para otras funciones si es necesario)
client = OpenAI(api_key=settings.OPENAI_KEY)

# --- Servicios y utilidades ---
class GoogleDriveService:
    """Servicio para operaciones con Google Drive"""
    
    @staticmethod
    def get_service():
        """Obtener servicio autenticado de Google Drive"""
        creds = service_account.Credentials.from_service_account_file(
            settings.GOOGLE_CREDS_PATH,
            scopes=SCOPES
        )
        return build('drive', 'v3', credentials=creds)
    
    @staticmethod
    def obtener_documentos(titulo_busqueda: str = None) -> List[Dict]:
        """Obtener documentos de Google Drive con paginación"""
        service = GoogleDriveService.get_service()
        query = f"'{settings.GOOGLE_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.document'"
        
        if titulo_busqueda:
            query += f" and name contains '{titulo_busqueda}'"
        
        archivos = []
        page_token = None
        
        while True:
            response = service.files().list(
                q=query,
                spaces='drive',
                fields='nextPageToken, files(id, name)',
                pageToken=page_token
            ).execute()
            
            archivos.extend(response.get('files', []))
            page_token = response.get('nextPageToken', None)
            
            if page_token is None:
                break
        
        return archivos
    
    @staticmethod
    def crear_documento(titulo: str, contenido: str) -> str:
        """Crear nuevo documento en Google Drive"""
        service = GoogleDriveService.get_service()
        archivo_metadata = {
            "name": titulo,
            "mimeType": "application/vnd.google-apps.document",
            "parents": [settings.GOOGLE_FOLDER_ID],
        }

        media = MediaIoBaseUpload(
            io.BytesIO(contenido.encode("utf-8")),
            mimetype="text/html",
            resumable=True
        )
        
        archivo = service.files().create(
            body=archivo_metadata,
            media_body=media,
            fields="id"
        ).execute()
        
        return archivo.get("id")


class N8nWebhookService:
    """Servicio para interactuar con el webhook de n8n"""
    
    @staticmethod
    def send_chat_message(chat_id: int, message: str, username: str = None) -> Dict:
        """Enviar mensaje al webhook de n8n y obtener respuesta"""
        try:
            payload = {
                "chat_id": chat_id,
                "message": message,
                "username": username or f"user_{chat_id}",
                "timestamp": datetime.now().isoformat()
            }
            
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "VECO-Bot/1.0"
            }
            
            logger.info(f"Enviando mensaje a n8n webhook para chat_id: {chat_id}")
            
            response = requests.post(
                N8N_CHAT_WEBHOOK_URL,
                json=payload,
                headers=headers,
                timeout=WEBHOOK_TIMEOUT
            )
            
            response.raise_for_status()
            
            result = response.json()
            logger.info(f"Respuesta recibida de n8n para chat_id: {chat_id}")
            
            return {
                "success": True,
                "response": result.get("response", "Sin respuesta del asistente"),
                "data": result
            }
            
        except requests.exceptions.Timeout:
            logger.error(f"Timeout al conectar con n8n webhook para chat_id: {chat_id}")
            return {
                "success": False,
                "error": "timeout",
                "message": "El asistente está tardando en responder. Intenta de nuevo."
            }
            
        except requests.exceptions.ConnectionError:
            logger.error(f"Error de conexión con n8n webhook para chat_id: {chat_id}")
            return {
                "success": False,
                "error": "connection_error",
                "message": "No se pudo conectar con el asistente. Intenta más tarde."
            }
            
        except requests.exceptions.HTTPError as e:
            logger.error(f"Error HTTP {e.response.status_code} en n8n webhook para chat_id: {chat_id}")
            return {
                "success": False,
                "error": "http_error",
                "message": f"Error del servidor del asistente ({e.response.status_code}). Intenta más tarde."
            }
            
        except Exception as e:
            logger.error(f"Error inesperado en n8n webhook para chat_id {chat_id}: {e}")
            return {
                "success": False,
                "error": "unknown_error",
                "message": "Error técnico del asistente. Contacta al administrador."
            }
    
    @staticmethod
    def test_webhook_connection() -> bool:
        """Probar la conexión con el webhook"""
        try:
            test_payload = {
                "chat_id": 0,
                "message": "test_connection",
                "username": "system_test",
                "timestamp": datetime.now().isoformat()
            }
            
            response = requests.post(
                N8N_CHAT_WEBHOOK_URL,
                json=test_payload,
                timeout=10
            )
            
            return response.status_code == 200
            
        except Exception as e:
            logger.error(f"Error probando conexión webhook: {e}")
            return False


class UserSessionManager:
    """Gestor de sesiones de usuario robusto"""
    
    @staticmethod
    def create_session(chat_id: int, username: str) -> bool:
        """Crear sesión de usuario con timeout"""
        try:
            session_data = {
                'username': username,
                'created_at': datetime.now().isoformat(),
                'is_support_mode': False,
                'conversation_count': 0,
                'last_activity': datetime.now().isoformat()
            }
            cache.set(f"user_session_{chat_id}", session_data, USER_SESSION_TIMEOUT)
            logger.info(f"Sesión creada para usuario {username} (chat_id: {chat_id})")
            return True
        except Exception as e:
            logger.error(f"Error creando sesión: {e}")
            return False
    
    @staticmethod
    def get_session(chat_id: int) -> Optional[Dict]:
        """Obtener sesión de usuario"""
        return cache.get(f"user_session_{chat_id}")
    
    @staticmethod
    def is_authenticated(chat_id: int) -> bool:
        """Verificar si el usuario está autenticado"""
        session = UserSessionManager.get_session(chat_id)
        return session is not None
    
    @staticmethod
    def update_session(chat_id: int, **kwargs) -> bool:
        """Actualizar datos de sesión"""
        try:
            session = UserSessionManager.get_session(chat_id)
            if session:
                session.update(kwargs)
                session['last_activity'] = datetime.now().isoformat()
                cache.set(f"user_session_{chat_id}", session, USER_SESSION_TIMEOUT)
                return True
            return False
        except Exception as e:
            logger.error(f"Error actualizando sesión: {e}")
            return False
    
    @staticmethod
    def destroy_session(chat_id: int) -> bool:
        """Destruir sesión de usuario"""
        try:
            cache.delete(f"user_session_{chat_id}")
            logger.info(f"Sesión destruida para chat_id: {chat_id}")
            return True
        except Exception as e:
            logger.error(f"Error destruyendo sesión: {e}")
            return False


class TelegramMenuBuilder:
    """Constructor de menús de Telegram flexible"""
    
    @staticmethod
    def get_main_menu() -> InlineKeyboardMarkup:
        """Menú principal mejorado"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 Generar guion", callback_data="action:guion")],
            [InlineKeyboardButton("✏️ Modificar guion", callback_data="action:modificar")],
            [InlineKeyboardButton("🤖 Soporte IA Inline", callback_data="action:soporte_inline")],
            [InlineKeyboardButton("📊 Mi perfil", callback_data="action:perfil")],
            [InlineKeyboardButton("🚪 Cerrar sesión", callback_data="action:logout")]
        ])
    
    @staticmethod
    def get_support_menu() -> InlineKeyboardMarkup:
        """Menú de soporte inline"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Chat rápido", callback_data="support:quick_chat")],
            [InlineKeyboardButton("📝 Ayuda con guiones", callback_data="support:script_help")],
            [InlineKeyboardButton("🔧 Soporte técnico", callback_data="support:tech_help")],
            [InlineKeyboardButton("🔙 Volver al menú", callback_data="action:menu")]
        ])
    
    @staticmethod
    def get_document_selection_menu(archivos: List[Dict]) -> InlineKeyboardMarkup:
        """Menú para selección de documentos"""
        botones = [
            [InlineKeyboardButton(archivo["name"], callback_data=f"archivo_{archivo['id']}")]
            for archivo in archivos
        ]
        return InlineKeyboardMarkup(botones)
    
    @staticmethod
    def get_title_selection_menu() -> InlineKeyboardMarkup:
        """Menú para selección de título"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🏷 Nombre personalizado", callback_data="nombre_personalizado")],
            [InlineKeyboardButton("🤖 Nombre automático", callback_data="nombre_default")]
        ])


class MessageTemplates:
    """Plantillas de mensajes profesionales"""
    
    WELCOME = "🎭 **¡Bienvenido al VECO!**\n\nSoy tu asistente para la creación y gestión de guiones deportivos.\n\nPara comenzar, necesito que te autentiques:"
    
    AUTH_SUCCESS = "✅ **¡Autenticación exitosa!**\n\n¡Hola {username}! Ya puedes usar todas las funciones del VECO."
    
    AUTH_FAILED = "❌ **Clave incorrecta**\n\nPor favor, verifica tu clave e intenta nuevamente."
    
    SUPPORT_WELCOME = "🤖 **Soporte IA Inline Activado**\n\nAhora puedes chatear directamente conmigo a través de tu asistente personalizado de n8n.\n\nSimplemente escribe tu consulta y te responderé de inmediato.\n\n💡 **Comandos disponibles:**\n• `/menu` - Volver al menú principal\n• `/help` - Ver ayuda\n• `/status` - Estado del asistente\n\n🎯 **Especialidades:**\n• Ayuda con guiones deportivos\n• Soporte técnico de la plataforma\n• Consultas sobre FMSPORTS"
    
    SESSION_EXPIRED = "⏰ **Sesión expirada**\n\nTu sesión ha caducado por inactividad. Por favor, auténticate nuevamente."
    
    DOCUMENT_CREATED = (
        "✅ **Guión creado exitosamente!**\n\n"
        "• 📄 Título: {titulo}\n"
        "• 📂 Carpeta: {carpeta}\n"
        "• 🕒 Fecha: {fecha}"
    )
    
    DOCUMENT_ERROR = "❌ **Error al crear guión**\n\n{error}\n\nPor favor intenta nuevamente o contacta a soporte."
    
    DOCUMENT_NOT_FOUND = "⚠️ **Documento no encontrado**\n\nNo se encontró ningún documento con el título: `{titulo}`"
    
    DOCUMENT_SELECT = "📄 **Selecciona un documento para modificar:**"



    """Servicio mejorado para operaciones del bot"""
    
    def __init__(self):
        self.bot = Bot(token=settings.BOT_TOKEN)
    
    async def send_message_safe(self, chat_id: int, text: str, **kwargs) -> bool:
        """Enviar mensaje con manejo de errores"""
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                await self.bot.send_message(chat_id=chat_id, text=text, **kwargs)
                return True
            except Exception as e:
                logger.warning(f"Intento {attempt + 1} fallido para chat_id {chat_id}: {e}")
                if attempt == MAX_RETRY_ATTEMPTS - 1:
                    logger.error(f"Error enviando mensaje después de {MAX_RETRY_ATTEMPTS} intentos: {e}")
                    return False
                time.sleep(10)  # Esperar antes del siguiente intento
        return False
    
    def send_message_sync(self, chat_id: int, text: str, **kwargs) -> bool:
        """Wrapper síncrono para envío de mensajes"""
        return async_to_sync(self.send_message_safe)(chat_id, text, **kwargs)


# Instancia global del servicio de bot
bot_service = Bot(settings.BOT_TOKEN)


# --- Vistas principales ---
class BienvenidaView(APIView):
    """Vista de bienvenida mejorada"""
    
    def post(self, request):
        chat_id = request.data.get("chat_id")
        
        if not chat_id:
            return Response(
                {"error": "chat_id es requerido"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verificar si ya está autenticado
        if UserSessionManager.is_authenticated(chat_id):
            session = UserSessionManager.get_session(chat_id)
            username = session.get('username', 'Usuario')
            
            success = async_to_sync(bot_service.send_message)(
                chat_id=chat_id,
                text=MessageTemplates.AUTH_SUCCESS.format(username=username),
                reply_markup=TelegramMenuBuilder.get_main_menu(),
                parse_mode="Markdown"
            )
        else:
            success = async_to_sync(bot_service.send_message) (
                chat_id=chat_id,
                text=MessageTemplates.WELCOME,
                parse_mode="Markdown"
            )
        
        if success:
            return Response({"status": "welcome_sent"})
        else:
            return Response(
                {"error": "Error enviando mensaje de bienvenida"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class VerificarClaveView(APIView):
    """Vista de verificación de clave mejorada"""
    
    def post(self, request):
        clave_secreta = request.data.get("clave_secreta")
        username = request.data.get("username")
        chat_id = request.data.get("chat_id")
        
        # Validaciones
        if not all([clave_secreta, username, chat_id]):
            return Response(
                {"error": "clave_secreta, username y chat_id son requeridos"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verificar clave
        if clave_secreta != settings.FMSPORTS_KEY:
            async_to_sync(bot_service.send_message)(
                chat_id=chat_id,
                text=MessageTemplates.AUTH_FAILED,
                parse_mode="Markdown"
            )
            
            logger.warning(f"Intento de autenticación fallido para {username} (chat_id: {chat_id})")
            
            return Response(
                {"error": "Clave incorrecta"},
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        # Crear sesión
        if UserSessionManager.create_session(chat_id, username):
            async_to_sync(bot_service.send_message)(
                chat_id=chat_id,
                text=MessageTemplates.AUTH_SUCCESS.format(username=username),
                reply_markup=TelegramMenuBuilder.get_main_menu(),
                parse_mode="Markdown"
            )
            
            return Response({
                "status": "authenticated",
                "username": username,
                "session_timeout": USER_SESSION_TIMEOUT
            })
        else:
            return Response(
                {"error": "Error creando sesión"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class MostrarMenuView( APIView):
    """Vista de menú principal mejorada"""
    
    def post(self, request):
        chat_id = request.data.get("chat_id")
        session = UserSessionManager.get_session(chat_id)
        username = session.get('username', 'Usuario')
        
        success = async_to_sync(bot_service.send_message)(
            chat_id=chat_id,
            text=f"🎭 **¡Hola {username}!**\n\n¿Qué necesitas del VECO hoy?",
            reply_markup=TelegramMenuBuilder.get_main_menu(),
            parse_mode="Markdown"
        )
        
        if success:
            return Response({"status": "menu_sent"})
        else:
            return Response(
                {"error": "Error enviando menú"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class SolicitarModificar( APIView):
    """Vista para solicitar modificación de guión"""
    
    def post(self, request):
        chat_id = request.data.get("chat_id")
        titulo = request.data.get('titulo')
        
        if not chat_id:
            return Response(
                {"error": "chat_id es requerido"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        archivos = GoogleDriveService.obtener_documentos(titulo_busqueda=titulo)

        if not archivos:
            async_to_sync(bot_service.send_message)(
                chat_id=chat_id, 
                text=MessageTemplates.DOCUMENT_NOT_FOUND.format(titulo=titulo),
                parse_mode="Markdown"
            )
            return Response(
                {"status": "No se encontraron documentos"},
                status=status.HTTP_404_NOT_FOUND
            )

        markup = TelegramMenuBuilder.get_document_selection_menu(archivos)
        async_to_sync(bot_service.send_message)(
            chat_id=chat_id,
            text=MessageTemplates.DOCUMENT_SELECT,
            reply_markup=markup,
            parse_mode="Markdown"
        )

        return Response({"status": "Archivos enviados"})


class getFileId( APIView):
    """Vista para obtener ID de archivo"""
    
    def post(self, request):
        file_id = request.data.get("file_id")
        file_idSplitted = file_id.replace("archivo_", "")
        return Response({"id": file_idSplitted})


class SoporteInlineView(APIView):
    """Vista de soporte inline conectada a n8n webhook"""
    
    def post(self, request):
        chat_id = request.data.get("chat_id")
        action = request.data.get("action", "init")
        message = request.data.get("message", "")
        
        if action == "init":
            # Activar modo soporte y verificar conexión
            webhook_status = N8nWebhookService.test_webhook_connection()
            
            if not webhook_status:
                async_to_sync(bot_service.send_message)(
                    chat_id=chat_id,
                    text="❌ **Servicio temporalmente no disponible**\n\nEl asistente IA está experimentando problemas técnicos. Por favor intenta más tarde.",
                    parse_mode="Markdown"
                )
                return Response({
                    "error": "webhook_unavailable",
                    "status": "service_unavailable"
                })
            
            UserSessionManager.update_session(chat_id, is_support_mode=True)
            
            async_to_sync(bot_service.send_message)(
                chat_id=chat_id,
                text=MessageTemplates.SUPPORT_WELCOME,
                reply_markup=TelegramMenuBuilder.get_support_menu(),
                parse_mode="Markdown"
            )
            
            return Response({
                "status": "support_mode_activated",
                "webhook_status": "connected"
            })
        
        elif action == "chat":
            # Procesar mensaje de chat a través de n8n
            return self._process_support_message(chat_id, message)
        
        elif action == "exit":
            # Salir del modo soporte
            UserSessionManager.update_session(chat_id, is_support_mode=False)
            
            async_to_sync(bot_service.send_message)(
                chat_id=chat_id,
                text="✅ **Modo soporte desactivado**\n\n¿Necesitas algo más?",
                reply_markup=TelegramMenuBuilder.get_main_menu(),
                parse_mode="Markdown"
            )
            
            return Response({"status": "support_mode_deactivated"})
    
    def _process_support_message(self, chat_id: int, message: str) -> Response:
        """Procesar mensaje de soporte a través del webhook de n8n"""
        try:
            # Verificar comandos especiales primero
            if message.startswith('/'):
                return self._handle_support_command(chat_id, message)
            
            # Obtener información del usuario
            session = UserSessionManager.get_session(chat_id)
            username = session.get('username', f'user_{chat_id}')
            
            # Actualizar contador de conversaciones y última actividad
            conversation_count = session.get('conversation_count', 0) + 1
            UserSessionManager.update_session(
                chat_id,
                conversation_count=conversation_count,
                last_activity=datetime.now().isoformat()
            )
            
            # Enviar indicador de "escribiendo..."
            async_to_sync(bot_service.send_message)(
                chat_id=chat_id,
                text="🤖 _Procesando tu consulta..._",
                parse_mode="Markdown"
            )
            
            # Enviar mensaje al webhook de n8n
            webhook_response = N8nWebhookService.send_chat_message(
                chat_id=chat_id,
                message=message,
                username=username
            )
            
            if webhook_response["success"]:
                # Formatear respuesta del asistente
                ai_response = webhook_response["response"]
                
                # Limitar longitud de respuesta para Telegram
                if len(ai_response) > 4000:
                    ai_response = ai_response[:3900] + "\n\n_[Respuesta truncada por longitud]_"
                
                formatted_response = f"🤖 **Asistente VECO:**\n\n{ai_response}"
                
                success = async_to_sync(bot_service.send_message)(
                    chat_id=chat_id,
                    text=formatted_response,
                    parse_mode="Markdown"
                )
                
                if success:
                    return Response({
                        "status": "response_sent",
                        "response": ai_response,
                        "conversation_count": conversation_count,
                        "webhook_data": webhook_response.get("data", {})
                    })
                else:
                    return Response({
                        "error": "telegram_send_failed",
                        "ai_response": ai_response
                    })
                    
            else:
                # Manejar diferentes tipos de errores del webhook
                error_type = webhook_response.get("error", "unknown")
                error_message = webhook_response.get("message", "Error desconocido")
                
                if error_type == "timeout":
                    response_text = "⏰ **Tiempo de espera agotado**\n\nEl asistente está tardando más de lo normal. ¿Podrías reformular tu pregunta o intentar más tarde?"
                elif error_type == "connection_error":
                    response_text = "🔌 **Problema de conexión**\n\nNo puedo conectar con el asistente ahora. Intenta de nuevo en unos minutos."
                else:
                    response_text = f"❌ **Error técnico**\n\n{error_message}\n\n¿Podrías intentar con una pregunta diferente?"
                
                async_to_sync(bot_service.send_message)(
                    chat_id=chat_id,
                    text=response_text,
                    parse_mode="Markdown"
                )
                
                return Response({
                    "error": error_type,
                    "message": error_message,
                    "status": "webhook_error"
                })
                
        except Exception as e:
            logger.error(f"Error procesando mensaje de soporte para chat_id {chat_id}: {e}")
            
            async_to_sync(bot_service.send_message)(
                chat_id=chat_id,
                text="💥 **Error interno**\n\nHubo un problema técnico. El equipo ha sido notificado.\n\nPuedes intentar:\n• Reformular tu pregunta\n• Usar `/menu` para volver al menú\n• Contactar soporte técnico",
                parse_mode="Markdown"
            )
            
            return Response({
                "error": "internal_server_error",
                "details": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def _handle_support_command(self, chat_id: int, command: str) -> Response:
        """Manejar comandos especiales del soporte"""
        session = UserSessionManager.get_session(chat_id)
        
        if command == "/menu":
            UserSessionManager.update_session(chat_id, is_support_mode=False)
            async_to_sync(bot_service.send_message)(
                chat_id=chat_id,
                text="🔙 Volviendo al menú principal...",
                reply_markup=TelegramMenuBuilder.get_main_menu()
            )
            
        elif command == "/help":
            help_text = """
🆘 **Ayuda del Soporte IA - VECO**

**Comandos disponibles:**
• `/menu` - Volver al menú principal
• `/help` - Mostrar esta ayuda
• `/status` - Estado del asistente

**¿Cómo funciona?**
Estás conectado a un asistente IA especializado en FMSPORTS a través de n8n. Simplemente escribe tu pregunta en lenguaje natural.

**Tipos de consulta que puedo resolver:**
• 📝 Ayuda con guiones deportivos
• 🔧 Soporte técnico de la plataforma
• 📊 Consultas sobre procesos FMSPORTS
• 💡 Ideas creativas para contenido
• ❓ Preguntas generales

**Ejemplos de preguntas:**
• "¿Cómo escribir un guión para un partido de fútbol?"
• "Necesito ideas para introducir un partido"
• "¿Cuáles son las mejores prácticas para narración deportiva?"

¡Pregunta lo que necesites!
            """
            async_to_sync(bot_service.send_message)(
                chat_id=chat_id,
                text=help_text,
                parse_mode="Markdown"
            )
            
        elif command == "/status":
            # Verificar estado del webhook
            webhook_status = N8nWebhookService.test_webhook_connection()
            conversation_count = session.get('conversation_count', 0)
            last_activity = session.get('last_activity', 'N/A')
            
            status_text = f"""
📊 **Estado del Asistente IA**

**Conexión n8n:** {'🟢 Conectado' if webhook_status else '🔴 Desconectado'}
**Conversaciones esta sesión:** {conversation_count}
**Última actividad:** {last_activity}
**Modo soporte:** {'🟢 Activo' if session.get('is_support_mode') else '🔴 Inactivo'}

**Webhook URL:** `{N8N_CHAT_WEBHOOK_URL[:50]}...`

{'✅ Todo funcionando correctamente' if webhook_status else '⚠️ Problemas de conectividad detectados'}
            """
            
            async_to_sync(bot_service.send_message)(
                chat_id=chat_id,
                text=status_text,
                parse_mode="Markdown"
            )
            
        else:
            async_to_sync(bot_service.send_message)(
                chat_id=chat_id,
                text=f"❓ Comando desconocido: `{command}`\n\nUsa `/help` para ver comandos disponibles.",
                parse_mode="Markdown"
            )
        
        return Response({"status": "command_processed", "command": command})


class SeleccionarTituloGuion(APIView):
    """Vista para seleccionar título del guión"""
    
    def post(self, request):
        chat_id = request.data.get("chat_id")
        
        if not chat_id:
            return Response(
                {"error": "chat_id es requerido"},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        async_to_sync(bot_service.send_message)(
            chat_id=chat_id,
            text="¿Cómo deseas nombrar tu guión?",
            reply_markup=TelegramMenuBuilder.get_title_selection_menu(),
            parse_mode="Markdown"
        )
        
        return Response(
            {"status": "Solicitud de nombre enviada"},
            status=status.HTTP_200_OK
        )


class CrearGuionView(APIView):
    """Vista para crear guión en Google Drive"""
    
    def post(self, request):
        chat_id = request.data.get("chat_id")
        prompt_content = request.data.get("content")
        titulo = request.data.get("titulo")
        
        # Validar parámetros obligatorios
        if not all([chat_id, prompt_content]):
            return Response(
                {"error": "chat_id y content son requeridos"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Obtener nombre de carpeta
            service = GoogleDriveService.get_service()
            folder_info = service.files().get(
                fileId=settings.GOOGLE_FOLDER_ID,
                fields="name"
            ).execute()
            
            nombre_carpeta = folder_info.get("name", "Carpeta VECO")
            currDate = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Generar título si no se proporciona
            if not titulo:
                titulo = f"Guion FMSPORTS - {datetime.now().strftime('%Y-%m-%d')}"
            
            # Crear documento
            doc_id = GoogleDriveService.crear_documento(
                titulo=titulo,
                contenido=prompt_content
            )
            
            # Enviar confirmación
            async_to_sync(bot_service.send_message)(
                chat_id=chat_id,
                text=MessageTemplates.DOCUMENT_CREATED.format(
                    titulo=titulo,
                    carpeta=nombre_carpeta,
                    fecha=currDate
                ),
                parse_mode="Markdown",
            )

            return Response({
                "status": "success",
                "fileName": titulo,
                "dateCreated": currDate,
                "folderId": settings.GOOGLE_FOLDER_ID,
                "docId": doc_id
            })

        except HttpError as error:
            error_details = error.content.decode('utf-8') if error.content else str(error)
            async_to_sync(bot_service.send_message)(
                chat_id=chat_id, 
                text=MessageTemplates.DOCUMENT_ERROR.format(error=error_details),
                parse_mode="Markdown"
            )
            return Response(
                {"error": f"Google API Error: {error_details}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
            
        except Exception as e:
            logger.error(f"Error al crear guión: {str(e)}")
            async_to_sync(bot_service.send_message)(
                chat_id=chat_id, 
                text=MessageTemplates.DOCUMENT_ERROR.format(error=str(e)),
                parse_mode="Markdown"
            )
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class PerfilUsuarioView(APIView):
    """Vista para mostrar perfil de usuario"""
    
    def post(self, request):
        chat_id = request.data.get("chat_id")
        session = UserSessionManager.get_session(chat_id)
        
        profile_text = f"""
👤 **Tu Perfil VECO**

**Usuario:** {session.get('username', 'N/A')}
**Sesión creada:** {session.get('created_at', 'N/A')}
**Modo soporte:** {'Activo' if session.get('is_support_mode') else 'Inactivo'}
**Conversaciones IA:** {session.get('conversation_count', 0)}
**Última actividad:** {session.get('last_activity', 'N/A')}

🔧 **Opciones:**
        """
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Estado del asistente", callback_data="profile:webhook_status")],
            [InlineKeyboardButton("🔄 Reiniciar conversación IA", callback_data="profile:reset_conversation")],
            [InlineKeyboardButton("🔙 Volver al menú", callback_data="action:menu")]
        ])
        
        async_to_sync(bot_service.send_message)(
            chat_id=chat_id,
            text=profile_text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        
        return Response({"status": "profile_sent"})


class WebhookStatusView(APIView):
    """Vista para verificar estado del webhook de n8n"""
    
    def post(self, request):
        chat_id = request.data.get("chat_id")
        
        # Probar conexión con el webhook
        webhook_status = N8nWebhookService.test_webhook_connection()
        
        # Obtener estadísticas de la sesión
        session = UserSessionManager.get_session(chat_id)
        conversation_count = session.get('conversation_count', 0)
        
        status_text = f"""
🔗 **Estado del Webhook N8N**

**URL:** `conexionai.app.n8n.cloud`
**Estado:** {'🟢 Operativo' if webhook_status else '🔴 Sin conexión'}
**Timeout configurado:** {WEBHOOK_TIMEOUT}s
**Conversaciones esta sesión:** {conversation_count}

**Asistente OpenAI:**
{'✅ Disponible a través de n8n' if webhook_status else '❌ No disponible'}

**Recomendaciones:**
{
'• Todo funcionando correctamente' if webhook_status 
else '• Verifica tu conexión a internet\\n• Intenta de nuevo en unos minutos\\n• Contacta al administrador si persiste'
}
        """
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Probar conexión", callback_data="webhook:test")],
            [InlineKeyboardButton("🔙 Volver al perfil", callback_data="action:perfil")]
        ])
        
        async_to_sync(bot_service.send_message)(
            chat_id=chat_id,
            text=status_text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        
        return Response({
            "status": "webhook_status_sent",
            "webhook_connected": webhook_status,
            "conversation_count": conversation_count
        })


class LogoutView(APIView):
    """Vista para cerrar sesión"""
    
    def post(self, request):
        chat_id = request.data.get("chat_id")
        session = UserSessionManager.get_session(chat_id)
        username = session.get('username', 'Usuario') if session else 'Usuario'
        
        UserSessionManager.destroy_session(chat_id)
        
        async_to_sync(bot_service.send_message)(
            chat_id=chat_id,
            text=f"👋 **¡Hasta luego {username}!**\n\nTu sesión ha sido cerrada correctamente.\n\nPara volver a usar el VECO, simplemente envía /start",
            parse_mode="Markdown"
        )
        
        return Response({"status": "logged_out"})