#!/usr/bin/env python3
"""
SERVIDOR DE SEÑALIZACIÓN WEBRTC CON BASE DE DATOS
"""

import asyncio
import websockets
import json
from aiohttp import web
import logging
from datetime import datetime
import os
from uuid import uuid4
import time
import hashlib
import base64
import sqlite3
import random
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========== DATABASE MANAGER ==========
class DatabaseManager:
    def __init__(self, db_path='webrtc.db'):
        self.db_path = db_path
        self.init_database()

    def init_database(self):
        """Inicializa la base de datos con las tablas necesarias"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Tabla de usuarios
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    avatar_url TEXT,
                    is_online BOOLEAN DEFAULT 0,
                    status TEXT DEFAULT 'disponible',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Tabla de sesiones
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            ''')
            
            # Tabla de llamadas
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    caller_id TEXT NOT NULL,
                    callee_id TEXT NOT NULL,
                    call_type TEXT DEFAULT 'audio',
                    start_time TIMESTAMP,
                    end_time TIMESTAMP,
                    duration INTEGER,
                    FOREIGN KEY (caller_id) REFERENCES users (id),
                    FOREIGN KEY (callee_id) REFERENCES users (id)
                )
            ''')
            
            conn.commit()
            logger.info("✅ Base de datos inicializada")

    def hash_password(self, password):
        """Genera hash seguro de la contraseña"""
        salt = os.urandom(32)
        key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
        return base64.b64encode(salt + key).decode('utf-8')

    def verify_password(self, stored_hash, password):
        """Verifica una contraseña contra su hash"""
        try:
            decoded = base64.b64decode(stored_hash)
            salt = decoded[:32]
            stored_key = decoded[32:]
            key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
            return stored_key == key
        except:
            return False

    def create_user(self, username, password, avatar_data=None):
        """Crea un nuevo usuario en la base de datos"""
        user_id = str(uuid4())
        password_hash = self.hash_password(password)
        avatar_url = None
        
        if avatar_data:
            # Guardar avatar como archivo
            avatar_dir = Path('static/avatars')
            avatar_dir.mkdir(parents=True, exist_ok=True)
            
            if avatar_data.startswith('data:image'):
                # Extraer base64 del data URL
                header, data = avatar_data.split(',', 1)
                if ';base64' in header:
                    data = base64.b64decode(data)
                    
                    # Determinar extensión
                    if 'png' in header:
                        ext = 'png'
                    elif 'jpeg' in header or 'jpg' in header:
                        ext = 'jpg'
                    else:
                        ext = 'png'
                    
                    filename = f"{user_id}.{ext}"
                    filepath = avatar_dir / filename
                    
                    with open(filepath, 'wb') as f:
                        f.write(data)
                    
                    avatar_url = f"/avatars/{filename}"
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO users (id, username, password_hash, avatar_url, is_online)
                    VALUES (?, ?, ?, ?, 0)
                ''', (user_id, username, password_hash, avatar_url))
                
                conn.commit()
                
                return {
                    'id': user_id,
                    'username': username,
                    'avatar_url': avatar_url,
                    'is_online': False,
                    'status': 'disponible'
                }
                
        except sqlite3.IntegrityError:
            logger.warning(f"❌ Usuario ya existe: {username}")
            return None
        except Exception as e:
            logger.error(f"💥 Error creando usuario: {e}")
            return None

    def verify_user(self, username, password):
        """Verifica las credenciales del usuario"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
                user = cursor.fetchone()
                
                if user and self.verify_password(user['password_hash'], password):
                    return {
                        'id': user['id'],
                        'username': user['username'],
                        'avatar_url': user['avatar_url'],
                        'is_online': False,
                        'status': user['status']
                    }
                return None
                
        except Exception as e:
            logger.error(f"💥 Error verificando usuario: {e}")
            return None

    def get_user(self, user_id):
        """Obtiene un usuario por su ID"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
                user = cursor.fetchone()
                
                if user:
                    return {
                        'id': user['id'],
                        'username': user['username'],
                        'avatar_url': user['avatar_url'],
                        'is_online': bool(user['is_online']),
                        'status': user['status']
                    }
                return None
                
        except Exception as e:
            logger.error(f"💥 Error obteniendo usuario: {e}")
            return None

    def update_user_status(self, user_id, is_online, status='disponible'):
        """Actualiza el estado de conexión del usuario"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE users 
                    SET is_online = ?, status = ?, last_seen = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (1 if is_online else 0, status, user_id))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"💥 Error actualizando estado: {e}")
            return False

    def get_all_users(self, exclude_user_id=None):
        """Obtiene todos los usuarios"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                if exclude_user_id:
                    cursor.execute('SELECT * FROM users WHERE id != ? ORDER BY username', (exclude_user_id,))
                else:
                    cursor.execute('SELECT * FROM users ORDER BY username')
                
                users = cursor.fetchall()
                result = []
                
                for user in users:
                    result.append({
                        'id': user['id'],
                        'username': user['username'],
                        'avatar_url': user['avatar_url'],
                        'is_connected': bool(user['is_online']),
                        'status': user['status'],
                        'avatar_color': f"#{random.randint(0, 0xFFFFFF):06x}"
                    })
                
                return result
                
        except Exception as e:
            logger.error(f"💥 Error obteniendo usuarios: {e}")
            return []

    def search_users(self, query, exclude_user_id=None):
        """Busca usuarios por nombre"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                search_query = f"%{query}%"
                if exclude_user_id:
                    cursor.execute('''
                        SELECT * FROM users 
                        WHERE username LIKE ? AND id != ?
                        ORDER BY username
                    ''', (search_query, exclude_user_id))
                else:
                    cursor.execute('''
                        SELECT * FROM users 
                        WHERE username LIKE ?
                        ORDER BY username
                    ''', (search_query,))
                
                users = cursor.fetchall()
                result = []
                
                for user in users:
                    result.append({
                        'id': user['id'],
                        'username': user['username'],
                        'avatar_url': user['avatar_url'],
                        'is_connected': bool(user['is_online']),
                        'status': user['status']
                    })
                
                return result
                
        except Exception as e:
            logger.error(f"💥 Error buscando usuarios: {e}")
            return []

    def update_user_profile(self, user_id, username=None, password=None, avatar_data=None):
        """Actualiza el perfil del usuario"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # Obtener usuario actual
                cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
                user = cursor.fetchone()
                
                if not user:
                    return None
                
                # Actualizar avatar si se proporciona
                avatar_url = user['avatar_url']
                if avatar_data and avatar_data.startswith('data:image'):
                    # Eliminar avatar anterior si existe
                    if avatar_url and avatar_url.startswith('/avatars/'):
                        old_path = Path(f"static{avatar_url}")
                        if old_path.exists():
                            old_path.unlink()
                    
                    # Guardar nuevo avatar
                    avatar_dir = Path('static/avatars')
                    avatar_dir.mkdir(parents=True, exist_ok=True)
                    
                    # Extraer base64 del data URL
                    header, data = avatar_data.split(',', 1)
                    if ';base64' in header:
                        data = base64.b64decode(data)
                        
                        # Determinar extensión
                        if 'png' in header:
                            ext = 'png'
                        elif 'jpeg' in header or 'jpg' in header:
                            ext = 'jpg'
                        else:
                            ext = 'png'
                        
                        filename = f"{user_id}.{ext}"
                        filepath = avatar_dir / filename
                        
                        with open(filepath, 'wb') as f:
                            f.write(data)
                        
                        avatar_url = f"/avatars/{filename}"
                
                # Actualizar contraseña si se proporciona
                password_hash = user['password_hash']
                if password:
                    password_hash = self.hash_password(password)
                
                # Actualizar username si se proporciona
                new_username = username if username else user['username']
                
                cursor.execute('''
                    UPDATE users 
                    SET username = ?, password_hash = ?, avatar_url = ?
                    WHERE id = ?
                ''', (new_username, password_hash, avatar_url, user_id))
                
                conn.commit()
                
                return {
                    'id': user_id,
                    'username': new_username,
                    'avatar_url': avatar_url,
                    'is_online': bool(user['is_online']),
                    'status': user['status']
                }
                
        except sqlite3.IntegrityError:
            logger.warning(f"❌ Nombre de usuario ya existe: {username}")
            return None
        except Exception as e:
            logger.error(f"💥 Error actualizando perfil: {e}")
            return None

    def log_call(self, caller_id, callee_id, call_type='audio', duration=0):
        """Registra una llamada en la base de datos"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO calls (caller_id, callee_id, call_type, start_time, end_time, duration)
                    VALUES (?, ?, ?, datetime('now', '-? seconds'), datetime('now'), ?)
                ''', (caller_id, callee_id, call_type, duration, duration))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"💥 Error registrando llamada: {e}")
            return False

    def create_session(self, user_id):
        """Crea una nueva sesión para el usuario"""
        try:
            token = str(uuid4())
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO sessions (token, user_id, expires_at)
                    VALUES (?, ?, datetime('now', '+1 day'))
                ''', (token, user_id))
                conn.commit()
            return token
        except Exception as e:
            logger.error(f"💥 Error creando sesión: {e}")
            return None

    def validate_session(self, token):
        """Valida un token de sesión"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT user_id FROM sessions 
                    WHERE token = ? AND expires_at > datetime('now')
                ''', (token,))
                result = cursor.fetchone()
                return result['user_id'] if result else None
        except Exception as e:
            logger.error(f"💥 Error validando sesión: {e}")
            return None

# ========== USER MANAGER ==========
class UserManager:
    def __init__(self, db_manager):
        self.db = db_manager
        self.connected_users = {}  # user_id -> {ws, username, etc}
        self.heartbeats = {}
        self.pending_signals = {}
        self.active_calls = {}

    def generate_session_token(self, user_id):
        """Genera un token de sesión único"""
        return self.db.create_session(user_id)

    def validate_session(self, token):
        """Valida un token de sesión"""
        return self.db.validate_session(token)

    def add_connected_user(self, user_id, websocket, user_data):
        """Agrega un usuario conectado via WebSocket"""
        current_time = datetime.now().isoformat()

        self.connected_users[user_id] = {
            'ws': websocket,
            'username': user_data['username'],
            'avatar_url': user_data.get('avatar_url'),
            'status': 'disponible',
            'in_call_with': None,
            'connected_at': current_time,
            'last_seen': current_time,
            'heartbeat': time.time()
        }

        # Actualizar estado en base de datos
        self.db.update_user_status(user_id, True)

        self.heartbeats[user_id] = time.time()
        self.pending_signals[user_id] = []

        logger.info(f"✅ Usuario conectado: {user_data['username']} ({user_id})")
        return user_data.get('avatar_url')

    def remove_connected_user(self, user_id):
        """Remueve un usuario conectado"""
        if user_id in self.connected_users:
            # Finalizar llamada si está en una
            partner = self.connected_users[user_id]['in_call_with']
            if partner and partner in self.connected_users:
                self.connected_users[partner]['in_call_with'] = None
                self.connected_users[partner]['status'] = 'disponible'

            username = self.connected_users[user_id]['username']

            # Actualizar estado en base de datos
            self.db.update_user_status(user_id, False)

            # Limpiar datos
            if user_id in self.heartbeats: 
                del self.heartbeats[user_id]
            if user_id in self.pending_signals: 
                del self.pending_signals[user_id]
            del self.connected_users[user_id]

            logger.info(f"🗑️ Usuario desconectado: {username}")
            return True
        return False

    def update_user_status(self, user_id, status, in_call_with=None):
        """Actualiza el estado de un usuario"""
        if user_id not in self.connected_users:
            return False

        self.connected_users[user_id]['status'] = status
        self.connected_users[user_id]['in_call_with'] = in_call_with
        self.connected_users[user_id]['last_seen'] = datetime.now().isoformat()

        return True

    def update_heartbeat(self, user_id):
        """Actualiza el heartbeat de un usuario"""
        if user_id in self.connected_users:
            self.heartbeats[user_id] = time.time()
            return True
        return False

    def check_inactive_users(self):
        """Verifica usuarios inactivos"""
        current_time = time.time()
        inactive = [uid for uid, hb in self.heartbeats.items() if current_time - hb > 30]

        for uid in inactive:
            logger.warning(f"⏰ Usuario inactivo: {uid}")
            self.remove_connected_user(uid)

        return inactive

    def get_user_info(self, user_id):
        """Obtiene información de usuario conectado"""
        if user_id in self.connected_users:
            u = self.connected_users[user_id]
            return {
                'id': user_id,
                'username': u['username'],
                'status': u['status'],
                'avatar_url': u['avatar_url'],
                'in_call_with': u['in_call_with']
            }
        return None

    def get_connected_users(self, exclude_user_id=None):
        """Obtiene lista de usuarios conectados"""
        connected = []
        for uid, data in self.connected_users.items():
            if uid != exclude_user_id:
                info = self.get_user_info(uid)
                if info:
                    connected.append(info)

        connected.sort(key=lambda x: x['username'].lower())
        return connected

    def can_call_user(self, caller_id, target_id):
        """Verifica si se puede llamar a un usuario"""
        if caller_id not in self.connected_users or target_id not in self.connected_users:
            return False

        if target_id == caller_id:
            return False

        caller_status = self.connected_users[caller_id]['status']
        target_status = self.connected_users[target_id]['status']

        if target_status != 'disponible' or caller_status != 'disponible':
            return False

        return True

    def initiate_call(self, caller_id, target_id):
        """Inicia una llamada entre usuarios"""
        if not self.can_call_user(caller_id, target_id):
            return False

        self.update_user_status(caller_id, 'llamando', target_id)
        self.update_user_status(target_id, 'recibiendo_llamada', caller_id)

        logger.info(f"📞 Llamada iniciada: {self.connected_users[caller_id]['username']} -> {self.connected_users[target_id]['username']}")
        return True

    def accept_call(self, user_id):
        """Acepta una llamada entrante"""
        if user_id not in self.connected_users:
            return None

        partner = self.connected_users[user_id]['in_call_with']
        if not partner or partner not in self.connected_users:
            return None

        if self.connected_users[partner]['status'] != 'llamando':
            return None

        self.update_user_status(user_id, 'en_llamada', partner)
        self.update_user_status(partner, 'en_llamada', user_id)

        call_id = f"{min(user_id, partner)}_{max(user_id, partner)}"
        self.active_calls[call_id] = {
            'users': [user_id, partner], 
            'start_time': datetime.now().isoformat()
        }

        return partner

    def end_call(self, user_id):
        """Finaliza una llamada"""
        if user_id not in self.connected_users:
            return None

        partner = self.connected_users[user_id]['in_call_with']

        if partner and partner in self.connected_users:
            self.update_user_status(user_id, 'disponible', None)
            self.update_user_status(partner, 'disponible', None)

            # Registrar en base de datos si la llamada fue aceptada
            if self.connected_users[user_id]['status'] == 'en_llamada':
                call_id = f"{min(user_id, partner)}_{max(user_id, partner)}"
                if call_id in self.active_calls:
                    call_data = self.active_calls[call_id]
                    start_time = datetime.fromisoformat(call_data['start_time'])
                    duration = int((datetime.now() - start_time).total_seconds())

                    # Registrar llamada en base de datos
                    try:
                        self.db.log_call(user_id, partner, 'audio', duration)
                    except Exception as e:
                        logger.error(f"Error registrando llamada: {e}")

                    del self.active_calls[call_id]
        else:
            if self.connected_users[user_id]['status'] == 'en_llamada':
                self.update_user_status(user_id, 'disponible', None)

        return partner

    def decline_call(self, user_id):
        """Rechaza una llamada entrante"""
        if user_id not in self.connected_users:
            return None

        partner = self.connected_users[user_id]['in_call_with']
        if partner and partner in self.connected_users:
            self.update_user_status(user_id, 'disponible', None)
            self.update_user_status(partner, 'disponible', None)

        return partner

    def store_signal(self, target_id, signal_data):
        """Almacena señales WebRTC pendientes"""
        if target_id not in self.pending_signals:
            self.pending_signals[target_id] = []
        self.pending_signals[target_id].append(signal_data)

    def get_pending_signals(self, user_id):
        """Obtiene señales WebRTC pendientes"""
        if user_id in self.pending_signals:
            signals = self.pending_signals[user_id].copy()
            self.pending_signals[user_id].clear()
            return signals
        return []

# ========== SERVER HANDLERS ==========
db_manager = DatabaseManager()
user_manager = UserManager(db_manager)

async def websocket_handler(request):
    """Maneja conexiones WebSocket"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    # Obtener token de sesión del query string
    token = request.query.get('token')
    if not token:
        logger.warning("❌ Sin token en WebSocket")
        await ws.close()
        return ws

    # Validar sesión
    user_id = user_manager.validate_session(token)
    if not user_id:
        logger.warning("❌ Token inválido en WebSocket")
        await ws.close()
        return ws

    # Obtener datos del usuario desde la base de datos
    user_data = db_manager.get_user(user_id)
    if not user_data:
        logger.error(f"❌ Usuario no encontrado: {user_id}")
        await ws.close()
        return ws

    logger.info(f"🔗 WebSocket conectado: {user_data['username']} ({user_id})")

    try:
        # Agregar usuario conectado
        user_manager.add_connected_user(user_id, ws, user_data)

        # Enviar mensaje de registro
        await ws.send_json({
            'type': 'registered',
            'userId': user_id,
            'username': user_data['username'],
            'avatar_url': user_data['avatar_url'],
            'token': token,
            'onlineUsers': user_manager.get_connected_users(user_id),
            'avatarColor': f"#{random.randint(0, 0xFFFFFF):06x}"
        })

        # Enviar señales pendientes
        pending = user_manager.get_pending_signals(user_id)
        for signal in pending:
            await ws.send_json(signal)

        # Broadcast nueva lista de usuarios
        await broadcast_user_list()

        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get('type')
                    logger.debug(f"📩 {msg_type} de {user_id}")

                    if msg_type == 'heartbeat':
                        user_manager.update_heartbeat(user_id)
                        await ws.send_json({'type': 'heartbeat_ack'})

                    elif msg_type == 'get_users':
                        # Obtener todos los usuarios de la base de datos
                        all_users = db_manager.get_all_users(user_id)

                        # Marcar cuales están conectados
                        connected_ids = set(user_manager.connected_users.keys())
                        for user in all_users:
                            user['is_connected'] = user['id'] in connected_ids
                            if user['is_connected']:
                                user['status'] = user_manager.connected_users.get(user['id'], {}).get('status', 'disponible')
                            else:
                                user['status'] = 'desconectado'

                        await ws.send_json({
                            'type': 'user_list',
                            'users': all_users
                        })

                    elif msg_type == 'search_users':
                        query = data.get('query', '')
                        if query:
                            results = db_manager.search_users(query, user_id)
                            connected_ids = set(user_manager.connected_users.keys())
                            for user in results:
                                user['is_connected'] = user['id'] in connected_ids
                                if user['is_connected']:
                                    user['status'] = user_manager.connected_users.get(user['id'], {}).get('status', 'disponible')
                                else:
                                    user['status'] = 'desconectado'

                            await ws.send_json({
                                'type': 'search_results',
                                'users': results
                            })

                    elif msg_type == 'call_request':
                        target_id = data.get('targetId')
                        if user_manager.initiate_call(user_id, target_id):
                            target_ws = user_manager.connected_users[target_id]['ws']
                            await target_ws.send_json({
                                'type': 'incoming_call',
                                'callerId': user_id,
                                'callerName': user_manager.connected_users[user_id]['username'],
                                'callerAvatar': user_manager.connected_users[user_id]['avatar_url']
                            })
                            await broadcast_user_list()
                        else:
                            await ws.send_json({
                                'type': 'call_error',
                                'message': 'Usuario no disponible'
                            })

                    elif msg_type == 'call_accept':
                        partner = user_manager.accept_call(user_id)
                        if partner:
                            partner_ws = user_manager.connected_users[partner]['ws']
                            await partner_ws.send_json({
                                'type': 'call_accepted',
                                'calleeId': user_id,
                                'calleeName': user_manager.connected_users[user_id]['username']
                            })
                            await broadcast_user_list()

                    elif msg_type == 'call_decline':
                        partner = user_manager.decline_call(user_id)
                        if partner:
                            partner_ws = user_manager.connected_users[partner]['ws']
                            await partner_ws.send_json({'type': 'call_declined'})
                            await broadcast_user_list()

                    elif msg_type == 'call_end':
                        partner = user_manager.end_call(user_id)
                        if partner:
                            partner_ws = user_manager.connected_users[partner]['ws']
                            await partner_ws.send_json({'type': 'call_ended'})
                            await broadcast_user_list()

                    elif msg_type == 'call_connected':
                        partner_id = data.get('partnerId')
                        logger.info(f"✅ Llamada conectada: {user_id} <-> {partner_id}")

                    elif msg_type == 'webrtc_signal':
                        target_id = data.get('targetId')
                        signal = data.get('signal')
                        if target_id in user_manager.connected_users:
                            target_ws = user_manager.connected_users[target_id]['ws']
                            try:
                                await target_ws.send_json({
                                    'type': 'webrtc_signal',
                                    'signal': signal,
                                    'senderId': user_id
                                })
                            except:
                                # Si no se puede enviar, almacenar pendiente
                                user_manager.store_signal(target_id, {
                                    'type': 'webrtc_signal',
                                    'signal': signal,
                                    'senderId': user_id
                                })

                    elif msg_type == 'profile_updated':
                        # Notificar a todos que un perfil fue actualizado
                        await broadcast_user_list()

                except Exception as e:
                    logger.error(f"❌ Error procesando mensaje: {e}")
                    await ws.send_json({
                        'type': 'error',
                        'message': 'Error procesando solicitud'
                    })

            elif msg.type == web.WSMsgType.ERROR:
                logger.error(f"💥 Error WebSocket: {ws.exception()}")

    except Exception as e:
        logger.error(f"💥 Error en WebSocket: {e}")

    finally:
        logger.info(f"🔌 WebSocket desconectado: {user_id}")
        user_manager.remove_connected_user(user_id)
        await broadcast_user_list()

    return ws

