const express = require('express');
const http = require('http');
const WebSocket = require('ws');
const sqlite3 = require('sqlite3').verbose();
const bcrypt = require('bcryptjs');
const { v4: uuidv4 } = require('uuid');
const path = require('path');
const fs = require('fs').promises;
const multer = require('multer');
const cors = require('cors');
const sharp = require('sharp');

// Configuración
const PORT = process.env.PORT || 3000;
const app = express();
const server = http.createServer(app);
const wss = new WebSocket.Server({ server });

// Middleware
app.use(cors());
app.use(express.json({ limit: '50mb' }));
app.use(express.static('.'));
app.use('/avatars', express.static('static/avatars'));

// Base de datos
const db = new sqlite3.Database('webrtc.db');

// Inicializar base de datos
function initDatabase() {
    db.serialize(() => {
        // Tabla de usuarios
        db.run(`
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
        `);

        // Tabla de sesiones
        db.run(`
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP DEFAULT (datetime('now', '+1 day')),
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        `);

        // Tabla de llamadas
        db.run(`
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
        `);

        console.log('✅ Base de datos inicializada');
    });
}

// User Manager
class UserManager {
    constructor() {
        this.connectedUsers = new Map(); // user_id -> {ws, username, etc}
        this.heartbeats = new Map();
        this.pendingSignals = new Map();
        this.activeCalls = new Map();
    }

    generateSessionToken(userId) {
        return uuidv4();
    }

    async validateSession(token) {
        return new Promise((resolve, reject) => {
            db.get(
                'SELECT user_id FROM sessions WHERE token = ? AND expires_at > datetime("now")',
                [token],
                (err, row) => {
                    if (err) reject(err);
                    resolve(row ? row.user_id : null);
                }
            );
        });
    }

    async addConnectedUser(userId, ws, userData) {
        const currentTime = new Date().toISOString();

        this.connectedUsers.set(userId, {
            ws,
            username: userData.username,
            avatarUrl: userData.avatar_url,
            status: 'disponible',
            inCallWith: null,
            connectedAt: currentTime,
            lastSeen: currentTime,
            heartbeat: Date.now()
        });

        // Actualizar estado en base de datos
        this.updateUserStatusDB(userId, true);

        this.heartbeats.set(userId, Date.now());
        this.pendingSignals.set(userId, []);

        console.log(`✅ Usuario conectado: ${userData.username} (${userId})`);
        return userData.avatar_url;
    }

    removeConnectedUser(userId) {
        if (this.connectedUsers.has(userId)) {
            const userData = this.connectedUsers.get(userId);
            
            // Finalizar llamada si está en una
            const partner = userData.inCallWith;
            if (partner && this.connectedUsers.has(partner)) {
                const partnerData = this.connectedUsers.get(partner);
                partnerData.inCallWith = null;
                partnerData.status = 'disponible';
            }

            console.log(`🗑️ Usuario desconectado: ${userData.username}`);

            // Actualizar estado en base de datos
            this.updateUserStatusDB(userId, false);

            // Limpiar datos
            this.heartbeats.delete(userId);
            this.pendingSignals.delete(userId);
            this.connectedUsers.delete(userId);

            return true;
        }
        return false;
    }

    updateUserStatusDB(userId, isOnline, status = 'disponible') {
        db.run(
            'UPDATE users SET is_online = ?, status = ?, last_seen = CURRENT_TIMESTAMP WHERE id = ?',
            [isOnline ? 1 : 0, status, userId]
        );
    }

    updateUserStatus(userId, status, inCallWith = null) {
        if (!this.connectedUsers.has(userId)) return false;

        const userData = this.connectedUsers.get(userId);
        userData.status = status;
        userData.inCallWith = inCallWith;
        userData.lastSeen = new Date().toISOString();

        // Actualizar en base de datos también
        db.run('UPDATE users SET status = ? WHERE id = ?', [status, userId]);

        return true;
    }

