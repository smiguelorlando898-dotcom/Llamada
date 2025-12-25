const express = require('express');
const app = express();
const PORT = 80;

// Middleware
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// Header para CORS (útil si llamas desde navegador)
app.use((req, res, next) => {
    res.header('Access-Control-Allow-Origin', '*');
    res.header('Access-Control-Allow-Headers', 'Origin, X-Requested-With, Content-Type, Accept');
    next();
});

// ====================
// ENDPOINTS PRINCIPALES
// ====================

// 1. Health Check (Render lo usa para saber si está vivo)
app.get('/', (req, res) => {
    res.json({
        status: 'online',
        service: 'TURN Server + Webhook API',
        timestamp: new Date().toISOString(),
        endpoints: {
            health: 'GET /health',
            webhook: 'POST /webhook',
            turnInfo: 'GET /turn-info',
            metrics: 'GET /metrics'
        }
    });
});

// 2. Health Check detallado
app.get('/health', (req, res) => {
    const health = {
        status: 'healthy',
        serverTime: new Date().toISOString(),
        uptime: process.uptime(),
        memory: process.memoryUsage(),
        nodeVersion: process.version,
        turnServer: {
            ip: '74.220.49.1',
            port: 3478,
            protocol: 'UDP/TCP',
            realm: 'acalled'
        }
    };
    res.json(health);
});

// 3. Información del servidor TURN
app.get('/turn-info', (req, res) => {
    res.json({
        turnServer: 'stun:74.220.49.1:3478',
        turnURLs: [
            'turn:74.220.49.1:3478?transport=udp',
            'turn:74.220.49.1:3478?transport=tcp'
        ],
        credentials: {
            username: 'admin',
            password: 'mO*061119',
            note: 'Credenciales estáticas - cambiar en producción'
        },
        iceServers: [
            {
                urls: 'stun:74.220.49.1:3478'
            },
            {
                urls: 'turn:74.220.49.1:3478',
                username: 'admin',
                credential: 'mO*061119'
            }
        ]
    });
});

// 4. Webhook principal (POST)
app.post('/webhook', (req, res) => {
    const event = req.body;
    const timestamp = new Date().toISOString();
    
    console.log(`📨 [${timestamp}] Webhook recibido:`, {
        type: event.type || 'unknown',
        data: event.data || 'no data',
        ip: req.ip
    });
    
    // Respuesta estándar
    res.json({
        received: true,
        timestamp: timestamp,
        eventId: `evt_${Date.now()}`,
        message: 'Webhook procesado correctamente'
    });
});

// 5. Métricas simples
app.get('/metrics', (req, res) => {
    res.json({
        requests: {
            total: req.app.locals.requestCount || 0,
            webhooks: req.app.locals.webhookCount || 0
        },
        server: {
            connections: 0, // Podrías añadir tracking real
            lastRestart: new Date(Date.now() - process.uptime() * 1000).toISOString()
        }
    });
});

// 6. Endpoint para probar TURN (GET simple)
app.get('/test-turn', (req, res) => {
    const config = {
        iceServers: [
            { urls: 'stun:74.220.49.1:3478' },
            {
                urls: 'turn:74.220.49.1:3478',
                username: 'admin',
                credential: 'mO*061119'
            }
        ]
    };
    
    res.json({
        instructions: 'Usa esta configuración en tu cliente WebRTC',
        config: config,
        example: {
            javascript: `const pc = new RTCPeerConnection({
  iceServers: ${JSON.stringify(config.iceServers, null, 2)}
});`
        }
    });
});

// 7. 404 para rutas no encontradas
app.use((req, res) => {
    res.status(404).json({
        error: 'Endpoint no encontrado',
        availableEndpoints: [
            'GET /',
            'GET /health',
            'POST /webhook',
            'GET /turn-info',
            'GET /metrics',
            'GET /test-turn'
        ]
    });
});

// Iniciar servidor
app.listen(PORT, '0.0.0.0', () => {
    console.log(`✅ Servidor Express iniciado en puerto ${PORT}`);
    console.log(`🔗 Health check: http://localhost:${PORT}/health`);
    console.log(`🎯 TURN server: turn:74.220.49.1:3478`);
    console.log(`👤 Usuario: admin, Contraseña: mO*061119`);
});