async def broadcast_user_list():
    """Transmite la lista de usuarios a todos conectados"""
    for uid, data in list(user_manager.connected_users.items()):
        try:
            if not data['ws'].closed:
                # Obtener todos los usuarios de la base de datos
                all_users = db_manager.get_all_users(uid)
                
                # Marcar cuales están conectados
                connected_ids = set(user_manager.connected_users.keys())
                for user in all_users:
                    user['is_connected'] = user['id'] in connected_ids
                    if user['is_connected']:
                        user['status'] = user_manager.connected_users.get(user['id'], {}).get('status', 'disponible')
                    else:
                        user['status'] = 'desconectado'
                
                await data['ws'].send_json({
                    'type': 'user_list',
                    'users': all_users
                })
        except Exception as e:
            logger.error(f"❌ Error enviando lista a {uid}: {e}")
            user_manager.remove_connected_user(uid)

async def cleanup_inactive_users():
    """Limpia usuarios inactivos periódicamente"""
    while True:
        await asyncio.sleep(60)
        inactive = user_manager.check_inactive_users()
        if inactive:
            logger.info(f"🧹 Usuarios inactivos limpiados: {len(inactive)}")
            await broadcast_user_list()

# ========== HTTP HANDLERS ==========
async def handle_login(request):
    """Maneja la página de login/registro"""
    return web.FileResponse('./login.html')