    updateHeartbeat(userId) {
        if (this.connectedUsers.has(userId)) {
            this.heartbeats.set(userId, Date.now());
            return true;
        }
        return false;
    }

    checkInactiveUsers() {
        const currentTime = Date.now();
        const inactive = [];

        for (const [userId, heartbeat] of this.heartbeats.entries()) {
            if (currentTime - heartbeat > 30000) { // 30 segundos
                inactive.push(userId);
            }
        }

        inactive.forEach(userId => {
            console.warn(`⏰ Usuario inactivo: ${userId}`);
            this.removeConnectedUser(userId);
        });

        return inactive;
    }

    getUserInfo(userId) {
        if (this.connectedUsers.has(userId)) {
            const user = this.connectedUsers.get(userId);
            return {
                id: userId,
                username: user.username,
                status: user.status,
                avatar_url: user.avatarUrl,
                in_call_with: user.inCallWith
            };
        }
        return null;
    }

    getConnectedUsers(excludeUserId = null) {
        const connected = [];
        
        for (const [userId, data] of this.connectedUsers.entries()) {
            if (userId !== excludeUserId) {
                const info = this.getUserInfo(userId);
                if (info) connected.push(info);
            }
        }

        connected.sort((a, b) => a.username.localeCompare(b.username));
        return connected;
    }

    canCallUser(callerId, targetId) {
        if (!this.connectedUsers.has(callerId) || !this.connectedUsers.has(targetId)) {
            return false;
        }

        if (targetId === callerId) {
            return false;
        }

        const callerStatus = this.connectedUsers.get(callerId).status;
        const targetStatus = this.connectedUsers.get(targetId).status;

        if (targetStatus !== 'disponible' || callerStatus !== 'disponible') {
            return false;
        }

        return true;
    }

    initiateCall(callerId, targetId) {
        if (!this.canCallUser(callerId, targetId)) {
            return false;
        }

        this.updateUserStatus(callerId, 'llamando', targetId);
        this.updateUserStatus(targetId, 'recibiendo_llamada', callerId);

        console.log(`📞 Llamada iniciada: ${this.connectedUsers.get(callerId).username} -> ${this.connectedUsers.get(targetId).username}`);
        return true;
    }

    acceptCall(userId) {
        if (!this.connectedUsers.has(userId)) {
            return null;
        }

        const partner = this.connectedUsers.get(userId).inCallWith;
        if (!partner || !this.connectedUsers.has(partner)) {
            return null;
        }

        if (this.connectedUsers.get(partner).status !== 'llamando') {
            return null;
        }

        this.updateUserStatus(userId, 'en_llamada', partner);
        this.updateUserStatus(partner, 'en_llamada', userId);

        const callId = `${Math.min(userId, partner)}_${Math.max(userId, partner)}`;
        this.activeCalls.set(callId, {
            users: [userId, partner],
            startTime: new Date().toISOString()
        });

        return partner;
    }

    endCall(userId) {
        if (!this.connectedUsers.has(userId)) {
            return null;
        }

        const partner = this.connectedUsers.get(userId).inCallWith;

        if (partner && this.connectedUsers.has(partner)) {
            this.updateUserStatus(userId, 'disponible', null);
            this.updateUserStatus(partner, 'disponible', null);

            // Registrar en base de datos si la llamada fue aceptada
            if (this.connectedUsers.get(userId).status === 'en_llamada') {
                const callId = `${Math.min(userId, partner)}_${Math.max(userId, partner)}`;
                if (this.activeCalls.has(callId)) {
                    const callData = this.activeCalls.get(callId);
                    const startTime = new Date(callData.startTime);
                    const duration = Math.floor((Date.now() - startTime.getTime()) / 1000);

                    // Registrar llamada en base de datos
                    db.run(
                        'INSERT INTO calls (caller_id, callee_id, call_type, start_time, end_time, duration) VALUES (?, ?, ?, datetime("now", "-? seconds"), datetime("now"), ?)',
                        [userId, partner, 'audio', duration, duration]
                    );

                    this.activeCalls.delete(callId);
                }
            }
        } else {
            if (this.connectedUsers.get(userId).status === 'en_llamada') {
                this.updateUserStatus(userId, 'disponible', null);
            }
        }

        return partner;
    }

    declineCall(userId) {
        if (!this.connectedUsers.has(userId)) {
            return null;
        }

        const partner = this.connectedUsers.get(userId).inCallWith;
        if (partner && this.connectedUsers.has(partner)) {
            this.updateUserStatus(userId, 'disponible', null);
            this.updateUserStatus(partner, 'disponible', null);
        }

        return partner;
    }

    storeSignal(targetId, signalData) {
        if (!this.pendingSignals.has(targetId)) {
            this.pendingSignals.set(targetId, []);
        }
        this.pendingSignals.get(targetId).push(signalData);
    }

    getPendingSignals(userId) {
        if (this.pendingSignals.has(userId)) {
            const signals = [...this.pendingSignals.get(userId)];
            this.pendingSignals.set(userId, []);
            return signals;
        }
        return [];
    }
}

// Database Helper Functions
async function getUserFromDB(userId) {
    return new Promise((resolve, reject) => {
        db.get('SELECT * FROM users WHERE id = ?', [userId], (err, row) => {
            if (err) reject(err);
            resolve(row ? {
                id: row.id,
                username: row.username,
                avatar_url: row.avatar_url,
                is_online: Boolean(row.is_online),
                status: row.status
            } : null);
        });
    });
}

async function createUserDB(username, password, avatarData = null) {
    const userId = uuidv4();
    const passwordHash = await bcrypt.hash(password, 10);
    let avatarUrl = null;

    if (avatarData && avatarData.startsWith('data:image')) {
        // Guardar avatar como archivo
        const avatarDir = path.join('static', 'avatars');
        await fs.mkdir(avatarDir, { recursive: true });

        const header = avatarData.split(',')[0];
        const data = avatarData.split(',')[1];
        
        let ext = 'png';
        if (header.includes('jpeg') || header.includes('jpg')) ext = 'jpg';
        
        const filename = `${userId}.${ext}`;
        const filepath = path.join(avatarDir, filename);
        
        // Decodificar base64 y guardar
        const buffer = Buffer.from(data, 'base64');
        await sharp(buffer)
            .resize(200, 200, { fit: 'cover' })
            .toFile(filepath);
        
        avatarUrl = `/avatars/${filename}`;
    }

    return new Promise((resolve, reject) => {
        db.run(
            'INSERT INTO users (id, username, password_hash, avatar_url, is_online) VALUES (?, ?, ?, ?, 0)',
            [userId, username, passwordHash, avatarUrl],
            function(err) {
                if (err) {
                    if (err.message.includes('UNIQUE constraint failed')) {
                        resolve(null); // Usuario ya existe
                    } else {
                        reject(err);
                    }
                } else {
                    resolve({
                        id: userId,
                        username,
                        avatar_url: avatarUrl,
                        is_online: false,
                        status: 'disponible'
                    });
                }
            }
        );
    });
}

async function verifyUserDB(username, password) {
    return new Promise((resolve, reject) => {
        db.get('SELECT * FROM users WHERE username = ?', [username], async (err, row) => {
            if (err) {
                reject(err);
                return;
            }

            if (!row) {
                resolve(null);
                return;
            }

            const isValid = await bcrypt.compare(password, row.password_hash);
            if (!isValid) {
                resolve(null);
                return;
            }

            resolve({
                id: row.id,
                username: row.username,
                avatar_url: row.avatar_url,
                is_online: Boolean(row.is_online),
                status: row.status
            });
        });
    });
}

async function getAllUsersDB(excludeUserId = null) {
    return new Promise((resolve, reject) => {
        let query = 'SELECT * FROM users';
        const params = [];

        if (excludeUserId) {
            query += ' WHERE id != ?';
            params.push(excludeUserId);
        }

        query += ' ORDER BY username';

        db.all(query, params, (err, rows) => {
            if (err) {
                reject(err);
                return;
            }

            const users = rows.map(row => ({
                id: row.id,
                username: row.username,
                avatar_url: row.avatar_url,
                is_connected: Boolean(row.is_online),
                status: row.status,
                avatar_color: `#${Math.floor(Math.random() * 16777215).toString(16).padStart(6, '0')}`
            }));

            resolve(users);
        });
    });
}

async function updateUserProfileDB(userId, username = null, password = null, avatarData = null) {
    return new Promise(async (resolve, reject) => {
        // Obtener usuario actual
        const currentUser = await getUserFromDB(userId);
        if (!currentUser) {
            resolve(null);
            return;
        }

        let avatarUrl = currentUser.avatar_url;
        
        // Procesar nuevo avatar si se proporciona
        if (avatarData && avatarData.startsWith('data:image')) {
            // Eliminar avatar anterior si existe
            if (avatarUrl && avatarUrl.startsWith('/avatars/')) {
                try {
                    const oldPath = path.join('static', avatarUrl);
                    await fs.unlink(oldPath);
                } catch (err) {
                    console.warn('No se pudo eliminar avatar anterior:', err);
                }
            }

            // Guardar nuevo avatar
            const avatarDir = path.join('static', 'avatars');
            await fs.mkdir(avatarDir, { recursive: true });

            const header = avatarData.split(',')[0];
            const data = avatarData.split(',')[1];
            
            let ext = 'png';
            if (header.includes('jpeg') || header.includes('jpg')) ext = 'jpg';
            
            const filename = `${userId}.${ext}`;
            const filepath = path.join(avatarDir, filename);
            
            const buffer = Buffer.from(data, 'base64');
            await sharp(buffer)
                .resize(200, 200, { fit: 'cover' })
                .toFile(filepath);
            
            avatarUrl = `/avatars/${filename}`;
        }

        // Preparar actualización
        const updates = [];
        const params = [];

        if (username && username !== currentUser.username) {
            updates.push('username = ?');
            params.push(username);
        }

        if (password) {
            const passwordHash = await bcrypt.hash(password, 10);
            updates.push('password_hash = ?');
            params.push(passwordHash);
        }

        if (avatarUrl !== currentUser.avatar_url) {
            updates.push('avatar_url = ?');
            params.push(avatarUrl);
        }

        if (updates.length === 0) {
            resolve({ ...currentUser, avatar_url: avatarUrl });
            return;
        }

        params.push(userId);
        const query = `UPDATE users SET ${updates.join(', ')} WHERE id = ?`;

        db.run(query, params, function(err) {
            if (err) {
                if (err.message.includes('UNIQUE constraint failed')) {
                    resolve(null); // Nombre de usuario ya existe
                } else {
                    reject(err);
                }
            } else {
                resolve({
                    id: userId,
                    username: username || currentUser.username,
                    avatar_url: avatarUrl,
                    is_online: currentUser.is_online,
                    status: currentUser.status
                });
            }
        });
    });
}

async function createSessionDB(userId) {
    const token = uuidv4();
    return new Promise((resolve, reject) => {
        db.run(
            'INSERT INTO sessions (token, user_id) VALUES (?, ?)',
            [token, userId],
            function(err) {
                if (err) reject(err);
                else resolve(token);
            }
        );
    });
}

// Inicializar User Manager
const userManager = new UserManager();

// Broadcast user list a todos los conectados
async function broadcastUserList() {
    const connectedUsers = Array.from(userManager.connectedUsers.keys());
    
    for (const userId of connectedUsers) {
        const userData = userManager.connectedUsers.get(userId);
        if (!userData || !userData.ws || userData.ws.readyState !== WebSocket.OPEN) continue;

        try {
            const allUsers = await getAllUsersDB(userId);
            
            // Marcar usuarios conectados
            const connectedIds = new Set(userManager.connectedUsers.keys());
            allUsers.forEach(user => {
                user.is_connected = connectedIds.has(user.id);
                if (user.is_connected) {
                    const connectedUser = userManager.getUserInfo(user.id);
                    user.status = connectedUser ? connectedUser.status : 'disponible';
                } else {
                    user.status = 'desconectado';
                }
            });

            userData.ws.send(JSON.stringify({
                type: 'user_list',
                users: allUsers
            }));
        } catch (err) {
            console.error(`Error enviando lista a ${userId}:`, err);
            userManager.removeConnectedUser(userId);
        }
    }
}

// WebSocket Handler
wss.on('connection', async (ws, req) => {
    // Obtener token de la URL
    const url = new URL(req.url, `http://${req.headers.host}`);
    const token = url.searchParams.get('token');
    
    if (!token) {
        ws.close();
        return;
    }

    // Validar sesión
    const userId = await userManager.validateSession(token);
    if (!userId) {
        ws.close();
        return;
    }

    // Obtener datos del usuario
    const userData = await getUserFromDB(userId);
    if (!userData) {
        ws.close();
        return;
    }

    console.log(`🔗 WebSocket conectado: ${userData.username} (${userId})`);

    try {
        // Agregar usuario conectado
        await userManager.addConnectedUser(userId, ws, userData);

        // Enviar mensaje de registro
        ws.send(JSON.stringify({
            type: 'registered',
            userId: userId,
            username: userData.username,
            avatar_url: userData.avatar_url,
            token: token,
            onlineUsers: userManager.getConnectedUsers(userId),
            avatarColor: `#${Math.floor(Math.random() * 16777215).toString(16).padStart(6, '0')}`
        }));

        // Enviar señales pendientes
        const pending = userManager.getPendingSignals(userId);
        pending.forEach(signal => {
            ws.send(JSON.stringify(signal));
        });

        // Broadcast nueva lista
        await broadcastUserList();

        // Manejar mensajes
        ws.on('message', async (message) => {
            try {
                const data = JSON.parse(message);
                const msgType = data.type;
                
                console.log(`📩 ${msgType} de ${userId}`);

                switch (msgType) {
                    case 'heartbeat':
                        userManager.updateHeartbeat(userId);
                        ws.send(JSON.stringify({ type: 'heartbeat_ack' }));
                        break;

                    case 'get_users':
                        const allUsers = await getAllUsersDB(userId);
                        const connectedIds = new Set(userManager.connectedUsers.keys());
                        
                        allUsers.forEach(user => {
                            user.is_connected = connectedIds.has(user.id);
                            if (user.is_connected) {
                                const connectedUser = userManager.getUserInfo(user.id);
                                user.status = connectedUser ? connectedUser.status : 'disponible';
                            } else {
                                user.status = 'desconectado';
                            }
                        });

                        ws.send(JSON.stringify({
                            type: 'user_list',
                            users: allUsers
                        }));
                        break;

                    case 'call_request':
                        const targetId = data.targetId;
                        if (userManager.initiateCall(userId, targetId)) {
                            const targetWs = userManager.connectedUsers.get(targetId).ws;
                            targetWs.send(JSON.stringify({
                                type: 'incoming_call',
                                callerId: userId,
                                callerName: userManager.connectedUsers.get(userId).username,
                                callerAvatar: userManager.connectedUsers.get(userId).avatarUrl
                            }));
                            await broadcastUserList();
                        } else {
                            ws.send(JSON.stringify({
                                type: 'call_error',
                                message: 'Usuario no disponible'
                            }));
                        }
                        break;

                    case 'call_accept':
                        const partner = userManager.acceptCall(userId);
                        if (partner) {
                            const partnerWs = userManager.connectedUsers.get(partner).ws;
                            partnerWs.send(JSON.stringify({
                                type: 'call_accepted',
                                calleeId: userId,
                                calleeName: userManager.connectedUsers.get(userId).username
                            }));
                            await broadcastUserList();
                        }
                        break;

                    case 'call_decline':
                        const declinedPartner = userManager.declineCall(userId);
                        if (declinedPartner) {
                            const partnerWs = userManager.connectedUsers.get(declinedPartner).ws;
                            partnerWs.send(JSON.stringify({ type: 'call_declined' }));
                            await broadcastUserList();
                        }
                        break;

                    case 'call_end':
                        const endedPartner = userManager.endCall(userId);
                        if (endedPartner) {
                            const partnerWs = userManager.connectedUsers.get(endedPartner).ws;
                            partnerWs.send(JSON.stringify({ type: 'call_ended' }));
                            await broadcastUserList();
                        }
                        break;

                    case 'webrtc_signal':
                        const signalTargetId = data.targetId;
                        const signal = data.signal;
                        
                        if (userManager.connectedUsers.has(signalTargetId)) {
                            const targetWs = userManager.connectedUsers.get(signalTargetId).ws;
                            try {
                                targetWs.send(JSON.stringify({
                                    type: 'webrtc_signal',
                                    signal: signal,
                                    senderId: userId
                                }));
                            } catch (err) {
                                // Almacenar señal pendiente
                                userManager.storeSignal(signalTargetId, {
                                    type: 'webrtc_signal',
                                    signal: signal,
                                    senderId: userId
                                });
                            }
                        }
                        break;

                    case 'profile_updated':
                        await broadcastUserList();
                        break;
                }
            } catch (err) {
                console.error(`❌ Error procesando mensaje de ${userId}:`, err);
                ws.send(JSON.stringify({
                    type: 'error',
                    message: 'Error procesando solicitud'
                }));
            }
        });

        ws.on('close', async () => {
            console.log(`🔌 WebSocket desconectado: ${userId}`);
            userManager.removeConnectedUser(userId);
            await broadcastUserList();
        });

        ws.on('error', (err) => {
            console.error(`💥 Error en WebSocket ${userId}:`, err);
        });

    } catch (err) {
        console.error(`💥 Error inicializando conexión de ${userId}:`, err);
        ws.close();
    }
});

// Cleanup de usuarios inactivos
setInterval(() => {
    const inactive = userManager.checkInactiveUsers();
    if (inactive.length > 0) {
        console.log(`🧹 Usuarios inactivos limpiados: ${inactive.length}`);
        broadcastUserList();
    }
}, 60000); // Cada minuto

// ========== HTTP ROUTES ==========

// Página de login
app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'login.html'));
});

// Página principal
app.get('/index', (req, res) => {
    res.sendFile(path.join(__dirname, 'index.html'));
});

// Registro de usuario
app.post('/api/register', async (req, res) => {
    try {
        const { username, password, avatar } = req.body;

        if (!username || !password) {
            return res.status(400).json({ 
                success: false, 
                error: 'Faltan campos requeridos' 
            });
        }

        if (username.length < 3) {
            return res.status(400).json({ 
                success: false, 
                error: 'El nombre debe tener al menos 3 caracteres' 
            });
        }

        if (password.length < 6) {
            return res.status(400).json({ 
                success: false, 
                error: 'La contraseña debe tener al menos 6 caracteres' 
            });
        }

        const user = await createUserDB(username, password, avatar);
        if (!user) {
            return res.status(400).json({ 
                success: false, 
                error: 'Usuario ya existe' 
            });
        }

        const token = await createSessionDB(user.id);
        
        res.json({
            success: true,
            user: user,
            token: token
        });
    } catch (err) {
        console.error('💥 Error en registro:', err);
        res.status(500).json({ 
            success: false, 
            error: 'Error interno del servidor' 
        });
    }
});

