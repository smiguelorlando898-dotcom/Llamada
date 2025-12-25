FROM ubuntu:20.04

ENV DEBIAN_FRONTEND=noninteractive

# Instalar Coturn y Node.js
RUN apt-get update && apt-get install -y coturn nodejs npm tzdata && rm -rf /var/lib/apt/lists/*

# Copiar configuración Coturn
COPY turnserver.conf /etc/turnserver.conf

# Copiar servidor Webhook
COPY server.js /app/server.js
WORKDIR /app

# Exponer puertos
EXPOSE 80
EXPOSE 3478/tcp
EXPOSE 3478/udp

# Ejecutar ambos procesos: TURN + Webhook
CMD turnserver -c /etc/turnserver.conf --no-cli & node server.js