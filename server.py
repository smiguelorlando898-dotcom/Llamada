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
from database import db_manager
import base64

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class UserManager:
    def __init__(self):
        self.connected_users = {}  # user_id -> {ws, username, etc}
        self.heartbeats = {}
        self.pending_signals = {}
        self.active_calls = {}
        self.user_sessions = {}  # session_token -> user_id

    def generate_session_token(self, user_id):
        """Genera un token de sesión único"""
        return f"{user_id}_{uuid4().hex[:16]}"

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
        db_manager.update_user_status(user_id, True)
        
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
            db_manager.update_user_status(user_id, False)
            
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
                    
                    # Determinar tipo de llamada (sería mejor obtener del cliente)
                    call_type = 'audio'  # Por defecto
                    
                    # Registrar en base de datos
                    try:
                        db_manager.log_call(user_id, partner, call_type, duration)
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

    def create_session(self, user_id):
        """Crea una sesión para el usuario"""
        token = self.generate_session_token(user_id)
        self.user_sessions[token] = {
            'user_id': user_id,
            'created_at': time.time()
        }
        return token

    def validate_session(self, token):
        """Valida un token de sesión"""
        if token in self.user_sessions:
            session = self.user_sessions[token]
            # Verificar si la sesión no ha expirado (24 horas)
            if time.time() - session['created_at'] < 86400:
                return session['user_id']
        return None

user_manager = UserManager()

async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    # Obtener token de sesión del query string
    token = request.query.get('token')
    if not token:
        await ws.close()
        return ws
    
    # Validar sesión
    user_id = user_manager.validate_session(token)
    if not user_id:
        await ws.close()
        return ws
    
    # Obtener datos del usuario desde la base de datos
    user_data = db_manager.get_user(user_id)
    if not user_data:
        await ws.close()
        return ws
    
    print(f"🔗 Cliente WebSocket conectado: {user_data['username']} ({user_id})")
    
    try:
        # Agregar usuario conectado
        user_manager.add_connected_user(user_id, ws, user_data)
        
        # Enviar señales pendientes
        pending = user_manager.get_pending_signals(user_id)
        for signal in pending:
            await ws.send_json(signal)
        
        # Enviar lista inicial de usuarios conectados
        await ws.send_json({
            'type': 'user_list',
            'users': user_manager.get_connected_users(user_id)
        })
        
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get('type')
                    
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
                            user['status'] = 'disponible' if user['is_connected'] else 'desconectado'
                        
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
                                user['status'] = 'disponible' if user['is_connected'] else 'desconectado'
                            
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
                            await user_manager.connected_users[partner]['ws'].send_json({
                                'type': 'call_accepted',
                                'calleeId': user_id,
                                'calleeName': user_manager.connected_users[user_id]['username']
                            })
                            await broadcast_user_list()
                    
                    elif msg_type == 'call_decline':
                        partner = user_manager.decline_call(user_id)
                        if partner:
                            await user_manager.connected_users[partner]['ws'].send_json({'type': 'call_declined'})
                            await broadcast_user_list()
                    
                    elif msg_type == 'call_end':
                        partner = user_manager.end_call(user_id)
                        if partner:
                            await user_manager.connected_users[partner]['ws'].send_json({'type': 'call_ended'})
                            await broadcast_user_list()
                    
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
                                user_manager.store_signal(target_id, {
                                    'type': 'webrtc_signal',
                                    'signal': signal,
                                    'senderId': user_id
                                })
                    
                    elif msg_type == 'profile_updated':
                        # Notificar a todos que un perfil fue actualizado
                        await broadcast_user_list()
                        
                except Exception as e:
                    logger.error(f"Error procesando mensaje: {e}")
    
    except Exception as e:
        logger.error(f"Error en WebSocket: {e}")
    
    finally:
        print(f"🔌 Cliente desconectado: {user_id}")
        user_manager.remove_connected_user(user_id)
        await broadcast_user_list()
    
    return ws

async def broadcast_user_list():
    """Transmite la lista de usuarios a todos conectados"""
    for uid, data in list(user_manager.connected_users.items()):
        try:
            if not data['ws'].closed:
                await data['ws'].send_json({
                    'type': 'user_list',
                    'users': user_manager.get_connected_users(uid)
                })
        except:
            user_manager.remove_connected_user(uid)

async def cleanup_inactive_users():
    """Limpia usuarios inactivos periódicamente"""
    while True:
        await asyncio.sleep(60)
        inactive = user_manager.check_inactive_users()
        if inactive:
            await broadcast_user_list()

async def handle_login(request):
    """Maneja la página de login/registro"""
    return web.FileResponse('./login.html')

async def handle_index(request):
    """Maneja la página principal"""
    return web.FileResponse('./index.html')

async def handle_register(request):
    """Maneja el registro de usuarios"""
    try:
        data = await request.json()
        username = data.get('username')
        password = data.get('password')
        avatar_data = data.get('avatar')  # Base64 image
        
        if not username or not password:
            return web.json_response({'success': False, 'error': 'Faltan campos requeridos'})
        
        # Crear usuario
        result = db_manager.create_user(username, password, avatar_data)
        
        if result:
            # Crear sesión
            token = user_manager.create_session(result['id'])
            return web.json_response({
                'success': True,
                'user': result,
                'token': token
            })
        else:
            return web.json_response({'success': False, 'error': 'Usuario ya existe'})
    
    except Exception as e:
        logger.error(f"Error en registro: {e}")
        return web.json_response({'success': False, 'error': 'Error interno'})

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
            token = user_manager.create_session(user['id'])
            return web.json_response({
                'success': True,
                'user': user,
                'token': token
            })
        else:
            return web.json_response({'success': False, 'error': 'Credenciales inválidas'})
    
    except Exception as e:
        logger.error(f"Error en login: {e}")
        return web.json_response({'success': False, 'error': 'Error interno'})

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
        avatar_data = data.get('avatar')
        
        # Actualizar perfil
        updated_user = db_manager.update_user_profile(user_id, username, password, avatar_data)
        
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
            return web.json_response({'success': False, 'error': 'Error actualizando perfil'})
    
    except Exception as e:
        logger.error(f"Error actualizando perfil: {e}")
        return web.json_response({'success': False, 'error': 'Error interno'})

async def handle_avatar(request):
    """Sirve avatares de usuarios"""
    avatar_path = request.match_info.get('path', '')
    full_path = os.path.join('static', 'avatars', avatar_path)
    
    if os.path.exists(full_path):
        return web.FileResponse(full_path)
    else:
        return web.Response(status=404)

async def start_server():
    """Inicia el servidor"""
    port = int(os.environ.get("PORT", 3000))
    host = "0.0.0.0"
    
    # Crear directorio para avatares
    os.makedirs('static/avatars', exist_ok=True)
    
    # Tarea para limpiar usuarios inactivos
    asyncio.create_task(cleanup_inactive_users())
    
    # Configurar aplicación
    app = web.Application()
    
    # Rutas
    app.router.add_get('/', handle_login)
    app.router.add_get('/index', handle_index)
    app.router.add_get('/ws', websocket_handler)
    app.router.add_post('/api/register', handle_register)
    app.router.add_post('/api/login', handle_login_api)
    app.router.add_post('/api/update_profile', handle_update_profile)
    app.router.add_get('/avatars/{path:.*}', handle_avatar)
    app.router.add_static('/', './')
    
    # Iniciar servidor
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    
    print(f"🚀 Servidor corriendo en http://{host}:{port}")
    print(f"📡 WebSocket: ws://{host}:{port}/ws?token=TOKEN")
    print(f"💾 Base de datos: {db_manager.db_path}")
    
    await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(start_server())