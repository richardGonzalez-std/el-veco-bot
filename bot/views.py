# views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from asgiref.sync import async_to_sync
from rest_framework import status
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from openai import OpenAI
import requests
import json
from rest_framework.decorators import api_view
from django.http import JsonResponse
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
import logging

# Configurar logging
logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.metadata.readonly"]

client = OpenAI(api_key=settings.OPENAI_KEY)
threads_usuarios = set()
modo_soporte_usuarios = set()
clave_acceso_bot = "FMSPORTS2025"

# URL del webhook para chat embedded
CHAT_WEBHOOK_URL = "https://conexionai.app.n8n.cloud/webhook/2305f427-849e-4111-a040-25e5de928328/chat"

def obtener_menu_inline():
    """
    Menú principal con todas las opciones incluyendo chat embedded
    """
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎬 Generar guion", callback_data="guion")],
            [InlineKeyboardButton("✏️ Modificar guion", callback_data="modificar")],
            [InlineKeyboardButton("🤖 Chat IA Directo", callback_data="chat_directo")],
        ]
    )

class MostrarMenuView(APIView):
    def post(self, request):
        chat_id = request.data.get("chat_id")
        bot = Bot(token=settings.BOT_TOKEN)
        async_to_sync(bot.send_message)(
            chat_id,
            text="Atención a la música, aquí el VECO, ¿qué necesitas de mi?",
            reply_markup=obtener_menu_inline(),
        )
        return Response({"respuesta": "Menú enviado"})