// Login de usuario
app.post('/api/login', async (req, res) => {
    try {
        const { username, password } = req.body;

        if (!username || !password) {
            return res.status(400).json({ 
                success: false, 
                error: 'Faltan campos requeridos' 
            });
        }

        const user = await verifyUserDB(username, password);
        if (!user) {
            return res.status(401).json({ 
                success: false, 
                error: 'Credenciales inválidas' 
            });
        }

        const token = await createSessionDB(user.id);
        
        res.json({
            success: true,
            user: user,
            token: token
        });
    } catch (err) {
        console.error('💥 Error en login:', err);
        res.status(500).json({ 
            success: false, 
            error: 'Error interno del servidor' 
        });
    }
});

// Actualizar perfil
app.post('/api/update_profile', async (req, res) => {
    try {
        const { token, username, password, avatar } = req.body;
        
        if (!token) {
            return res.status(401).json({ 
                success: false, 
                error: 'Token de sesión requerido' 
            });
        }

        const userId = await userManager.validateSession(token);
        if (!userId) {
            return res.status(401).json({ 
                success: false, 
                error: 'Sesión inválida' 
            });
        }

        // Validar username si se va a cambiar
        if (username && username.length < 3) {
            return res.status(400).json({ 
                success: false, 
                error: 'El nombre debe tener al menos 3 caracteres' 
            });
        }

        // Validar password si se va a cambiar
        if (password && password.length < 6) {
            return res.status(400).json({ 
                success: false, 
                error: 'La nueva contraseña debe tener al menos 6 caracteres' 
            });
        }

        const updatedUser = await updateUserProfileDB(userId, username, password, avatar);
        if (!updatedUser) {
            return res.status(400).json({ 
                success: false, 
                error: 'Error actualizando perfil. ¿Nombre ya existe?' 
            });
        }

        // Actualizar en usuarios conectados si está online
        if (userManager.connectedUsers.has(userId)) {
            const userData = userManager.connectedUsers.get(userId);
            userData.username = updatedUser.username;
            userData.avatarUrl = updatedUser.avatar_url;
        }

        res.json({
            success: true,
            user: {
                id: userId,
                username: updatedUser.username,
                avatar_url: updatedUser.avatar_url
            }
        });
    } catch (err) {
        console.error('💥 Error actualizando perfil:', err);
        res.status(500).json({ 
            success: false, 
            error: 'Error interno del servidor' 
        });
    }
});

// Servir archivos estáticos
app.get('/avatars/:filename', (req, res) => {
    const filename = req.params.filename;
    const filepath = path.join(__dirname, 'static', 'avatars', filename);
    
    res.sendFile(filepath, (err) => {
        if (err) {
            res.status(404).send('Avatar no encontrado');
        }
    });
});

// Ruta por defecto para archivos estáticos
app.get('*', (req, res) => {
    const filepath = path.join(__dirname, req.path);
    if (fs.existsSync(filepath) && !filepath.includes('..')) {
        res.sendFile(filepath);
    } else {
        res.status(404).send('Archivo no encontrado');
    }
});

// Iniciar servidor
async function startServer() {
    // Crear directorios necesarios
    await fs.mkdir(path.join('static', 'avatars'), { recursive: true });
    
    // Inicializar base de datos
    initDatabase();
    
    server.listen(PORT, () => {
        console.log('='.repeat(50));
        console.log(`🚀 Servidor WebRTC Node.js iniciado correctamente!`);
        console.log(`📡 WebSocket: ws://localhost:${PORT}/ws?token=TOKEN`);
        console.log(`🌐 HTTP: http://localhost:${PORT}/`);
        console.log(`💾 Base de datos: webrtc.db`);
        console.log('='.repeat(50));
    });
}

// Manejar cierre limpio
process.on('SIGINT', () => {
    console.log('\n👋 Servidor detenido por el usuario');
    db.close();
    process.exit(0);
});

// Iniciar el servidor
startServer().catch(console.error);