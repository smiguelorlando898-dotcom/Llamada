FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# 1. Instalar dependencias base
RUN apt-get update && apt-get install -y \
    curl \
    coturn \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# 2. Instalar Node.js 18.x LTS (¡CRÍTICO!)
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# 3. Preparar directorios para TURN
RUN mkdir -p /var/lib/turn /var/log/turn \
    && chown -R turnserver:turnserver /var/lib/turn /var/log/turn

# 4. Copiar configuración TURN
COPY turnserver.conf /etc/turnserver.conf

# 5. Copiar aplicación Node.js
WORKDIR /app
COPY server.js /app/server.js
COPY package.json /app/package.json

# 6. Instalar dependencias Node.js
RUN npm install

# 7. Copiar y hacer ejecutable el entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 8. Exponer puertos (HTTP + TURN + Relay UDP)
EXPOSE 80
EXPOSE 3478/tcp
EXPOSE 3478/udp
EXPOSE 49152-65535/udp

# 9. Ejecutar con entrypoint
ENTRYPOINT ["/entrypoint.sh"]