async def handle_index(request):
    """Maneja la página principal"""
    return web.FileResponse('./index.html')

async def handle_static(request):
    """Sirve archivos estáticos"""
    path = request.match_info.get('path', '')
    full_path = Path('.').joinpath(path).resolve()
    
    # Verificar que el archivo exista y esté dentro del directorio actual
    if full_path.exists() and full_path.is_file() and str(full_path).startswith(str(Path('.').resolve())):
        return web.FileResponse(str(full_path))
    else:
        return web.Response(status=404)

async def handle_register(request):
    """Maneja el registro de usuarios"""
    try:
        data = await request.json()
        username = data.get('username')
        password = data.get('password')
        avatar = data.get('avatar')  # Base64 image

        if not username or not password:
            return web.json_response({'success': False, 'error': 'Faltan campos requeridos'})

        if len(username) < 3:
            return web.json_response({'success': False, 'error': 'El nombre debe tener al menos 3 caracteres'})

        if len(password) < 6:
            return web.json_response({'success': False, 'error': 'La contraseña debe tener al menos 6 caracteres'})

        # Crear usuario
        result = db_manager.create_user(username, password, avatar)

        if result:
            # Crear sesión
            token = user_manager.generate_session_token(result['id'])
            return web.json_response({
                'success': True,
                'user': result,
                'token': token
            })
        else:
            return web.json_response({'success': False, 'error': 'Usuario ya existe'})

    except Exception as e:
        logger.error(f"💥 Error en registro: {e}")
        return web.json_response({'success': False, 'error': 'Error interno del servidor'})

