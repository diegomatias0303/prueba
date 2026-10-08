import asyncio
import json
import socket
import sqlite3
import os
import os
import traceback
from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import uvicorn

app = FastAPI()

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return PlainTextResponse(str(exc) + "\n\n" + traceback.format_exc(), status_code=500)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

DATABASE_URL = os.environ.get("DATABASE_URL")
IS_POSTGRES = DATABASE_URL and DATABASE_URL.startswith("postgres")

if IS_POSTGRES:
    import psycopg2
    import psycopg2.extras
    IntegrityError = psycopg2.IntegrityError
else:
    IntegrityError = sqlite3.IntegrityError

def execute_query(conn, query, params=()):
    if IS_POSTGRES:
        query = query.replace("?", "%s")
        c = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    else:
        c = conn.cursor()
    c.execute(query, params)
    return c

# ==========================================
# DATABASE SETUP
# ==========================================
def init_db():
    if IS_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        auto_inc = "SERIAL PRIMARY KEY"
    else:
        conn = sqlite3.connect("database.db")
        auto_inc = "INTEGER PRIMARY KEY AUTOINCREMENT"
        
    execute_query(conn, f"""
        CREATE TABLE IF NOT EXISTS usuarios (
            id {auto_inc},
            nombre VARCHAR(100),
            usuario VARCHAR(50) UNIQUE,
            email VARCHAR(100) UNIQUE,
            password VARCHAR(50),
            victorias INTEGER DEFAULT 0,
            puntaje INTEGER DEFAULT 0
        )
    """)
    conn.commit()

    if IS_POSTGRES:
        try: 
            execute_query(conn, "ALTER TABLE usuarios ADD COLUMN victorias INTEGER DEFAULT 0")
            conn.commit()
        except: 
            conn.rollback()
        try: 
            execute_query(conn, "ALTER TABLE usuarios ADD COLUMN puntaje INTEGER DEFAULT 0")
            conn.commit()
        except: 
            conn.rollback()
    else:
        try: execute_query(conn, "ALTER TABLE usuarios ADD COLUMN victorias INTEGER DEFAULT 0")
        except: pass
        try: execute_query(conn, "ALTER TABLE usuarios ADD COLUMN puntaje INTEGER DEFAULT 0")
        except: pass

    execute_query(conn, f"""
        CREATE TABLE IF NOT EXISTS amigos (
            id {auto_inc},
            usuario_id INTEGER,
            amigo_id INTEGER,
            UNIQUE(usuario_id, amigo_id),
            FOREIGN KEY(usuario_id) REFERENCES usuarios(id),
            FOREIGN KEY(amigo_id) REFERENCES usuarios(id)
        )
    """)
    conn.commit()

    # Insert defaults if empty
    c = execute_query(conn, "SELECT COUNT(*) FROM usuarios")
    count = c.fetchone()[0] if not IS_POSTGRES else c.fetchone()[0]
    if count == 0:
        execute_query(conn, "INSERT INTO usuarios (nombre, usuario, email, password) VALUES ('Administrador', 'admin', 'admin@loteria.local', 'admin123')")
        execute_query(conn, "INSERT INTO usuarios (nombre, usuario, email, password) VALUES ('Diego', 'diego', 'diego@loteria.local', '123456')")
    conn.commit()
    conn.close()

init_db()

def get_db():
    if IS_POSTGRES:
        return psycopg2.connect(DATABASE_URL)
    else:
        conn = sqlite3.connect("database.db")
        conn.row_factory = sqlite3.Row
        return conn

# ==========================================
# ROUTES
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    # Simplest session: just rely on JS or redirect directly to login
    return RedirectResponse(url="/login")

@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={"request": request})

@app.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, usuario_o_email: str = Form(...), password: str = Form(...)):
    conn = get_db()
    c = execute_query(conn, "SELECT * FROM usuarios WHERE (usuario=? OR email=?) AND password=?", (usuario_o_email, usuario_o_email, password))
    user = c.fetchone()
    conn.close()
    
    if user:
        # We will use a simple query parameter for session for this prototype, or cookies
        response = RedirectResponse(url="/game", status_code=302)
        response.set_cookie(key="nombreJugador", value=user["nombre"])
        return response
    else:
        return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": "Credenciales incorrectas.", "input": usuario_o_email})

@app.get("/register", response_class=HTMLResponse)
async def register_get(request: Request):
    return templates.TemplateResponse(request=request, name="register.html", context={"request": request})

@app.post("/register", response_class=HTMLResponse)
async def register_post(request: Request, nombre: str = Form(...), usuario: str = Form(...), email: str = Form(...), password: str = Form(...), confirm_password: str = Form(...)):
    if password != confirm_password:
        return templates.TemplateResponse(request=request, name="register.html", context={"request": request, "error": "Las contraseñas no coinciden."})
    
    conn = get_db()
    try:
        execute_query(conn, "INSERT INTO usuarios (nombre, usuario, email, password) VALUES (?, ?, ?, ?)", (nombre, usuario, email, password))
        conn.commit()
        
        response = RedirectResponse(url="/game", status_code=302)
        response.set_cookie(key="nombreJugador", value=nombre)
        return response
    except IntegrityError:
        return templates.TemplateResponse(request=request, name="register.html", context={"request": request, "error": "El usuario o email ya existe."})
    finally:
        conn.close()

