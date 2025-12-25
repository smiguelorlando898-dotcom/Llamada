#!/usr/bin/env python3
"""
SERVIDOR DE SEÑALIZACIÓN WEBRTC - SISTEMA MEJORADO
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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class UserManager:
    def __init__(self):
        self.users = {}
        self.heartbeats = {}
        self.pending_signals = {}
        self.active_calls = {}

    def generate_avatar_color(self, user_id):
        colors = [
            '#2563eb', '#10b981', '#8b5cf6', '#f59e0b', '#ef4444',
            '#06b6d4', '#84cc16', '#f97316', '#6366f1', '#ec4899'
        ]
        hash_val = sum(ord(c) for c in user_id)
        return colors[hash_val % len(colors)]

    def add_user(self, user_id, websocket, username):
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
        if user_id in self.users:
            partner = self.users[user_id]['in_call_with']
            if partner and partner in self.users:
                self.users[partner]['in_call_with'] = None
                self.users[partner]['status'] = 'disponible'

            username = self.users[user_id]['username']
            if user_id in self.heartbeats: del self.heartbeats[user_id]
            if user_id in self.pending_signals: del self.pending_signals[user_id]
            del self.users[user_id]
            logger.info(f"🗑️ Usuario eliminado: {username}")
            return True
        return False

    def update_user_status(self, user_id, status, in_call_with=None):
        if user_id not in self.users:
            return False
        old_status = self.users[user_id]['status']
        old_partner = self.users[user_id]['in_call_with']
        self.users[user_id]['status'] = status
        self.users[user_id]['in_call_with'] = in_call_with
        self.users[user_id]['last_seen'] = datetime.now().isoformat()
        if old_partner and old_partner != in_call_with:
            if old_partner in self.users:
                self.users[old_partner]['in_call_with'] = None
                self.users[old_partner]['status'] = 'disponible'
        return True

    def update_heartbeat(self, user_id):
        if user_id in self.users:
            self.heartbeats[user_id] = time.time()
            return True
        return False

    def check_inactive_users(self):
        current_time = time.time()
        inactive = [uid for uid, hb in self.heartbeats.items() if current_time - hb > 30]
        for uid in inactive:
            logger.warning(f"⏰ Usuario inactivo: {uid}")
            self.remove_user(uid)
        return inactive

    def get_user_info(self, user_id):
        if user_id in self.users:
            u = self.users[user_id]
            return {
                'id': user_id,
                'username': u['username'],
                'status': u['status'],
                'avatar_color': u['avatar_color'],
                'in_call_with': u['in_call_with'],
                'last_seen': u['last_seen']
            }
        return None

    def get_online_users(self, exclude_user_id=None):
        online = []
        for uid, data in self.users.items():
            if uid != exclude_user_id:
                info = self.get_user_info(uid)
                if info:
                    online.append(info)
        online.sort(key=lambda x: x['username'].lower())
        return online

    def can_call_user(self, caller_id, target_id):
        if caller_id not in self.users or target_id not in self.users:
            return False
        if target_id == caller_id:
            return False
        if self.users[target_id]['status'] != 'disponible' or self.users[caller_id]['status'] != 'disponible':
            return False
        return True

    def initiate_call(self, caller_id, target_id):
        if not self.can_call_user(caller_id, target_id):
            return False
        self.update_user_status(caller_id, 'llamando', target_id)
        self.update_user_status(target_id, 'recibiendo_llamada', caller_id)
        logger.info(f"📞 Llamada iniciada: {self.users[caller_id]['username']} -> {self.users[target_id]['username']}")
        return True

    def accept_call(self, user_id):
        if user_id not in self.users:
            return None
        partner = self.users[user_id]['in_call_with']
        if not partner or partner not in self.users or self.users[partner]['status'] != 'llamando':
            return None
        self.update_user_status(user_id, 'en_llamada', partner)
        self.update_user_status(partner, 'en_llamada', user_id)
        call_id = f"{min(user_id, partner)}_{max(user_id, partner)}"
        self.active_calls[call_id] = {'users': [user_id, partner], 'start_time': datetime.now().isoformat()}
        return partner

    def end_call(self, user_id):
        if user_id not in self.users:
            return None
        partner = self.users[user_id]['in_call_with']
        if partner and partner in self.users:
            self.update_user_status(user_id, 'disponible', None)
            self.update_user_status(partner, 'disponible', None)
            call_id = f"{min(user_id, partner)}_{max(user_id, partner)}"
            if call_id in self.active_calls:
                del self.active_calls[call_id]
        else:
            if self.users[user_id]['status'] == 'en_llamada':
                self.update_user_status(user_id, 'disponible', None)
        return partner

    def decline_call(self, user_id):
        if user_id not in self.users:
            return None
        partner = self.users[user_id]['in_call_with']
        if partner and partner in self.users:
            self.update_user_status(user_id, 'disponible', None)
            self.update_user_status(partner, 'disponible', None)
        return partner

    def store_signal(self, target_id, signal_data):
        if target_id not in self.pending_signals:
            self.pending_signals[target_id] = []
        self.pending_signals[target_id].append(signal_data)

    def get_pending_signals(self, user_id):
        if user_id in self.pending_signals:
            signals = self.pending_signals[user_id].copy()
            self.pending_signals[user_id].clear()
            return signals
        return []

user_manager = UserManager()

async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    client_id = str(uuid4())[:8]

    try:
        pending = user_manager.get_pending_signals(client_id)
        for signal in pending:
            await ws.send_json(signal)

        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get('type')

                    if msg_type == 'register':
                        username = data.get('username', f'Usuario_{client_id}')
                        avatar_color = user_manager.add_user(client_id, ws, username)

                        await ws.send_json({
                            'type': 'registered',
                            'userId': client_id,
                            'username': username,
                            'avatarColor': avatar_color,
                            'onlineUsers': user_manager.get_online_users(client_id)
                        })

                        await broadcast_user_list()

                    elif msg_type == 'heartbeat':
                        user_manager.update_heartbeat(client_id)

                    elif msg_type == 'get_users':
                        await ws.send_json({
                            'type': 'user_list',
                            'users': user_manager.get_online_users(client_id)
                        })

                    elif msg_type == 'call_request':
                        target_id = data.get('targetId')
                        if user_manager.initiate_call(client_id, target_id):
                            target_ws = user_manager.users[target_id]['ws']
                            await target_ws.send_json({
                                'type': 'incoming_call',
                                'callerId': client_id,
                                'callerName': user_manager.users[client_id]['username'],
                                'callerAvatar': user_manager.users[client_id]['avatar_color']
                            })
                            await broadcast_user_list()
                        else:
                            await ws.send_json({
                                'type': 'call_error',
                                'message': 'Usuario no disponible en este momento'
                            })

                    elif msg_type == 'call_accept':
                        partner = user_manager.accept_call(client_id)
                        if partner:
                            await user_manager.users[partner]['ws'].send_json({
                                'type': 'call_accepted',
                                'calleeId': client_id,
                                'calleeName': user_manager.users[client_id]['username']
                            })
                            await broadcast_user_list()

                    elif msg_type == 'call_decline':
                        partner = user_manager.decline_call(client_id)
                        if partner:
                            await user_manager.users[partner]['ws'].send_json({'type': 'call_declined'})
                            await broadcast_user_list()

                    elif msg_type == 'call_end':
                        partner = user_manager.end_call(client_id)
                        if partner:
                            await user_manager.users[partner]['ws'].send_json({'type': 'call_ended'})
                            await broadcast_user_list()

                    elif msg_type == 'webrtc_signal':
                        target_id = data.get('targetId')
                        signal = data.get('signal')
                        if target_id in user_manager.users:
                            target_ws = user_manager.users[target_id]['ws']
                            try:
                                await target_ws.send_json({
                                    'type': 'webrtc_signal',
                                    'signal': signal,
                                    'senderId': client_id
                                })
                            except:
                                user_manager.store_signal(target_id, {
                                    'type': 'webrtc_signal',
                                    'signal': signal,
                                    'senderId': client_id
                                })
                except Exception as e:
                    logger.error(f"Error: {e}")

    finally:
        if user_manager.remove_user(client_id):
            await broadcast_user_list()

    return ws

async def broadcast_user_list():
    for uid, data in list(user_manager.users.items()):
        try:
            if not data['ws'].closed:
                await data['ws'].send_json({
                    'type': 'user_list',
                    'users': user_manager.get_online_users(uid)
                })
        except:
            user_manager.remove_user(uid)

async def cleanup_inactive_users():
    while True:
        await asyncio.sleep(60)
        if user_manager.check_inactive_users():
            await broadcast_user_list()

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
    print("🚀 Servidor corriendo en puerto", port)

    await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(start_server())