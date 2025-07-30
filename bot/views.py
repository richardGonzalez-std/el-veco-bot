# views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from asgiref.sync import async_to_sync
from rest_framework import status
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from openai import OpenAI
from django.conf import settings
import re
import time    
import markdown
from googleapiclient.errors import HttpError
from datetime import datetime
from googleapiclient.http import MediaIoBaseUpload
import time
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io
import requests
import json
import logging
import uuid

SCOPES = ["https://www.googleapis.com/auth/drive.metadata.readonly"]

threads_usuarios = set()
user_timeout = 7200
modo_soporte_usuarios = set()

logger = logging.getLogger(__name__)

class MessageTemplates:
    """Plantillas de mensajes más profesionales"""
    
    WELCOME = "🎭 **¡Bienvenido al VECO!**\n\nSoy tu asistente para la creación y gestión de guiones deportivos.\n\nPara comenzar, necesito que te autentiques:"
    WELCOME_INLINE = "🎭 **¡Bienvenido al VECO!**\n\nSoy tu asistente para la creación y gestión de guiones deportivos.\n\nSelecciona una de las opciones disponibles:"
    AUTH_SUCCESS = "✅ **¡Autenticación exitosa!**\n\n¡Hola {username}! Ya puedes usar todas las funciones del VECO."
    
    AUTH_FAILED = "❌ **Clave incorrecta**\n\nPor favor, verifica tu clave e intenta nuevamente."
    
    SUPPORT_WELCOME = "🤖 **Soporte IA Inline Activado**\n\nAhora puedes chatear directamente conmigo a través de tu asistente personalizado de n8n.\n\nSimplemente escribe tu consulta y te responderé de inmediato.\n\n💡 **Comandos disponibles:**\n• `/menu` - Volver al menú principal\n• `/help` - Ver ayuda\n• `/status` - Estado del asistente\n\n🎯 **Especialidades:**\n• Ayuda con guiones deportivos\n• Soporte técnico de la plataforma\n• Consultas sobre FMSPORTS"
    
    SESSION_EXPIRED = "⏰ **Sesión expirada**\n\nTu sesión ha caducado por inactividad. Por favor, auténticate nuevamente."


def obtener_menu_inline():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎬 Generar guion", callback_data="guion")],
            [InlineKeyboardButton("✏️ Modificar guion", callback_data="modificar")],
            [InlineKeyboardButton("🤖 Modo soporte GPT", callback_data="soporte")],
        ]
    )


def is_session_active(chat_id):
    for cid, username, timestamp in list(threads_usuarios):
        if cid == chat_id:
            if time.time() - timestamp < user_timeout:
                return True
            else:
                threads_usuarios.remove((cid, username, timestamp))
                return False
    return False


class MostrarMenuView(APIView):

    def post(self, request):
        chat_id = request.data.get("chat_id")
        bot = Bot(token=settings.BOT_TOKEN)
        if not is_session_active(chat_id):
            async_to_sync(bot.send_message)(
                chat_id=chat_id,
                text=MessageTemplates.SESSION_EXPIRED
            )
            return Response({"error": "Sesión expirada"}, status=status.HTTP_401_UNAUTHORIZED)
        async_to_sync(bot.send_message)(
            chat_id,
            text=MessageTemplates.WELCOME_INLINE,
            reply_markup=obtener_menu_inline(),
        )
        return Response({"respuesta": "Menú enviado"})


class BienvenidaView(APIView):
    def post(self, request):
        bot = Bot(token=settings.BOT_TOKEN)
        chat_id = request.data.get("chat_id")
        async_to_sync(bot.sendMessage)(
            chat_id=chat_id,
            text=MessageTemplates.WELCOME,
        )
        return Response({"respuesta": f"Esperando clave {status.HTTP_100_CONTINUE}"})


