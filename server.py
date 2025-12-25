#!/usr/bin/env python3
"""
SERVIDOR DE SEÑALIZACIÓN WEBRTC - SISTEMA MEJORADO
Con sincronización robusta y manejo de llamadas
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

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============================================
# GESTIÓN DE USUARIOS Y CONEXIONES
# ============================================
class UserManager:
    def __init__(self):
        self.users = {}  # {user_id: {ws, username, status, in_call_with, avatar_color, last_seen}}
        self.heartbeats = {}  # {user_id: last_heartbeat}
        self.pending_signals = {}  # {user_id: [signals]} para señales pendientes
        self.active_calls = {}  # {call_id: {users: [user1, user2], start_time}}

    def generate_avatar_color(self, user_id):
        """Generar color consistente para el avatar basado en user_id"""
        colors = [
            '#2563eb', '#10b981', '#8b5cf6', '#f59e0b', '#ef4444',
            '#06b6d4', '#84cc16', '#f97316', '#6366f1', '#ec4899'
        ]
        hash_val = sum(ord(c) for c in user_id)
        return colors[hash_val % len(colors)]

    def add_user(self, user_id, websocket, username):
        """Agregar nuevo usuario"""
        avatar_color = self.generate_avatar_color(user_id)
        current_time = datetime.now().isoformat()

        self.users[user_id] = {
            'ws': websocket,
            'username': username,
            'status': 'disponible',
            'in_call_with': None,
            'avatar_color': avatar_color,
            'connected_at': current_time,
            'last_seen': current_time,
            'heartbeat': time.time()
        }

        self.heartbeats[user_id] = time.time()
        self.pending_signals[user_id] = []
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
                logger.info(f"⚠️  Compañero {partner} liberado por desconexión")

            username = self.users[user_id]['username']

            # Limpiar
            if user_id in self.heartbeats:
                del self.heartbeats[user_id]
            if user_id in self.pending_signals:
                del self.pending_signals[user_id]

            del self.users[user_id]
            logger.info(f"🗑️  Usuario eliminado: {username}")
            return True
        return False

    def update_user_status(self, user_id, status, in_call_with=None):
        """Actualizar estado del usuario de manera atómica"""
        if user_id not in self.users:
            return False

        old_status = self.users[user_id]['status']
        old_partner = self.users[user_id]['in_call_with']

        self.users[user_id]['status'] = status
        self.users[user_id]['in_call_with'] = in_call_with
        self.users[user_id]['last_seen'] = datetime.now().isoformat()

        logger.info(f"📊 {self.users[user_id]['username']}: {old_status} -> {status}")

        # Si estaba en llamada y ahora no, liberar al compañero
        if old_partner and old_partner != in_call_with:
            if old_partner in self.users:
                self.users[old_partner]['in_call_with'] = None
                self.users[old_partner]['status'] = 'disponible'
                logger.info(f"🔓 {self.users[old_partner]['username']} liberado")

        return True

    def update_heartbeat(self, user_id):
        """Actualizar heartbeat del usuario"""
        if user_id in self.users:
            self.heartbeats[user_id] = time.time()
            return True
        return False

    def check_inactive_users(self):
        """Verificar y eliminar usuarios inactivos"""
        current_time = time.time()
        inactive_users = []

        for user_id, last_heartbeat in self.heartbeats.items():
            if current_time - last_heartbeat > 30:  # 30 segundos sin heartbeat
                inactive_users.append(user_id)

        for user_id in inactive_users:
            logger.warning(f"⏰ Usuario inactivo: {user_id}")
            self.remove_user(user_id)

        return inactive_users

    def get_user_info(self, user_id):
        """Obtener información pública del usuario"""
        if user_id in self.users:
            user = self.users[user_id]
            return {
                'id': user_id,
                'username': user['username'],
                'status': user['status'],
                'avatar_color': user['avatar_color'],
                'in_call_with': user['in_call_with'],
                'last_seen': user['last_seen']
            }
        return None

    def get_online_users(self, exclude_user_id=None):
        """Obtener lista de usuarios online (excepto el usuario excluido)"""
        online_list = []
        for user_id, user_data in self.users.items():
            if user_id != exclude_user_id:
                user_info = self.get_user_info(user_id)
                if user_info:
                    online_list.append(user_info)

        # Ordenar por nombre
        online_list.sort(key=lambda x: x['username'].lower())
        return online_list

    def can_call_user(self, caller_id, target_id):
        """Verificar si se puede llamar a un usuario"""
        # Primero verificar que ambos existan
        if caller_id not in self.users or target_id not in self.users:
            return False
        if target_id == caller_id:
            return False

        caller = self.users[caller_id]
        target = self.users[target_id]

        # Ambos deben estar disponibles
        if target['status'] == 'disponible' and caller['status'] == 'disponible':
            return True

        return False

    def initiate_call(self, caller_id, target_id):
        """Iniciar una llamada entre dos usuarios"""
        if not self.can_call_user(caller_id, target_id):
            return False

        # Actualizar estados de manera atómica
        self.update_user_status(caller_id, 'llamando', target_id)
        self.update_user_status(target_id, 'recibiendo_llamada', caller_id)

        logger.info(f"📞 {self.users[caller_id]['username']} llama a {self.users[target_id]['username']}")
        return True

    def accept_call(self, user_id):
        """Aceptar una llamada entrante"""
        if user_id not in self.users:
            return None

        partner = self.users[user_id]['in_call_with']
        if not partner or partner not in self.users:
            return None

        # Verificar que el compañero todavía está llamando
        if self.users[partner]['status'] != 'llamando':
            return None

        # Actualizar ambos estados
        self.update_user_status(user_id, 'en_llamada', partner)
        self.update_user_status(partner, 'en_llamada', user_id)

        # Registrar tiempo de inicio de llamada
        call_id = f"{min(user_id, partner)}_{max(user_id, partner)}"
        self.active_calls[call_id] = {
            'users': [user_id, partner],
            'start_time': datetime.now().isoformat()
        }

        logger.info(f"✅ Llamada aceptada entre {self.users[user_id]['username']} y {self.users[partner]['username']}")
        return partner

    def end_call(self, user_id):
        """Terminar una llamada"""
        if user_id not in self.users:
            return None

        partner = self.users[user_id]['in_call_with']
        if not partner or partner not in self.users:
            # Si no hay compañero pero el usuario está en llamada, liberarlo
            if self.users[user_id]['status'] == 'en_llamada':
                self.update_user_status(user_id, 'disponible', None)
            return None

        # Actualizar ambos estados
        self.update_user_status(user_id, 'disponible', None)
        self.update_user_status(partner, 'disponible', None)

        # Limpiar registro de llamada activa
        call_id = f"{min(user_id, partner)}_{max(user_id, partner)}"
        if call_id in self.active_calls:
            del self.active_calls[call_id]

        logger.info(f"📞 Llamada terminada entre {self.users[user_id]['username']} y {self.users[partner]['username']}")
        return partner

    def decline_call(self, user_id):
        """Rechazar una llamada entrante"""
        if user_id not in self.users:
            return None

        partner = self.users[user_id]['in_call_with']
        if not partner or partner not in self.users:
            return None

        # Actualizar ambos estados
        self.update_user_status(user_id, 'disponible', None)
        self.update_user_status(partner, 'disponible', None)

        logger.info(f"❌ {self.users[user_id]['username']} rechazó llamada de {self.users[partner]['username']}")
        return partner

    def store_signal(self, target_id, signal_data):
        """Almacenar señal WebRTC para un usuario"""
        if target_id not in self.pending_signals:
            self.pending_signals[target_id] = []
        self.pending_signals[target_id].append(signal_data)
        return True

    def get_pending_signals(self, user_id):
        """Obtener señales WebRTC pendientes para un usuario"""
        if user_id in self.pending_signals:
            signals = self.pending_signals[user_id].copy()
            self.pending_signals[user_id].clear()
            return signals
        return []

user_manager = UserManager()

# ============================================
# MANEJADOR WEBSOCKET
# ============================================
async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    client_id = str(uuid4())[:8]
    username = None

    try:
        # Enviar señales pendientes si las hay (por reconexión rápida)
        pending_signals = user_manager.get_pending_signals(client_id)
        for signal in pending_signals:
            await ws.send_json(signal)

        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get('type', 'unknown')

                    if msg_type == 'register':
                        username = data.get('username', f'Usuario_{client_id}')
                        avatar_color = user_manager.add_user(client_id, ws, username)

                        await ws.send_json({
                            'type': 'registered',
                            'userId': client_id,
                            'username': username,
                            'avatarColor': avatar_color,
                            'onlineUsers': user_manager.get_online_users(exclude_user_id=client_id),
                            'timestamp': datetime.now().isoformat()
                        })

                        await broadcast_user_list()
                        logger.info(f"👤 {username} se registró exitosamente")

                    elif msg_type == 'heartbeat':
                        user_manager.update_heartbeat(client_id)
                        await ws.send_json({'type': 'heartbeat_ack'})

                    elif msg_type == 'get_users':
                        await ws.send_json({
                            'type': 'user_list',
                            'users': user_manager.get_online_users(exclude_user_id=client_id)
                        })

                    elif msg_type == 'call_request':
                        target_id = data.get('targetId')
                        if user_manager.initiate_call(client_id, target_id):
                            target_ws = user_manager.users[target_id]['ws']
                            caller_name = user_manager.users[client_id]['username']
                            await target_ws.send_json({
                                'type': 'incoming_call',
                                'callerId': client_id,
                                'callerName': caller_name,
                                'callerAvatar': user_manager.users[client_id]['avatar_color']
                            })
                            await broadcast_user_list()
                        else:
                            await ws.send_json({
                                'type': 'call_error',
                                'message': 'No se puede llamar a este usuario'
                            })

                    elif msg_type == 'call_accept':
                        partner_id = user_manager.accept_call(client_id)
                        if partner_id:
                            caller_ws = user_manager.users[partner_id]['ws']
                            await caller_ws.send_json({
                                'type': 'call_accepted',
                                'calleeId': client_id,
                                'calleeName': user_manager.users[client_id]['username']
                            })
                            await broadcast_user_list()

                    elif msg_type == 'call_decline':
                        partner_id = user_manager.decline_call(client_id)
                        if partner_id:
                            caller_ws = user_manager.users[partner_id]['ws']
                            await caller_ws.send_json({'type': 'call_declined'})

                            await broadcast_user_list()

                    elif msg_type == 'call_end':
                        partner_id = user_manager.end_call(client_id)
                        if partner_id:
                            partner_ws = user_manager.users[partner_id]['ws']
                            await partner_ws.send_json({'type': 'call_ended'})
                            await broadcast_user_list()

                    elif msg_type == 'webrtc_signal':
                        target_id = data.get('targetId')
                        signal = data.get('signal')
                        sender_id = client_id

                        if target_id in user_manager.users:
                            target_ws = user_manager.users[target_id]['ws']
                            signal_data = {
                                'type': 'webrtc_signal',
                                'signal': signal,
                                'senderId': sender_id
                            }
                            try:
                                await target_ws.send_json(signal_data)
                            except:
                                user_manager.store_signal(target_id, signal_data)
                        else:
                            logger.warning(f"⚠️  Usuario objetivo {target_id} no encontrado")

                except json.JSONDecodeError:
                    await ws.send_json({'type': 'error', 'message': 'JSON inválido'})
                except Exception as e:
                    logger.error(f"Error procesando mensaje: {e}")
                    await ws.send_json({'type': 'error', 'message': 'Error interno'})

    finally:
        logger.info(f"🔌 Conexión cerrada: {client_id}")
        if user_manager.remove_user(client_id):
            await broadcast_user_list()

    return ws

async def broadcast_user_list():
    for user_id, user_data in list(user_manager.users.items()):
        try:
            if not user_data['ws'].closed:
                await user_data['ws'].send_json({
                    'type': 'user_list',
                    'users': user_manager.get_online_users(exclude_user_id=user_id)
                })
        except:
            user_manager.remove_user(user_id)

async def cleanup_inactive_users():
    while True:
        await asyncio.sleep(60)
        inactive = user_manager.check_inactive_users()
        if inactive:
            await broadcast_user_list()

# ============================================
# SERVIDOR HTTP
# ============================================
async def handle_login(request):
    return web.FileResponse('./login.html')

async def handle_index(request):
    return web.FileResponse('./index.html')

async def start_server():
    port = int(os.environ.get("PORT", 3000))
    host = "0.0.0.0"

    asyncio.create_task(cleanup_inactive_users())

    app = web.Application()
    app.router.add_get('/', handle_login)
    app.router.add_get('/index', handle_index)
    app.router.add_get('/ws', websocket_handler)
    app.router.add_static('/', './')

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    print("🚀 Servidor iniciado correctamente")

    await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(start_server())