@app.get("/game", response_class=HTMLResponse)
async def game(request: Request):
    nombreJugador = request.cookies.get("nombreJugador", "Jugador1")
    return templates.TemplateResponse(request=request, name="board.html", context={"request": request, "nombreJugador": nombreJugador})

@app.get("/api/perfil/{nombre_jugador}")
async def get_perfil(nombre_jugador: str):
    conn = get_db()
    c = execute_query(conn, "SELECT id, nombre, usuario, victorias, puntaje FROM usuarios WHERE nombre = ?", (nombre_jugador,))
    user = c.fetchone()
    if not user:
        conn.close()
        return JSONResponse({"error": "Usuario no encontrado"})
    
    c = execute_query(conn, """
        SELECT u.nombre, u.usuario FROM amigos a
        JOIN usuarios u ON a.amigo_id = u.id
        WHERE a.usuario_id = ?
    """, (user["id"],))
    amigos = [{"nombre": row["nombre"], "usuario": row["usuario"]} for row in c.fetchall()]
    conn.close()
    
    return JSONResponse({
        "id": user["id"],
        "nombre": user["nombre"],
        "usuario": user["usuario"],
        "victorias": user["victorias"],
        "puntaje": user["puntaje"],
        "amigos": amigos
    })

@app.post("/api/add_friend")
async def add_friend(request: Request):
    data = await request.json()
    my_name = data.get("my_name")
    friend_name = data.get("friend_name")
    
    conn = get_db()
    c = execute_query(conn, "SELECT id FROM usuarios WHERE nombre = ?", (my_name,))
    me = c.fetchone()
    c = execute_query(conn, "SELECT id FROM usuarios WHERE nombre = ?", (friend_name,))
    friend = c.fetchone()
    
    if me and friend and me["id"] != friend["id"]:
        try:
            execute_query(conn, "INSERT INTO amigos (usuario_id, amigo_id) VALUES (?, ?)", (me["id"], friend["id"]))
            conn.commit()
            success = True
            msg = "Amigo agregado"
        except IntegrityError:
            conn.rollback()
            success = False
            msg = "Ya son amigos"
    else:
        success = False
        msg = "Usuario inválido"
    conn.close()
    return JSONResponse({"success": success, "msg": msg})

@app.post("/api/add_win")
async def add_win(request: Request):
    data = await request.json()
    nombre = data.get("nombre")
    puntos = data.get("puntos", 10)
    
    conn = get_db()
    execute_query(conn, "UPDATE usuarios SET victorias = victorias + 1, puntaje = puntaje + ? WHERE nombre = ?", (puntos, nombre))
    conn.commit()
    conn.close()
    return JSONResponse({"success": True})


# ==========================================
# WEBSOCKET MULTIPLAYER LOGIC
# ==========================================
rooms = {}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    current_room = None
    is_host = False

    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            room_code = data.get("roomCode")
            
            if action == "CREATE_ROOM":
                current_room = room_code
                is_host = True
                if current_room not in rooms:
                    rooms[current_room] = {"host": websocket, "clients": set()}
                else:
                    rooms[current_room]["host"] = websocket
                
            elif action == "JOIN_ROOM":
                current_room = room_code
                is_host = False
                if current_room in rooms:
                    rooms[current_room]["clients"].add(websocket)
                    host_ws = rooms[current_room]["host"]
                    if host_ws:
                        await host_ws.send_json(data.get("data", {}))
                else:
                    await websocket.send_json({"type": "ERROR", "message": "Sala no encontrada"})
                    
            elif action == "BROADCAST":
                if current_room in rooms:
                    payload = data.get("data", {})
                    for client in list(rooms[current_room]["clients"]):
                        try:
                            await client.send_json(payload)
                        except:
                            rooms[current_room]["clients"].remove(client)
                            
            elif action == "SEND_TO_HOST":
                if current_room in rooms:
                    host_ws = rooms[current_room]["host"]
                    if host_ws:
                        payload = data.get("data", {})
                        try:
                            await host_ws.send_json(payload)
                        except:
                            pass

    except WebSocketDisconnect:
        if current_room in rooms:
            if is_host:
                for client in list(rooms[current_room]["clients"]):
                    try:
                        await client.send_json({"type": "ERROR", "message": "El host se ha desconectado"})
                    except:
                        pass
                del rooms[current_room]
            else:
                rooms[current_room]["clients"].discard(websocket)

# ==========================================
# RUN SERVER
# ==========================================
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

if __name__ == "__main__":
    local_ip = get_local_ip()
    print("="*60)
    print("🚀 SERVIDOR DE LOTERÍA Y ATRAPADAS (FULL PYTHON) 🚀")
    print("="*60)
    print(f"Para jugar, abre en tu navegador:")
    print(f"  En esta PC: http://localhost:8000")
    print(f"  En la red LAN: http://{local_ip}:8000")
    print("="*60)
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
