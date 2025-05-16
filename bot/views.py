# views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from asgiref.sync import async_to_sync
from rest_framework import status
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from openai import OpenAI
from django.conf import settings
import re
import markdown
from googleapiclient.errors import HttpError
from datetime import datetime
import time
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive.metadata.readonly"]

client = OpenAI(api_key=settings.OPENAI_KEY)
threads_usuarios = set()
modo_soporte_usuarios = set()
clave_acceso_bot = "FMSPORTS2025"


def obtener_menu_inline():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎬 Generar guion", callback_data="guion")],
            [InlineKeyboardButton("✏️ Modificar guion", callback_data="modificar")],
            [InlineKeyboardButton("🤖 Modo soporte GPT", callback_data="soporte")],
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
        while clave_secreta != clave_acceso_bot:
            async_to_sync(bot.send_message)(
                chat_id = chat_id,
                text=f"Verificación de clave incorrecta, intente de nuevo"
            )
        async_to_sync(bot.send_message)(
            chat_id=chat_id,
            text=f"Atención a la musica! Bienvenido {username} dime que necesitas de mi?",
            reply_markup=obtener_menu_inline(),
        )

        return Response(
            {
                "respuesta": f"Verificación de clave correcta, {status.HTTP_202_ACCEPTED}"
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

class SoporteGPT(APIView):
    def post(self, request):
        chat_id = request.data.get("chat_id")
        bot = Bot(token=settings.BOT_TOKEN)

        async_to_sync(bot.send_message)(
            chat_id=chat_id,
            text="Abrir chat en Linea",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("CHAT IA", url=settings.CHAT_WEBHOOK)]]
            ),
        )

        return Response({"chat Abierto"})


class SeleccionarTituloGuion(APIView):
    def post(self, request):
        chat_id = request.data.get("chat_id")
        bot = Bot(token=settings.BOT_TOKEN)
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


def crearDocumento(drive_service, docs_service, titulo, contenido):
    archivo_metadata = {
        "name": f"{titulo}",
        "mimeType": "application/vnd.google-apps.document",
        "parents": [settings.GOOGLE_FOLDER_ID],
    }

    archivo = drive_service.files().create(body=archivo_metadata, fields="id").execute()
    documento_id = archivo.get("id")

    # Insertar el contenido
    docs_service.documents().batchUpdate(
        documentId=documento_id,
        body={
            "requests": [{"insertText": {"location": {"index": 1}, "text": contenido}}]
        },
    ).execute()
    return documento_id


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
            # Crear el título
            if titulo:
                crearDocumento(drive_service, docs_service, titulo, prompt_content)
                # Enviar mensaje informativo al usuario
                async_to_sync(bot.send_message)(
                    chat_id=chat_id,
                    text=f"✅ Tu guión *{titulo}* fue creado exitosamente en la carpeta *{nombre_carpeta}* de Google Drive.",
                    parse_mode="Markdown",
                )

                return Response({"status": "Documento creado"})

            fecha = datetime.now().strftime("%Y-%m-%d")
            titulo_auto = f"Guión FMSPORTS *{fecha}*"
            crearDocumento(
                drive_service=drive_service,
                docs_service=docs_service,
                titulo=titulo_auto,
                contenido=prompt_content,
            )

            # Enviar mensaje informativo al usuario
            async_to_sync(bot.send_message)(
                chat_id=chat_id,
                text=f"✅ Tu guión *{titulo_auto}* fue creado exitosamente en la carpeta *{nombre_carpeta}* de Google Drive.",
                parse_mode="Markdown",
            )

            return Response({"status": "Documento creado"})

        except HttpError as error:
            async_to_sync(bot.send_message)(
                chat_id=chat_id, text="❌ Ocurrió un error al crear el documento."
            )
            return Response(
                {"error": str(error)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