class VerificarClaveView(APIView):
    def post(self, request):
        clave_secreta = request.data.get("clave_secreta")
        username = request.data.get("username")
        chat_id = request.data.get("chat_id")
        bot = Bot(token=settings.BOT_TOKEN)
        if clave_secreta != settings.CLAVE_ACCESO or not chat_id:
            async_to_sync(bot.send_message)(
                chat_id = chat_id,
                text=MessageTemplates.AUTH_FAILED
            )
            return Response({
                "respuesta":f"Verificación incorrecta {status.HTTP_401_UNAUTHORIZED}",
                "status_code": status.HTTP_401_UNAUTHORIZED
            })
        threads_usuarios.add((chat_id,username,time.time()))
        async_to_sync(bot.send_message)(
            chat_id=chat_id,
            text=MessageTemplates.WELCOME_INLINE,
            reply_markup=obtener_menu_inline(),
        )

        return Response(
            {
                "respuesta": f"Verificación de clave correcta, {status.HTTP_202_ACCEPTED}",
                "status_code": status.HTTP_202_ACCEPTED
            }
        )
            

def obtener_documentos_google_docs(titulo_busqueda=None):
    creds = service_account.Credentials.from_service_account_file(
        settings.GOOGLE_CREDS_PATH,
        scopes=SCOPES
    )
    service = build('drive', 'v3', credentials=creds)

    # Filtro base: documentos dentro de la carpeta
    filtro_base = f"'{settings.GOOGLE_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.document'"

    resultados = service.files().list(
        q=filtro_base,
        pageSize=10,
        fields="files(id,name)"
    ).execute()

    archivos = resultados.get("files", [])
    #Si se proporciona un titulo
    if titulo_busqueda:
        titulo_busqueda = titulo_busqueda.strip().lower()
        archivos = [a for a in archivos if a['name'].strip().lower() == titulo_busqueda]
    return archivos


