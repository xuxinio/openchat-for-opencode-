# OpenCode Chat

Una interfaz web tipo chat para [OpenCode](https://opencode.ai), pensada para usarla desde el celular o desde otra compu, sin instalar nada más que Python.

Es un solo archivo (`opencode_chat.py`) y usa solo la librería estándar de Python. Habla con `opencode serve` (si no está corriendo, lo levanta solo) y te deja:

- Conversar con OpenCode en la carpeta del proyecto que elijas, con el historial de charlas a un costado.
- Ver los pasos que hace (herramientas, razonamiento) y **aprobar o rechazar permisos** y responder sus preguntas con botones.
- Detener una tarea en cualquier momento.
- Ver cuánto **contexto** ocupa la charla y cuánto queda, con un aviso para pasar a una charla nueva antes de que se llene. Si el proveedor corta por **cupo o límite de uso**, lo muestra claro y te ofrece cambiar de modelo.
- Elegir el modelo (con filtro de gratuitos) y medir cuál responde más rápido.
- Subir y descargar archivos del proyecto.
- Recibir avisos en el celular por [ntfy](https://ntfy.sh) cuando OpenCode termina o pide permiso.
- Volver a la última charla de cada proyecto al recargar la página.

## Requisitos

- Python 3.9 o más nuevo.
- OpenCode instalado (el comando `opencode` en el PATH) y con al menos un proveedor configurado.

## Instalación

```bash
git clone https://github.com/<usuario>/opencode-chat.git
cd opencode-chat
python3 opencode_chat.py --setup   # pregunta dirección, puerto, carpeta raíz, token y ntfy
python3 opencode_chat.py           # arranca
```

Abrí `http://<dirección>:<puerto>/` en el navegador. La primera vez te pide el **token** que mostró el asistente de configuración.

## Configuración

La configuración se guarda en `~/.config/opencode-chat/config.json` (con permisos 600) y se puede cambiar desde la web, con el botón **⚙**. La dirección y el puerto se aplican al reiniciar; lo demás, en el momento.

| Opción | Qué hace |
|---|---|
| Dirección y puerto | Dónde escucha. `127.0.0.1` = solo esta máquina. |
| Carpeta raíz | OpenCode y la web solo ven lo que está adentro. |
| Token | La clave de acceso a la web. |
| ntfy | URL del tema (`https://ntfy.sh/un-tema-largo-y-secreto`), token si tu servidor lo pide, cuántos segundos tiene que durar una tarea para avisar y si el aviso incluye el comando pedido. |
| Máximos de subida y descarga | En MB. |

Si preferís variables de entorno, mandan sobre el archivo: `HOST`, `PORT`, `OC_ROOT`, `OPENCODE_CHAT_TOKEN`, `NTFY_URL`, `NTFY_TOKEN`, `NTFY_MIN_SECS`, `NTFY_DETAIL=1`, `NTFY_CLICK`, `UPLOAD_MAX_MB`, `DOWNLOAD_MAX_MB`. Para un `opencode serve` que ya corre con contraseña: `OPENCODE_SERVER_PASSWORD` (y `OC_PORT` si no está en el 4096).

## Seguridad: leelo antes de usarlo

- **El token es una llave a tu máquina.** OpenCode edita archivos y ejecuta comandos: quien tenga el token puede pedirle cualquier cosa dentro de la carpeta raíz. Tratalo como una contraseña.
- **No lo expongas a internet.** Usalo en `127.0.0.1` o dentro de una red privada como [Tailscale](https://tailscale.com) (el asistente detecta tu IP de Tailscale). Va por `http`; dentro de Tailscale el tráfico ya viaja cifrado.
- **Elegí bien la carpeta raíz.** Conviene la carpeta de tus proyectos, no todo tu usuario. La web no muestra ni deja bajar archivos ni carpetas ocultos (`.env`, `.git`, `.ssh`…) ni `.pem`/`.key`, pero sí todo lo demás que esté adentro: respaldos, bases de datos, planillas.
- La web solo reenvía a OpenCode una lista cerrada de pedidos; no es un acceso libre a su API.

## Dejarlo corriendo (Linux, systemd de usuario)

```ini
# ~/.config/systemd/user/opencode-chat.service
[Unit]
Description=OpenCode Chat

[Service]
ExecStart=/usr/bin/python3 %h/opencode-chat/opencode_chat.py
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now opencode-chat
loginctl enable-linger "$USER"   # para que siga corriendo sin sesión abierta
```

## Licencia

MIT. Ver [LICENSE](LICENSE).