async def handle_login_api(request):
    """Maneja el login de usuarios"""
    try:
        data = await request.json()
        username = data.get('username')
        password = data.get('password')

        if not username or not password:
            return web.json_response({'success': False, 'error': 'Faltan campos requeridos'})

        # Verificar usuario
        user = db_manager.verify_user(username, password)

        if user:
            # Crear sesión
            token = user_manager.generate_session_token(user['id'])
            return web.json_response({
                'success': True,
                'user': user,
                'token': token
            })
        else:
            return web.json_response({'success': False, 'error': 'Credenciales inválidas'})

    except Exception as e:
        logger.error(f"💥 Error en login: {e}")
        return web.json_response({'success': False, 'error': 'Error interno del servidor'})

async def handle_update_profile(request):
    """Actualiza el perfil del usuario"""
    try:
        data = await request.json()
        token = data.get('token')
        user_id = user_manager.validate_session(token) if token else None

        if not user_id:
            return web.json_response({'success': False, 'error': 'Sesión inválida'})

        username = data.get('username')
        password = data.get('password')
        avatar = data.get('avatar')

        # Verificar si el usuario quiere cambiar nombre
        current_user = db_manager.get_user(user_id)
        if username and username != current_user['username'] and len(username) < 3:
            return web.json_response({'success': False, 'error': 'El nombre debe tener al menos 3 caracteres'})

        # Verificar si quiere cambiar contraseña
        if password and len(password) < 6:
            return web.json_response({'success': False, 'error': 'La nueva contraseña debe tener al menos 6 caracteres'})

        # Actualizar perfil
        updated_user = db_manager.update_user_profile(user_id, username, password, avatar)

        if updated_user:
            # Actualizar en usuarios conectados si está online
            if user_id in user_manager.connected_users:
                user_manager.connected_users[user_id]['username'] = updated_user['username']
                user_manager.connected_users[user_id]['avatar_url'] = updated_user['avatar_url']

            return web.json_response({
                'success': True,
                'user': {
                    'id': user_id,
                    'username': updated_user['username'],
                    'avatar_url': updated_user['avatar_url']
                }
            })
        else:
            return web.json_response({'success': False, 'error': 'Error actualizando perfil. ¿Nombre ya existe?'})

    except Exception as e:
        logger.error(f"💥 Error actualizando perfil: {e}")
        return web.json_response({'success': False, 'error': 'Error interno del servidor'})

async def handle_avatar(request):
    """Sirve avatares de usuarios"""
    path = request.match_info.get('path', '')
    full_path = Path('static/avatars').joinpath(path).resolve()
    
    # Verificar que el archivo esté dentro del directorio avatars
    avatars_dir = Path('static/avatars').resolve()
    if full_path.exists() and full_path.is_file() and str(full_path).startswith(str(avatars_dir)):
        return web.FileResponse(str(full_path))
    else:
        return web.Response(status=404)

# ========== SERVER SETUP ==========
async def start_server():
    """Inicia el servidor"""
    port = int(os.environ.get("PORT", 3000))
    host = "0.0.0.0"

    # Crear directorios necesarios
    os.makedirs('static/avatars', exist_ok=True)

    # Tarea para limpiar usuarios inactivos
    asyncio.create_task(cleanup_inactive_users())

    # Configurar aplicación
    app = web.Application()

    # Rutas WebSocket
    app.router.add_get('/ws', websocket_handler)

    # Rutas HTTP API
    app.router.add_post('/api/register', handle_register)
    app.router.add_post('/api/login', handle_login_api)
    app.router.add_post('/api/update_profile', handle_update_profile)

    # Rutas de archivos
    app.router.add_get('/', handle_login)
    app.router.add_get('/index', handle_index)
    app.router.add_get('/avatars/{path:.*}', handle_avatar)
    app.router.add_get('/{path:.*}', handle_static)

    # Iniciar servidor
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    print("=" * 50)
    print(f"🚀 Servidor WebRTC iniciado correctamente!")
    print(f"📡 WebSocket: ws://{host}:{port}/ws?token=TOKEN")
    print(f"🌐 HTTP: http://{host}:{port}/")
    print(f"💾 Base de datos: {db_manager.db_path}")
    print("=" * 50)

    # Mantener el servidor corriendo
    await asyncio.Future()

if __name__ == "__main__":
    try:
        asyncio.run(start_server())
    except KeyboardInterrupt:
        print("\n👋 Servidor detenido por el usuario")
    except Exception as e:
        print(f"💥 Error fatal: {e}")