class ChatEmbeddedView(APIView):
    """
    Vista para manejar conversaciones directamente con el webhook de n8n
    """
    def post(self, request):
        chat_id = request.data.get("chat_id")
        message = request.data.get("message")
        user_id = request.data.get("user_id", chat_id)
        
        if not chat_id or not message:
            return Response(
                {"error": "chat_id y message son requeridos"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        try:
            # Preparar datos para enviar al webhook
            webhook_data = {
                "message": message,
                "user_id": str(user_id),
                "chat_id": str(chat_id),
                "timestamp": datetime.now().isoformat(),
                "source": "telegram_bot"
            }
            
            logger.info(f"Enviando mensaje al webhook: {webhook_data}")
            
            # Enviar mensaje al webhook de n8n
            response = requests.post(
                CHAT_WEBHOOK_URL,
                json=webhook_data,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "VECO-Bot/1.0"
                },
                timeout=30
            )
            
            logger.info(f"Respuesta del webhook: {response.status_code} - {response.text}")
            
            if response.status_code == 200:
                try:
                    # Obtener respuesta del webhook
                    webhook_response = response.json()
                    ai_response = webhook_response.get("response", webhook_response.get("message", "No se pudo obtener respuesta de la IA"))
                    
                    # Si la respuesta es un string, usarla directamente
                    if isinstance(webhook_response, str):
                        ai_response = webhook_response
                    
                except json.JSONDecodeError:
                    # Si no es JSON, usar el texto plano
                    ai_response = response.text
                
                # Enviar respuesta al usuario via Telegram
                bot = Bot(token=settings.BOT_TOKEN)
                
                # Truncar mensaje si es muy largo (Telegram tiene límite de 4096 caracteres)
                if len(ai_response) > 4000:
                    ai_response = ai_response[:4000] + "...\n\n_Mensaje truncado por longitud_"
                
                async_to_sync(bot.send_message)(
                    chat_id=chat_id,
                    text=ai_response,
                    parse_mode="Markdown"
                )
                
                return Response({
                    "status": "success",
                    "ai_response": ai_response,
                    "webhook_status": response.status_code
                })
            else:
                # Manejar errores del webhook
                bot = Bot(token=settings.BOT_TOKEN)
                async_to_sync(bot.send_message)(
                    chat_id=chat_id,
                    text="❌ Error al conectar con el asistente de IA. Inténtalo de nuevo."
                )
                
                return Response(
                    {"error": f"Webhook error: {response.status_code}"},
                    status=status.HTTP_502_BAD_GATEWAY
                )
                
        except requests.exceptions.Timeout:
            bot = Bot(token=settings.BOT_TOKEN)
            async_to_sync(bot.send_message)(
                chat_id=chat_id,
                text="⏱️ El asistente está tardando en responder. Inténtalo de nuevo en unos momentos."
            )
            
            return Response(
                {"error": "Timeout al conectar con el webhook"},
                status=status.HTTP_408_REQUEST_TIMEOUT
            )
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error en ChatEmbeddedView: {str(e)}")
            bot = Bot(token=settings.BOT_TOKEN)
            async_to_sync(bot.send_message)(
                chat_id=chat_id,
                text="❌ Error de conexión con el asistente de IA."
            )
            
            return Response(
                {"error": f"Error de conexión: {str(e)}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

class ChatSesionView(APIView):
    """
    Vista para manejar sesiones de chat continuas
    """
    def post(self, request):
        chat_id = request.data.get("chat_id")
        action = request.data.get("action")  # 'start', 'end', 'status'
        
        bot = Bot(token=settings.BOT_TOKEN)
        
        if action == "start":
            # Agregar usuario al modo soporte
            modo_soporte_usuarios.add(chat_id)
            
            async_to_sync(bot.send_message)(
                chat_id=chat_id,
                text="🤖 *Modo Chat IA Activado*\n\n"
                     "Ahora puedes enviar mensajes directamente y recibirás respuestas de la IA.\n\n"
                     "Para salir del modo chat, envía: `/salir_chat`\n"
                     "Para ver el menú, envía: `/menu`",
                parse_mode="Markdown"
            )
            
            return Response({"status": "Chat session started"})
            
        elif action == "end":
            # Remover usuario del modo soporte
            modo_soporte_usuarios.discard(chat_id)
            
            async_to_sync(bot.send_message)(
                chat_id=chat_id,
                text="✅ *Modo Chat IA Desactivado*\n\n"
                     "Has salido del modo chat. Usa el menú para acceder a otras funciones.",
                parse_mode="Markdown",
                reply_markup=obtener_menu_inline()
            )
            
            return Response({"status": "Chat session ended"})
            
        elif action == "status":
            is_active = chat_id in modo_soporte_usuarios
            return Response({
                "chat_active": is_active,
                "chat_id": chat_id
            })
            
        return Response(
            {"error": "Acción no válida"},
            status=status.HTTP_400_BAD_REQUEST
        )

class ManejarMensajesChatView(APIView):
    """
    Vista para manejar mensajes cuando el usuario está en modo chat
    """
    def post(self, request):
        chat_id = request.data.get("chat_id")
        message = request.data.get("message")
        user_id = request.data.get("user_id", chat_id)
        
        if not chat_id or not message:
            return Response(
                {"error": "chat_id y message son requeridos"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Verificar si el usuario está en modo chat
        if chat_id not in modo_soporte_usuarios:
            return Response(
                {"error": "Usuario no está en modo chat"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verificar comandos especiales
        if message.lower() in ['/salir_chat', '/exit', '/quit']:
            # Crear objeto request simulado para ChatSesionView
            mock_request = type('MockRequest', (), {
                'data': {'chat_id': chat_id, 'action': 'end'}
            })()
            return ChatSesionView().post(mock_request)
        
        if message.lower() == '/menu':
            bot = Bot(token=settings.BOT_TOKEN)
            async_to_sync(bot.send_message)(
                chat_id=chat_id,
                text="Aquí tienes el menú (seguirás en modo chat):",
                reply_markup=obtener_menu_inline()
            )
            return Response({"status": "menu_sent"})
        
        # Enviar mensaje al chat embedded
        chat_view = ChatEmbeddedView()
        # Actualizar los datos del request
        request.data['user_id'] = user_id
        return chat_view.post(request)

class TelegramWebhookView(APIView):
    """
    Vista principal para manejar todas las actualizaciones de Telegram
    """
    def post(self, request):
        update = request.data
        
        try:
            # Manejar mensajes de texto
            if 'message' in update:
                message = update['message']
                chat_id = message['chat']['id']
                user_id = message['from']['id']
                text = message.get('text', '')
                
                # Verificar si el usuario está en modo chat
                if chat_id in modo_soporte_usuarios:
                    # Procesar como mensaje de chat
                    mock_request = type('MockRequest', (), {
                        'data': {
                            'chat_id': chat_id,
                            'message': text,
                            'user_id': user_id
                        }
                    })()
                    
                    chat_view = ManejarMensajesChatView()
                    return chat_view.post(mock_request)
                
                # Manejar comandos especiales
                if text.startswith('/'):
                    return self.handle_command(chat_id, text)
                
                # Respuesta por defecto
                bot = Bot(token=settings.BOT_TOKEN)
                async_to_sync(bot.send_message)(
                    chat_id=chat_id,
                    text="Usa el menú para interactuar conmigo 👇",
                    reply_markup=obtener_menu_inline()
                )
                
            # Manejar callback queries (botones inline)
            elif 'callback_query' in update:
                return self.handle_callback_query(update['callback_query'])
                
        except Exception as e:
            logger.error(f"Error en TelegramWebhookView: {str(e)}")
            return Response({"error": "Internal server error"}, status=500)
        
        return Response({"status": "ok"})
    
    def handle_command(self, chat_id, command):
        """Manejar comandos especiales"""
        bot = Bot(token=settings.BOT_TOKEN)
        
        if command == '/start':
            async_to_sync(bot.send_message)(
                chat_id=chat_id,
                text="¡Hola! Soy el VECO. Usa el menú para interactuar conmigo.",
                reply_markup=obtener_menu_inline()
            )
        elif command == '/menu':
            async_to_sync(bot.send_message)(
                chat_id=chat_id,
                text="Aquí tienes el menú principal:",
                reply_markup=obtener_menu_inline()
            )
        elif command == '/chat':
            # Activar modo chat directo
            mock_request = type('MockRequest', (), {
                'data': {'chat_id': chat_id, 'action': 'start'}
            })()
            chat_view = ChatSesionView()
            return chat_view.post(mock_request)
        
        return Response({"status": "command_handled"})
    
    def handle_callback_query(self, callback_query):
        """Manejar callbacks de botones inline"""
        chat_id = callback_query['message']['chat']['id']
        data = callback_query['data']
        
        bot = Bot(token=settings.BOT_TOKEN)
        
        # Responder al callback query
        async_to_sync(bot.answer_callback_query)(
            callback_query_id=callback_query['id']
        )
        
        # Procesar según el callback
        if data == "chat_directo":
            # Activar modo chat directo
            mock_request = type('MockRequest', (), {
                'data': {'chat_id': chat_id, 'action': 'start'}
            })()
            chat_view = ChatSesionView()
            return chat_view.post(mock_request)
            
        elif data == "guion":
            # Manejar generación de guión
            mock_request = type('MockRequest', (), {
                'data': {'chat_id': chat_id}
            })()
            titulo_view = SeleccionarTituloGuion()
            return titulo_view.post(mock_request)
            
        elif data == "modificar":
            # Manejar modificación de guión
            async_to_sync(bot.send_message)(
                chat_id=chat_id,
                text="Envía el título del guión que quieres modificar:"
            )
            
        elif data == "soporte":
            # Manejar soporte GPT web
            mock_request = type('MockRequest', (), {
                'data': {'chat_id': chat_id}
            })()
            soporte_view = SoporteGPT()
            return soporte_view.post(mock_request)
        
        return Response({"status": "callback_handled"})

class BienvenidaView(APIView):
    def post(self, request):
        bot = Bot(token=settings.BOT_TOKEN)
        chat_id = request.data.get("chat_id")
        async_to_sync(bot.sendMessage)(
            chat_id=chat_id,
            text="Bienvenido al VECO, entra al siguiente link para introducir tu nombre y la clave"
            " secreta",
        )
        return Response({"respuesta": f"Esperando clave {status.HTTP_100_CONTINUE}"})

class VerificarClaveView(APIView):
    def post(self, request):
        clave_secreta = request.data.get("clave_secreta")
        username = request.data.get("username")
        chat_id = request.data.get("chat_id")
        bot = Bot(token=settings.BOT_TOKEN)
        
        if clave_secreta != clave_acceso_bot:
            async_to_sync(bot.send_message)(
                chat_id=chat_id,
                text=f"Verificación de clave incorrecta, intente de nuevo"
            )
            return Response({
                "respuesta": f"Verificación incorrecta, {status.HTTP_401_UNAUTHORIZED}"
            })
        
        async_to_sync(bot.send_message)(
            chat_id=chat_id,
            text=f"Atención a la música! Bienvenido {username}, ¿qué necesitas de mi?",
            reply_markup=obtener_menu_inline(),
        )

        return Response({
            "respuesta": f"Verificación de clave correcta, {status.HTTP_202_ACCEPTED}"
        })

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
    # Si se proporciona un titulo
    if titulo_busqueda:
        titulo_busqueda = titulo_busqueda.strip().lower()
        archivos = [a for a in archivos if a['name'].strip().lower() == titulo_busqueda]
    return archivos

class SolicitarModificar(APIView):
    def post(self, request):
        chat_id = request.data.get("chat_id")
        titulo = request.data.get('titulo')
        bot = Bot(token=settings.BOT_TOKEN)
        
        if not chat_id:
            return Response(
                {"error": "chat_id es requerido"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        archivos = obtener_documentos_google_docs(titulo_busqueda=titulo)
        
        if not archivos:
            async_to_sync(bot.send_message)(
                chat_id=chat_id, 
                text="No hay archivos con ese nombre. Verifica el título e intenta nuevamente"
            )
            return Response({"status": "archivo_no_encontrado"})

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

class SoporteGPT(APIView):
    def post(self, request):
        chat_id = request.data.get("chat_id")
        bot = Bot(token=settings.BOT_TOKEN)

        # Ofrecer ambas opciones de chat
        async_to_sync(bot.send_message)(
            chat_id=chat_id,
            text="Selecciona cómo quieres acceder al asistente de IA:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 Chat Directo", callback_data="chat_directo")],
                [InlineKeyboardButton("🌐 Chat Web", url=settings.CHAT_WEBHOOK)],
                [InlineKeyboardButton("🔙 Volver al menú", callback_data="menu")]
            ])
        )

        return Response({"status": "opciones_chat_enviadas"})

class SeleccionarTituloGuion(APIView):
    def post(self, request):
        chat_id = request.data.get("chat_id")
        bot = Bot(token=settings.BOT_TOKEN)
        async_to_sync(bot.send_message)(
            chat_id=chat_id,
            text="¿Desea el guión con un nombre personalizado o uno designado por el sistema?",
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
                            text="Designado por el bot", 
                            callback_data="nombre_default"
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

                return Response({
                    "status": "Documento creado",
                    "fileName": f"{titulo}",
                    "dateCreated": currDate,
                    "folderId": settings.GOOGLE_FOLDER_ID
                })

            fecha = datetime.now().strftime("%Y-%m-%d")
            titulo_auto = f"Guión FMSPORTS {fecha}"
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

            return Response({
                "status": "Documento creado",
                "fileName": f"{titulo_auto}",
                "dateCreated": currDate,
                "folderId": settings.GOOGLE_FOLDER_ID
            })

        except HttpError as error:
            logger.error(f"Error al crear documento: {str(error)}")
            async_to_sync(bot.send_message)(
                chat_id=chat_id, 
                text="❌ Ocurrió un error al crear el documento."
            )
            return Response(
                {"error": str(error)}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

# Funciones auxiliares
def es_mensaje_chat(chat_id, message):
    """
    Determina si un mensaje debe ser procesado por el chat embedded
    """
    if chat_id in modo_soporte_usuarios:
        return True
    
    # Palabras clave que activan el chat directo
    keywords_chat = ['pregunta', 'ayuda', 'consulta', 'chat', 'ia', 'gpt']
    message_lower = message.lower()
    
    return any(keyword in message_lower for keyword in keywords_chat)