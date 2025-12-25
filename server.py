#!/usr/bin/env python3
"""
SERVIDOR DE SEÑALIZACIÓN WEBRTC - SISTEMA DE USUARIOS
Con lista de usuarios y estados
"""

import asyncio
import websockets
import json
from aiohttp import web
import logging
from datetime import datetime
import os
from uuid import uuid4

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============================================
# GESTIÓN DE USUARIOS Y CONEXIONES
# ============================================
class UserManager:
    def __init__(self):
        self.users = {}  # {user_id: {ws, username, status, in_call_with, avatar_color}}
        self.online_users = set()  # user_ids online
    
    def generate_avatar_color(self, user_id):
        """Generar color consistente para el avatar basado en user_id"""
        colors = [
            '#0088cc', '#00a884', '#ff6b6b', '#51cf66', '#ffd43b',
            '#20c997', '#be4bdb', '#f76707', '#339af0', '#ff8787'
        ]
        # Usar hash del user_id para seleccionar color
        hash_val = sum(ord(c) for c in user_id)
        return colors[hash_val % len(colors)]
    
    def add_user(self, user_id, websocket, username):
        """Agregar nuevo usuario"""
        avatar_color = self.generate_avatar_color(user_id)
        self.users[user_id] = {
            'ws': websocket,
            'username': username,
            'status': 'disponible',
            'in_call_with': None,
            'avatar_color': avatar_color,
            'connected_at': datetime.now().isoformat()
        }
        self.online_users.add(user_id)
        logger.info(f"✅ Usuario registrado: {username} ({user_id})")
        return avatar_color
    
    def remove_user(self, user_id):
        """Eliminar usuario desconectado"""
        if user_id in self.users:
            # Notificar al compañero si estaba en llamada
            partner = self.users[user_id]['in_call_with']
            if partner and partner in self.users:
                self.users[partner]['in_call_with'] = None
                self.users[partner]['status'] = 'disponible'
                # Notificar desconexión
                asyncio.create_task(self.notify_partner_disconnected(partner, user_id))
            
            username = self.users[user_id]['username']
            if user_id in self.online_users:
                self.online_users.remove(user_id)
            del self.users[user_id]
            logger.info(f"🗑️  Usuario eliminado: {username}")
    
    async def notify_partner_disconnected(self, partner_id, disconnected_id):
        """Notificar al compañero que se desconectó"""
        if partner_id in self.users and self.users[partner_id]['ws']:
            await self.users[partner_id]['ws'].send_json({
                'type': 'peer_disconnected',
                'message': 'Tu compañero se ha desconectado'
            })
    
    def update_status(self, user_id, status, in_call_with=None):
        """Actualizar estado del usuario"""
        if user_id in self.users:
            self.users[user_id]['status'] = status
            self.users[user_id]['in_call_with'] = in_call_with
            logger.info(f"📊 {self.users[user_id]['username']} -> {status}")
            return True
        return False
    
    def get_user_info(self, user_id):
        """Obtener información pública del usuario"""
        if user_id in self.users:
            user = self.users[user_id]
            return {
                'id': user_id,
                'username': user['username'],
                'status': user['status'],
                'avatar_color': user['avatar_color'],
                'in_call_with': user['in_call_with']
            }
        return None
    
    def get_online_users(self, exclude_user_id=None):
        """Obtener lista de usuarios online (excepto el usuario excluido)"""
        online_list = []
        for user_id in self.online_users:
            if user_id != exclude_user_id:
                user_info = self.get_user_info(user_id)
                if user_info:
                    online_list.append(user_info)
        return online_list
    
    def find_user_by_username(self, username):
        """Buscar usuario por nombre"""
        for user_id, user_data in self.users.items():
            if user_data['username'] == username:
                return user_id
        return None
    
    def can_call_user(self, caller_id, target_id):
        """Verificar si se puede llamar a un usuario"""
        if (target_id in self.users and 
            caller_id in self.users and
            target_id != caller_id):
            target = self.users[target_id]
            return target['status'] == 'disponible'
        return False
    
    def initiate_call(self, caller_id, target_id):
        """Iniciar una llamada entre dos usuarios"""
        if (self.can_call_user(caller_id, target_id) and
            caller_id in self.users and
            target_id in self.users):
            
            # Actualizar estados
            self.update_status(caller_id, 'llamando', target_id)
            self.update_status(target_id, 'recibiendo_llamada', caller_id)
            
            logger.info(f"📞 {self.users[caller_id]['username']} llama a {self.users[target_id]['username']}")
            return True
        return False
    
    def accept_call(self, user_id):
        """Aceptar una llamada entrante"""
        if user_id in self.users:
            partner = self.users[user_id]['in_call_with']
            if partner and partner in self.users:
                # Ambos pasan a estado "en_llamada"
                self.update_status(user_id, 'en_llamada', partner)
                self.update_status(partner, 'en_llamada', user_id)
                logger.info(f"✅ Llamada aceptada entre {self.users[user_id]['username']} y {self.users[partner]['username']}")
                return partner
        return None
    
    def end_call(self, user_id):
        """Terminar una llamada"""
        if user_id in self.users:
            partner = self.users[user_id]['in_call_with']
            if partner and partner in self.users:
                # Volver a disponible
                self.update_status(partner, 'disponible', None)
                self.update_status(user_id, 'disponible', None)
                logger.info(f"📞 Llamada terminada entre {self.users[user_id]['username']} y {self.users[partner]['username']}")
                return partner
        return None
    
    def decline_call(self, user_id):
        """Rechazar una llamada entrante"""
        if user_id in self.users:
            partner = self.users[user_id]['in_call_with']
            if partner and partner in self.users:
                # Ambos vuelven a disponible
                self.update_status(partner, 'disponible', None)
                self.update_status(user_id, 'disponible', None)
                logger.info(f"❌ {self.users[user_id]['username']} rechazó llamada de {self.users[partner]['username']}")
                return partner
        return None