class SolicitarModificar(APIView):
    def post(self, request):
        chat_id = request.data.get("chat_id")
        titulo = request.data.get('titulo')
        bot = Bot(token=settings.BOT_TOKEN)
        if not is_session_active(chat_id):
            async_to_sync(bot.send_message)(
                chat_id=chat_id,
                text=MessageTemplates.SESSION_EXPIRED
            )
            return Response({"error": "Sesión expirada"}, status=status.HTTP_401_UNAUTHORIZED)
        archivos = obtener_documentos_google_docs(titulo_busqueda=titulo)
        if not chat_id:
            return Response(
                {"error": "chat_id es requerido"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not archivos:
            async_to_sync(bot.send_message)(chat_id=chat_id, text="No hay archivos con ese nombre. Verifica el titulo e intenta nuevamente")
        botones = [
            [
                InlineKeyboardButton(
                    archivo["name"],
                    callback_data=f"archivo_{archivo['id']}",
                )
            ]
            for archivo in archivos
        ]
        markup = InlineKeyboardMarkup(botones)
        async_to_sync(bot.send_message)(
            chat_id=chat_id,
            text="Selecciona un archivo para modificar: ",
            reply_markup=markup,
        )
        return Response({"status": "mensaje enviado"})


class getFileId(APIView):
    def post(self, request):
        file_id = request.data.get("file_id")
        file_idSplitted = file_id.replace("archivo_", "")
        return Response({"id": file_idSplitted})


# CLASE ACTUALIZADA CON N8N CHAT TRIGGER
class SoporteGPT(APIView):
    def post(self, request):
        chat_id = request.data.get("chat_id")
        bot = Bot(token=settings.BOT_TOKEN)
        
        # Verificar sesión activa
        if not is_session_active(chat_id):
            async_to_sync(bot.send_message)(
                chat_id=chat_id,
                text=MessageTemplates.SESSION_EXPIRED
            )
            return Response({"error": "Sesión expirada"}, status=status.HTTP_401_UNAUTHORIZED)
        
        # Activar modo soporte si no está activo
        if chat_id not in modo_soporte_usuarios:
            modo_soporte_usuarios.add(chat_id)
            
            # Enviar mensaje de bienvenida por Telegram
            async_to_sync(bot.send_message)(
                chat_id=chat_id,
                text=MessageTemplates.SUPPORT_WELCOME,
            )
        
        # Retornar información para el chat embedded
        return Response({
            "chat": "Abierto", 
            "status": "inline_activated",
            "webhook_url": settings.WEBHOOK_URL,  # URL del n8n Chat Trigger
            "session_id": f"support_{chat_id}_{int(time.time())}"
        })


# NUEVA CLASE PARA MANEJAR N8N CHAT TRIGGER
class N8nChatHandler(APIView):
    """Maneja la comunicación con n8n Chat Trigger webhook"""
    
    def post(self, request):
        try:
            chat_id = request.data.get("chat_id")
            mensaje = request.data.get("message")
            session_id = request.data.get("session_id", f"support_{chat_id}")
            
            if not chat_id or not mensaje:
                return Response(
                    {"error": "chat_id y message son requeridos"}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Verificar que el chat tenga soporte activo
            if chat_id not in modo_soporte_usuarios:
                return Response(
                    {"error": "Chat no tiene soporte activo"}, 
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Enviar mensaje al n8n Chat Trigger
            respuesta = self._enviar_a_n8n_chat(mensaje, session_id, chat_id)
            
            if respuesta:
                return Response({
                    "status": "success",
                    "response": respuesta.get("output", "Sin respuesta"),
                    "session_id": respuesta.get("sessionId", session_id)
                })
            else:
                return Response(
                    {"error": "Error comunicando con n8n"}, 
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
        except Exception as e:
            logger.error(f"Error en N8nChatHandler: {str(e)}")
            return Response(
                {"error": "Error interno"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _enviar_a_n8n_chat(self, mensaje, session_id, chat_id):
        """Envía mensaje al n8n Chat Trigger y retorna la respuesta"""
        try:
            # Payload en el formato que espera n8n Chat Trigger
            payload = {
                "chatInput": mensaje,
                "sessionId": session_id,
                "action": "sendMessage",
                # Datos adicionales para tu workflow
                "metadata": {
                    "telegram_chat_id": chat_id,
                    "platform": "telegram_veco",
                    "timestamp": time.time()
                }
            }
            
            headers = {
                'Content-Type': 'application/json',
                'User-Agent': 'VECO-Bot/1.0'
            }
            
            # Si tienes autenticación básica configurada en n8n
            auth = None
            if hasattr(settings, 'N8N_WEBHOOK_AUTH'):
                auth = settings.N8N_WEBHOOK_AUTH  # ('username', 'password')
            
            response = requests.post(
                settings.WEBHOOK_URL,  # Tu n8n Chat Trigger webhook URL
                json=payload,
                headers=headers,
                auth=auth,
                timeout=30  # n8n puede tardar en responder
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"n8n respondió con status {response.status_code}: {response.text}")
                return None
                
        except requests.RequestException as e:
            logger.error(f"Error conectando con n8n Chat Trigger: {str(e)}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Error decodificando respuesta de n8n: {str(e)}")
            return None


# NUEVA CLASE PARA RECIBIR CALLBACKS DE N8N (OPCIONAL)
class N8nWebhookReceiver(APIView):
    """Recibe callbacks desde n8n si configuraste respuestas asíncronas"""
    
    def post(self, request):
        try:
            session_id = request.data.get("sessionId")
            output = request.data.get("output")
            chat_id = request.data.get("telegram_chat_id")
            
            if chat_id and output:
                # Enviar respuesta por Telegram si es necesario
                bot = Bot(token=settings.BOT_TOKEN)
                async_to_sync(bot.send_message)(
                    chat_id=chat_id,
                    text=f"🤖 **Respuesta del asistente:**\n\n{output}",
                    parse_mode="Markdown"
                )
            
            return Response({"status": "received"})
            
        except Exception as e:
            logger.error(f"Error en N8nWebhookReceiver: {str(e)}")
            return Response(
                {"error": "Error procesando callback"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class SeleccionarTituloGuion(APIView):
    def post(self, request):
        chat_id = request.data.get("chat_id")
        bot = Bot(token=settings.BOT_TOKEN)
        if not is_session_active(chat_id):
            async_to_sync(bot.send_message)(
                chat_id=chat_id,
                text=MessageTemplates.SESSION_EXPIRED
            )
            return Response({"error": "Sesión expirada"}, status=status.HTTP_401_UNAUTHORIZED)
        async_to_sync(bot.send_message)(
            chat_id=chat_id,
            text="Desea el guión con un nombre personalizado o una designado por el sistema?",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="Nombre personalizado",
                            callback_data="nombre_personalizado",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="Designado por el bot", callback_data="nombre_default"
                        )
                    ],
                ]
            ),
        )
        return Response({"seleccion": "recibida"})


def crearDocumento(drive_service, titulo, contenido):
    archivo_metadata = {
        "name": f"{titulo}",
        "mimeType": "application/vnd.google-apps.document",
        "parents": [settings.GOOGLE_FOLDER_ID],
    }

    media = MediaIoBaseUpload(
       io.BytesIO(contenido.encode("utf-8")),
       mimetype="text/html",
       resumable=True
   )
    archivo = (drive_service.files().create(
        body=archivo_metadata,
        media_body=media,
        fields="id",
    ).execute())
    return archivo.get("id")


class CrearGuionView(APIView):
    def post(self, request):
        bot = Bot(token=settings.BOT_TOKEN)
        chat_id = request.data.get("chat_id")
        prompt_content = request.data.get("content")
        titulo = request.data.get("titulo")
        if not is_session_active(chat_id):
            async_to_sync(bot.send_message)(
                chat_id=chat_id,
                text=MessageTemplates.SESSION_EXPIRED
            )
            return Response({"error": "Sesión expirada"}, status=status.HTTP_401_UNAUTHORIZED)
        if not chat_id or not prompt_content:
            return Response(
                {"error": "chat_id y content son requeridos"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Autenticación
            creds = service_account.Credentials.from_service_account_file(
                settings.GOOGLE_CREDS_PATH,
                scopes=[
                    "https://www.googleapis.com/auth/drive",
                    "https://www.googleapis.com/auth/documents",
                ],
            )
            drive_service = build("drive", "v3", credentials=creds)
            docs_service = build("docs", "v1", credentials=creds)
            folder = (
                drive_service.files()
                .get(fileId=settings.GOOGLE_FOLDER_ID, fields="name")
                .execute()
            )
            nombre_carpeta = folder.get("name", "Carpeta")
            currDate = time.ctime(time.time())
            # Crear el título
            if titulo:
                crearDocumento(drive_service, titulo, prompt_content)
                # Enviar mensaje informativo al usuario
                async_to_sync(bot.send_message)(
                    chat_id=chat_id,
                    text=f"✅ Tu guión *{titulo}* fue creado exitosamente en la carpeta *{nombre_carpeta}* de Google Drive.",
                    parse_mode="Markdown",
                )

                return Response({"status": "Documento creado","fileName":f"{titulo}","dateCreated":currDate,"folderId":settings.GOOGLE_FOLDER_ID})

            fecha = datetime.now().strftime("%Y-%m-%d")
            titulo_auto = f"Guión FMSPORTS *{fecha}*"
            crearDocumento(
                drive_service=drive_service,
                titulo=titulo_auto,
                contenido=prompt_content,
            )

            # Enviar mensaje informativo al usuario
            async_to_sync(bot.send_message)(
                chat_id=chat_id,
                text=f"✅ Tu guión *{titulo_auto}* fue creado exitosamente en la carpeta *{nombre_carpeta}* de Google Drive.",
                parse_mode="Markdown",
            )

            return Response({"status": "Documento creado","fileName":f"{titulo_auto}","dateCreated":currDate,"folderId":settings.GOOGLE_FOLDER_ID})

        except HttpError as error:
            async_to_sync(bot.send_message)(
                chat_id=chat_id, text="❌ Ocurrió un error al crear el documento."
            )
            return Response(
                {"error": str(error)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )