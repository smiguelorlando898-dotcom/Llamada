#!/usr/bin/env python3
"""
SERVIDOR DE SEÑALIZACIÓN WEBRTC - VERSIÓN PARA RENDER
"""

import asyncio
import websockets
import json
from aiohttp import web
import logging
from datetime import datetime
import os

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============================================
# GESTIÓN DE CONEXIONES
# ============================================
class ConnectionManager:
    def __init__(self):
        self.clients = {}  # {client_id: {ws, username, partner}}
        self.waiting = []  # Clientes esperando pareja
    
    def add_client(self, client_id, websocket):
        username = f"Usuario_{client_id[-4:]}"
        self.clients[client_id] = {
            'ws': websocket,
            'username': username,
            'partner': None,
            'connected_at': datetime.now()
        }
        self.waiting.append(client_id)
        logger.info(f"✅ Cliente conectado: {username}")
        return username
    
    def remove_client(self, client_id):
        if client_id in self.clients:
            # Notificar al compañero
            partner = self.clients[client_id]['partner']
            if partner and partner in self.clients:
                self.clients[partner]['partner'] = None
                if partner not in self.waiting:
                    self.waiting.append(partner)
                logger.info(f"⚠️  Compañero {partner} ahora está esperando")
            
            # Limpiar
            if client_id in self.waiting:
                self.waiting.remove(client_id)
            
            username = self.clients[client_id]['username']
            del self.clients[client_id]
            logger.info(f"🗑️  Cliente eliminado: {username}")
    
    def pair_clients(self, client1_id, client2_id):
        if client1_id in self.clients and client2_id in self.clients:
            self.clients[client1_id]['partner'] = client2_id
            self.clients[client2_id]['partner'] = client1_id
            
            # Remover de espera
            for client_id in [client1_id, client2_id]:
                if client_id in self.waiting:
                    self.waiting.remove(client_id)
            
            logger.info(f"🤝 Pareja creada: {client1_id} <-> {client2_id}")
            return True
        return False
    
    def find_partner_for(self, client_id):
        if client_id not in self.waiting:
            return None
        
        for other_id in self.waiting:
            if other_id != client_id:
                return other_id
        return None

manager = ConnectionManager()

# ============================================
# MANEJADOR WEBSOCKET
# ============================================
async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    client_id = str(id(ws))
    
    try:
        # 1. REGISTRAR CLIENTE
        username = manager.add_client(client_id, ws)
        
        # 2. ENVIAR INFO DE CONEXIÓN
        await ws.send_json({
            'type': 'connection_info',
            'userId': client_id,
            'username': username,
            'timestamp': datetime.now().isoformat()
        })
        logger.info(f"📤 connection_info enviado a {client_id}")
        
        # 3. BUSCAR PAREJA
        partner_id = manager.find_partner_for(client_id)
        
        if partner_id:
            if manager.pair_clients(client_id, partner_id):
                client_name = manager.clients[client_id]['username']
                partner_name = manager.clients[partner_id]['username']
                
                # Enviar a AMBOS clientes
                timestamp = datetime.now().isoformat()
                
                await ws.send_json({
                    'type': 'peer_connected',
                    'peerId': partner_id,
                    'peerName': partner_name,
                    'timestamp': timestamp
                })
                
                await manager.clients[partner_id]['ws'].send_json({
                    'type': 'peer_connected',
                    'peerId': client_id,
                    'peerName': client_name,
                    'timestamp': timestamp
                })
                
                logger.info(f"🎉 Emparejados: {client_name} ↔ {partner_name}")
            else:
                await ws.send_json({
                    'type': 'waiting_for_peer',
                    'message': 'Esperando a otro usuario...'
                })
        else:
            await ws.send_json({
                'type': 'waiting_for_peer',
                'message': 'Esperando a otro usuario...',
                'waitingCount': len(manager.waiting)
            })
        
        # 4. ESCUCHAR MENSAJES
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get('type', 'unknown')
                    
                    logger.info(f"📩 {client_id} -> {msg_type}")
                    
                    # Verificar cliente
                    if client_id not in manager.clients:
                        continue
                    
                    partner = manager.clients[client_id]['partner']
                    
                    # Reenviar mensajes WebRTC
                    if msg_type == 'webrtc_signal' and partner:
                        data['senderName'] = manager.clients[client_id]['username']
                        await manager.clients[partner]['ws'].send_json(data)
                        logger.info(f"🔀 Señal reenviada: {client_id} -> {partner}")
                    
                    # Llamada rechazada
                    elif msg_type == 'call_decline' and partner:
                        await manager.clients[partner]['ws'].send_json({
                            'type': 'call_decline',
                            'senderName': manager.clients[client_id]['username'],
                            'timestamp': datetime.now().isoformat()
                        })
                    
                    # Fin de llamada
                    elif msg_type == 'call_end' and partner:
                        await manager.clients[partner]['ws'].send_json({
                            'type': 'call_end',
                            'timestamp': datetime.now().isoformat()
                        })
                    
                except json.JSONDecodeError:
                    logger.error(f"❌ JSON inválido de {client_id}")
            
            elif msg.type == web.WSMsgType.ERROR:
                logger.error(f"💥 Error WS: {ws.exception()}")
                
    except Exception as e:
        logger.error(f"💥 Error: {e}")
    finally:
        # Limpiar
        if client_id in manager.clients:
            partner = manager.clients[client_id]['partner']
            if partner and partner in manager.clients:
                await manager.clients[partner]['ws'].send_json({
                    'type': 'peer_disconnected',
                    'message': 'Tu compañero se ha desconectado'
                })
        
        manager.remove_client(client_id)
    
    return ws

# ============================================
# SERVIDOR HTTP
# ============================================
async def handle_index(request):
    return web.FileResponse('./index.html')

async def handle_status(request):
    return web.json_response({
        'status': 'online',
        'timestamp': datetime.now().isoformat(),
        'clients': len(manager.clients),
        'waiting': len(manager.waiting)
    })

async def start_server():
    print("=" * 60)
    print("🚀 SERVIDOR WEBRTC - TELEGRAM STYLE")
    print("=" * 60)
    
    # Obtener puerto de Render o usar 3000 por defecto
    port = int(os.environ.get("PORT", 3000))
    host = "0.0.0.0"
    
    print(f"🌐 Servidor iniciado en: http://{host}:{port}")
    print("=" * 60)
    
    # Configurar app
    app = web.Application()
    
    # Rutas HTTP
    app.router.add_get('/', handle_index)
    app.router.add_get('/status', handle_status)
    app.router.add_get('/ws', websocket_handler)
    app.router.add_static('/', './')
    
    # Iniciar servidor
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    
    print("✅ Servidor listo")
    print("👥 Esperando conexiones...")
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