user_manager = UserManager()

# ============================================
# MANEJADOR WEBSOCKET
# ============================================
async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    client_id = str(uuid4())[:8]  # ID más corto para mostrar
    username = None
    
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get('type', 'unknown')
                    
                    if msg_type == 'register':
                        # Registrar nuevo usuario
                        username = data.get('username', f'Usuario_{client_id}')
                        avatar_color = user_manager.add_user(client_id, ws, username)
                        
                        # Enviar información de registro
                        await ws.send_json({
                            'type': 'registered',
                            'userId': client_id,
                            'username': username,
                            'avatarColor': avatar_color,
                            'onlineUsers': user_manager.get_online_users(exclude_user_id=client_id)
                        })
                        
                        # Notificar a todos que hay un nuevo usuario
                        await broadcast_user_list()
                        logger.info(f"👤 {username} se registró")
                    
                    elif msg_type == 'get_users':
                        # Enviar lista de usuarios
                        await ws.send_json({
                            'type': 'user_list',
                            'users': user_manager.get_online_users(exclude_user_id=client_id)
                        })
                    
                    elif msg_type == 'call_request':
                        # Solicitar llamada a otro usuario
                        target_id = data.get('targetId')
                        caller_name = user_manager.users[client_id]['username'] if client_id in user_manager.users else 'Usuario'
                        
                        if user_manager.initiate_call(client_id, target_id):
                            target_ws = user_manager.users[target_id]['ws']
                            
                            # Notificar al objetivo
                            await target_ws.send_json({
                                'type': 'incoming_call',
                                'callerId': client_id,
                                'callerName': caller_name,
                                'callerAvatar': user_manager.users[client_id]['avatar_color']
                            })
                            
                            # Actualizar lista para todos
                            await broadcast_user_list()
                    
                    elif msg_type == 'call_accept':
                        # Aceptar llamada entrante
                        partner_id = user_manager.accept_call(client_id)
                        if partner_id:
                            caller_ws = user_manager.users[partner_id]['ws']
                            
                            # Notificar al que inició la llamada
                            await caller_ws.send_json({
                                'type': 'call_accepted',
                                'calleeId': client_id,
                                'calleeName': user_manager.users[client_id]['username']
                            })
                            
                            # Actualizar lista para todos
                            await broadcast_user_list()
                    
                    elif msg_type == 'call_decline':
                        # Rechazar llamada
                        partner_id = user_manager.decline_call(client_id)
                        if partner_id:
                            caller_ws = user_manager.users[partner_id]['ws']
                            await caller_ws.send_json({
                                'type': 'call_declined',
                                'message': 'Llamada rechazada'
                            })
                            
                            # Actualizar lista
                            await broadcast_user_list()
                    
                    elif msg_type == 'call_end':
                        # Terminar llamada
                        partner_id = user_manager.end_call(client_id)
                        if partner_id:
                            partner_ws = user_manager.users[partner_id]['ws']
                            await partner_ws.send_json({
                                'type': 'call_ended',
                                'message': 'Llamada finalizada'
                            })
                            
                            # Actualizar lista
                            await broadcast_user_list()
                    
                    elif msg_type == 'webrtc_signal':
                        # Señal WebRTC
                        target_id = data.get('targetId')
                        signal = data.get('signal')
                        
                        if target_id in user_manager.users:
                            target_ws = user_manager.users[target_id]['ws']
                            await target_ws.send_json({
                                'type': 'webrtc_signal',
                                'signal': signal,
                                'senderId': client_id
                            })
                    
                except json.JSONDecodeError:
                    logger.error(f"❌ JSON inválido")
                except Exception as e:
                    logger.error(f"💥 Error procesando mensaje: {e}")
            
            elif msg.type == web.WSMsgType.ERROR:
                logger.error(f"💥 Error WS: {ws.exception()}")
    
    except Exception as e:
        logger.error(f"💥 Error en conexión: {e}")
    finally:
        # Limpiar usuario desconectado
        if client_id in user_manager.users:
            user_manager.remove_user(client_id)
            await broadcast_user_list()
    
    return ws

async def broadcast_user_list():
    """Enviar lista actualizada de usuarios a todos conectados"""
    for user_id in user_manager.online_users:
        try:
            ws = user_manager.users[user_id]['ws']
            await ws.send_json({
                'type': 'user_list',
                'users': user_manager.get_online_users(exclude_user_id=user_id)
            })
        except:
            pass

# ============================================
# SERVIDOR HTTP
# ============================================
async def handle_login(request):
    return web.FileResponse('./login.html')

async def handle_index(request):
    return web.FileResponse('./index.html')

async def handle_status(request):
    return web.json_response({
        'status': 'online',
        'timestamp': datetime.now().isoformat(),
        'totalUsers': len(user_manager.users),
        'onlineUsers': len(user_manager.online_users)
    })

async def start_server():
    print("=" * 60)
    print("🚀 SERVIDOR WEBRTC - SISTEMA DE USUARIOS")
    print("=" * 60)
    
    # Obtener puerto de Render o usar 3000 por defecto
    port = int(os.environ.get("PORT", 3000))
    host = "0.0.0.0"
    
    print(f"🌐 Servidor iniciado en: http://{host}:{port}")
    print(f"📞 WebSocket disponible en: /ws")
    print("=" * 60)
    
    # Configurar app
    app = web.Application()
    
    # Rutas HTTP
    app.router.add_get('/', handle_login)
    app.router.add_get('/index', handle_index)
    app.router.add_get('/status', handle_status)
    app.router.add_get('/ws', websocket_handler)
    app.router.add_static('/', './')
    
    # Iniciar servidor
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    
    print("✅ Servidor listo")
    print("👥 Esperando usuarios...")
    print("=" * 60)
    
    await asyncio.Future()

# ============================================
# EJECUCIÓN
# ============================================
if __name__ == "__main__":
    try:
        asyncio.run(start_server())
    except KeyboardInterrupt:
        print("\n⏹️  Servidor detenido")