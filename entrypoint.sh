#!/bin/bash

echo "========================================="
echo "   INICIANDO TURN SERVER + WEBHOOK API   "
echo "========================================="

# Configurar external-ip dinámicamente
if [ ! -z "$RENDER_EXTERNAL_IP" ]; then
    echo "✓ Usando external-ip de variable de entorno: $RENDER_EXTERNAL_IP"
    sed -i "s/external-ip=.*/external-ip=$RENDER_EXTERNAL_IP/g" /etc/turnserver.conf
else
    echo "⚠  Usando external-ip estático del archivo de configuración"
fi

# Verificar configuración TURN
echo "✓ Configuración TURN cargada:"
grep -E "external-ip|realm|listening-port" /etc/turnserver.conf

# Iniciar TURN server en segundo plano
echo "🚀 Iniciando TURN server (Coturn)..."
turnserver -c /etc/turnserver.conf --no-cli &
TURN_PID=$!
sleep 2

# Verificar si TURN está corriendo
if ps -p $TURN_PID > /dev/null; then
    echo "✅ TURN server iniciado (PID: $TURN_PID)"
else
    echo "❌ ERROR: TURN server no pudo iniciar"
    exit 1
fi

# Iniciar servidor web Node.js
echo "🚀 Iniciando servidor web Express..."
node server.js &
NODE_PID=$!
sleep 2

# Verificar si Node.js está corriendo
if ps -p $NODE_PID > /dev/null; then
    echo "✅ Servidor Express iniciado (PID: $NODE_PID)"
    echo "📡 HTTP API disponible en: http://localhost:80"
    echo "🎯 TURN server disponible en: turn:74.220.49.1:3478"
    echo "👤 Usuario TURN: admin"
else
    echo "❌ ERROR: Servidor Express no pudo iniciar"
    kill $TURN_PID 2>/dev/null
    exit 1
fi

echo "========================================="
echo "   SERVICIOS INICIADOS CORRECTAMENTE     "
echo "========================================="

# Función para limpiar al salir
cleanup() {
    echo "🛑 Recibida señal de terminación..."
    echo "⚠  Deteniendo servicios..."
    kill $TURN_PID $NODE_PID 2>/dev/null
    wait $TURN_PID $NODE_PID 2>/dev/null
    echo "✅ Servicios detenidos. ¡Hasta pronto!"
    exit 0
}

# Capturar señales de terminación
trap cleanup SIGINT SIGTERM

# Mantener el contenedor vivo
echo "📊 Monitoreando servicios..."
while true; do
    # Verificar que ambos procesos sigan vivos
    if ! ps -p $TURN_PID > /dev/null; then
        echo "❌ TURN server se detuvo inesperadamente"
        kill $NODE_PID 2>/dev/null
        exit 1
    fi
    
    if ! ps -p $NODE_PID > /dev/null; then
        echo "❌ Servidor Express se detuvo inesperadamente"
        kill $TURN_PID 2>/dev/null
        exit 1
    fi
    
    # Esperar 30 segundos y verificar de nuevo
    sleep 30
done