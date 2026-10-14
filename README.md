# OpenCode Chat

Una interfaz web tipo chat para [OpenCode](https://opencode.ai), pensada para usarla desde el celular o desde otra compu. Es **un solo archivo** (`opencode_chat.py`) y no necesita instalar nada más que Python: usa solo la librería estándar.

Habla con `opencode serve` (si no está corriendo, lo levanta solo) y te deja:

- Conversar con OpenCode en la carpeta del proyecto que elijas, con el historial de charlas a un costado.
- Ver los pasos que hace (herramientas, razonamiento) y **aprobar o rechazar permisos** y responder sus preguntas con botones.
- Detener una tarea en cualquier momento.
- Ver cuánto **contexto** ocupa la charla y cuánto queda, con un aviso para pasar a una charla nueva antes de que se llene. Si el proveedor corta por **cupo o límite de uso**, lo muestra claro y te ofrece cambiar de modelo.
- Elegir el modelo (con filtro de gratuitos) y medir cuál responde más rápido.
- Subir y descargar archivos del proyecto.
- Recibir avisos en el celular por [ntfy](https://ntfy.sh) cuando OpenCode termina o pide permiso.
- Volver a la última charla de cada proyecto al recargar la página.

---

## Índice

1. [Requisitos](#1-requisitos)
2. [Instalación paso a paso](#2-instalación-paso-a-paso)
3. [Usarlo desde el celular (Tailscale)](#3-usarlo-desde-el-celular-tailscale)
4. [Cómo se usa](#4-cómo-se-usa)
5. [Avisos en el celular (ntfy)](#5-avisos-en-el-celular-ntfy)
6. [Configuración](#6-configuración)
7. [Dejarlo corriendo siempre](#7-dejarlo-corriendo-siempre)
8. [Seguridad: leelo antes de usarlo](#8-seguridad-leelo-antes-de-usarlo)
9. [Actualizar y desinstalar](#9-actualizar-y-desinstalar)
10. [Problemas comunes](#10-problemas-comunes)

---

## 1. Requisitos

- **Linux, macOS o Windows con WSL.** En Windows se corre dentro de WSL (Ubuntu, por ejemplo).
- **Python 3.9 o más nuevo.** Para ver tu versión: `python3 --version`.
- **OpenCode** instalado y con al menos un proveedor de modelos configurado.
- **git**, para bajarlo (o descargá el ZIP desde GitHub).

## 2. Instalación paso a paso

### 2.1. Instalar OpenCode (si no lo tenés)

Seguí las instrucciones de [opencode.ai](https://opencode.ai). La forma más corta:

```bash
curl -fsSL https://opencode.ai/install | bash
```

Después configurá un proveedor de modelos (OpenCode trae modelos gratuitos para empezar):

```bash
opencode auth login
```

Comprobá que anda: `opencode --version`.

### 2.2. Bajar OpenCode Chat

```bash
cd ~
git clone https://github.com/xuxinio/openchat-for-opencode-.git opencode-chat
cd opencode-chat
```

### 2.3. Configurarlo

```bash
python3 opencode_chat.py --setup
```

El asistente pregunta cinco cosas (Enter deja el valor entre corchetes):

| Pregunta | Qué poner |
|---|---|
| **Dirección** | `127.0.0.1` si lo vas a usar solo en esta compu. Si querés entrar desde el celular, la IP de Tailscale (el asistente la detecta y la propone). |
| **Puerto** | `8080` está bien, salvo que ya lo use otra cosa. |
| **Carpeta raíz** | La carpeta donde tenés tus proyectos, por ejemplo `/home/tu-usuario/proyectos`. **No pongas todo tu usuario**: ver [Seguridad](#8-seguridad-leelo-antes-de-usarlo). |
| **Token** | Se genera solo. **Anotalo**: es la clave que te pide la web la primera vez. |
| **ntfy** | Opcional. Dejalo vacío por ahora; se puede configurar después (ver [sección 5](#5-avisos-en-el-celular-ntfy)). |

Todo queda guardado en `~/.config/opencode-chat/config.json`.

### 2.4. Arrancarlo

```bash
python3 opencode_chat.py
```

Vas a ver algo así:

```
✅ opencode serve iniciado en 127.0.0.1:4096
🚀 OpenCode Chat 6.0 en http://127.0.0.1:8080
📂 Carpeta raíz: /home/tu-usuario/proyectos
🔑 Token: el de /home/tu-usuario/.config/opencode-chat/config.json
```

Abrí la dirección en el navegador, pegá el token cuando lo pida y listo. Para pararlo: `Ctrl+C`.

## 3. Usarlo desde el celular (Tailscale)

La forma segura de entrar desde el celular o desde otra compu es [Tailscale](https://tailscale.com): arma una red privada entre tus dispositivos, sin abrir puertos en tu router. Es gratis para uso personal.

1. Instalá Tailscale en la compu donde corre OpenCode Chat y en el celular, y entrá con la misma cuenta en los dos.
2. En la compu, mirá su IP de Tailscale: `tailscale ip -4` (empieza con `100.`).
3. Volvé a correr `python3 opencode_chat.py --setup` y poné esa IP como **Dirección** (o cambiala desde ⚙ y reiniciá).
4. En el celular, con Tailscale prendido, abrí `http://100.x.y.z:8080/`.
5. Opcional: en el menú del navegador, **"Agregar a la pantalla de inicio"**, así queda como una app.

## 4. Cómo se usa

### Elegir el proyecto

Arriba, el botón **📁** abre el selector de carpetas. Podés navegar o buscar por nombre. Las carpetas con 🌿 son repositorios git. Tocá **Elegir** en la que quieras: OpenCode va a trabajar ahí adentro. Las últimas que usaste aparecen primero.

### Conversar

- Escribí abajo y mandá con **Enter** (en el celular, con el botón ↑; Enter hace salto de línea). **Shift+Enter** = salto de línea en la compu.
- Las charlas del proyecto quedan en el panel de la izquierda (en el celular, en el menú ☰). **＋ Nueva conversación** arranca una limpia; la ✕ borra una.
- Al recargar la página, o si la abrís otro día, vuelve a la última charla de ese proyecto.

### Lo que hace OpenCode

- Debajo de cada respuesta aparecen desplegables: **💭 Razonamiento** y cada **herramienta** que usó (✓ terminó, ✗ falló, ⋯ en curso), con lo que pidió y lo que obtuvo.
- Mientras trabaja se ven los tres puntitos, y el botón de enviar se convierte en **■ Detener**: corta la tarea y descarta los permisos o preguntas pendientes.

### Permisos

Cuando OpenCode quiere hacer algo que requiere permiso (editar un archivo, correr un comando…), aparece una tarjeta 🔐 con lo que pide:

- **Permitir una vez**: solo esta vez.
- **Rechazar**: no lo hace.
- **Siempre**: queda como regla permanente para ese tipo de pedido (pide confirmación).

### Preguntas

Si OpenCode necesita que elijas algo, aparece una tarjeta con las opciones como botones (y un campo para escribir otra respuesta). **Enviar respuesta** sigue la tarea; **Descartar** puede detenerla.

### Modelos

El botón **⚡** abre la lista de modelos de tus proveedores. **Solo gratuitos** filtra los que no cobran. **⏱ Probar** les manda a todos los gratuitos un mensaje mínimo y los ordena del más rápido al más lento (los de pago no se prueban, para no gastar).

### Contexto y cupo

Arriba de la caja de texto:

- **Contexto**: cuánto ocupa la charla en la memoria del modelo, de cuánto es el límite y cuánto queda. La barra se pone amarilla y roja a medida que se llena. Desde el 75% aparece un aviso con el botón **Nueva charla**: conviene seguir en una nueva antes de que el modelo empiece a olvidar.
- **Procesado en la charla**: la suma de todo lo que se mandó y recibió en cada pedido (cada pedido reenvía el historial, por eso crece rápido). Si el modelo cobra, también muestra el costo.
- **⛽ Cupo del proveedor**: los proveedores no informan cuánto cupo te queda, pero cuando cortan por límite de uso el chat lo muestra claro y te ofrece cambiar de modelo.

### Archivos

El botón **📎** abre los archivos del proyecto:

- Tocá un archivo para **descargarlo**.
- **⬆ Subir aquí** sube archivos a la carpeta en la que estás. Nunca pisa uno que ya existe (le agrega un número). Al terminar, la ruta queda escrita en tu mensaje para que se lo pases a OpenCode.

### Copiar

Cada bloque de código y cada respuesta tienen su botón **Copiar**. El **📋** de arriba copia toda la conversación.

## 5. Avisos en el celular (ntfy)

[ntfy](https://ntfy.sh) manda notificaciones al celular. OpenCode Chat avisa cuando una tarea larga **termina**, cuando OpenCode **pide permiso** y cuando **te hace una pregunta**.

1. Instalá la app **ntfy** en el celular (Android o iPhone).
2. Inventá un tema largo y difícil de adivinar, por ejemplo `opencode-ana-7f3k9q2m`: cualquiera que lo sepa puede leer los avisos.
3. En la app, **Suscribirse** a ese tema.
4. En OpenCode Chat, **⚙ → Avisos por ntfy**: `https://ntfy.sh/opencode-ana-7f3k9q2m` → **Guardar**. Se activa en el momento.

Opciones:

- **Avisar solo si la tarea duró al menos…** (15 segundos por defecto): para que no avise de cada respuesta corta.
- **Incluir en el aviso el comando o la ruta del permiso**: más cómodo, pero el texto pasa por el servidor de ntfy.
- **Token de ntfy**: solo si usás un servidor ntfy propio que lo pida.

## 6. Configuración

Desde la web, con el botón **⚙**:

| Opción | Qué hace | Cuándo se aplica |
|---|---|---|
| Carpeta raíz | Hasta dónde ven OpenCode y la web | En el momento |
| Dirección y puerto | Dónde escucha | Al reiniciar |
| Avisos por ntfy | URL del tema, token, segundos mínimos, detalle | En el momento |
| Máximos de subida y descarga | En MB | En el momento |
| Generar un token nuevo | Cambia la clave de acceso; los otros dispositivos la van a pedir de nuevo | En el momento |

También se puede volver a correr `python3 opencode_chat.py --setup` o editar a mano `~/.config/opencode-chat/config.json` y reiniciar.

### Variables de entorno

Si están, mandan sobre el archivo (y en ⚙ aparecen bloqueadas):

| Variable | Equivale a |
|---|---|
| `HOST`, `PORT` | Dirección y puerto |
| `OC_ROOT` | Carpeta raíz |
| `OPENCODE_CHAT_TOKEN` | Token |
| `NTFY_URL`, `NTFY_TOKEN`, `NTFY_MIN_SECS`, `NTFY_DETAIL=1`, `NTFY_CLICK` | Avisos por ntfy (`NTFY_CLICK` = qué abre el aviso al tocarlo) |
| `UPLOAD_MAX_MB`, `DOWNLOAD_MAX_MB` | Máximos |
| `OPENCODE_SERVER_PASSWORD`, `OC_PORT` | Si ya tenés un `opencode serve` corriendo con contraseña, o en otro puerto que no sea el 4096 |

## 7. Dejarlo corriendo siempre

### Linux / WSL con systemd

Creá `~/.config/systemd/user/opencode-chat.service` (cambiá la ruta si lo clonaste en otro lado):

```ini
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
loginctl enable-linger "$USER"     # que siga corriendo aunque cierres la sesión
journalctl --user -u opencode-chat -f    # ver qué está pasando
```

En WSL, systemd tiene que estar activado (`/etc/wsl.conf` con `[boot]` y `systemd=true`).

### Sin systemd

Con `tmux`:

```bash
tmux new -d -s opencode-chat 'python3 ~/opencode-chat/opencode_chat.py'
```

## 8. Seguridad: leelo antes de usarlo

- **El token es una llave a tu máquina.** OpenCode edita archivos y ejecuta comandos: quien tenga el token puede pedirle cualquier cosa dentro de la carpeta raíz. Tratalo como una contraseña y cambialo (⚙) si lo compartiste por error.
- **No lo expongas a internet.** Usalo en `127.0.0.1` o dentro de una red privada como Tailscale. No abras el puerto en el router. Va por `http`; dentro de Tailscale el tráfico ya viaja cifrado.
- **Elegí bien la carpeta raíz.** La web no muestra ni deja bajar archivos ni carpetas ocultos (`.env`, `.git`, `.ssh`, `.config`…) ni claves `.pem`/`.key`, pero **sí todo lo demás que esté adentro**: respaldos de bases de datos, planillas, documentos. Si tenés algo así en tus proyectos, guardalo en otro lado.
- La web solo reenvía a OpenCode una lista cerrada de pedidos; no es un acceso libre a su API.
- "Siempre" en un permiso queda como regla permanente de OpenCode: usalo solo para cosas que no te preocupan.

## 9. Actualizar y desinstalar

**Actualizar:**

```bash
cd ~/opencode-chat
git pull
```

Y reiniciá (`systemctl --user restart opencode-chat`, o `Ctrl+C` y arrancarlo de nuevo). La configuración no se toca.

**Desinstalar:**

```bash
systemctl --user disable --now opencode-chat 2>/dev/null
rm -f ~/.config/systemd/user/opencode-chat.service
rm -rf ~/opencode-chat ~/.config/opencode-chat
```

OpenCode y tus charlas no se borran: son de OpenCode.

## 10. Problemas comunes

| Qué pasa | Qué hacer |
|---|---|
| La web pide el token todo el tiempo | Pegaste un token viejo. Mirá el actual en `~/.config/opencode-chat/config.json` (campo `token`). |
| `❌ opencode serve pide contraseña` | Ya tenés un `opencode serve` corriendo con contraseña. Paralo, o exportá `OPENCODE_SERVER_PASSWORD` con esa contraseña. |
| `❌ No encuentro el binario opencode` | OpenCode no está instalado o no está en el PATH. Probá `opencode --version`. |
| No aparecen modelos | No hay proveedores configurados en OpenCode: `opencode auth login`. |
| `Address already in use` | El puerto lo usa otra cosa: elegí otro con `--setup`. |
| Desde el celular no abre | Tailscale prendido en los dos, y la **Dirección** tiene que ser la IP de Tailscale (no `127.0.0.1`). |
| "Fuera de la carpeta raíz permitida" | El proyecto está fuera de la carpeta raíz: ampliala en ⚙. |
| El contexto dice "límite desconocido" | El modelo de esa charla ya no está en la lista (los gratuitos rotan) y nunca se vio su límite. El resto funciona igual. |
| "Copiar" no anda | Algunos navegadores lo bloquean fuera de `https`; el chat usa un plan B, pero si falla, seleccioná y copiá a mano. |

## Licencia

MIT. Ver [LICENSE](LICENSE).
