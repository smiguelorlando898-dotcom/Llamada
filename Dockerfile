FROM ubuntu:20.04

# Instalar Coturn
RUN apt-get update && apt-get install -y coturn && rm -rf /var/lib/apt/lists/*

# Copiar configuración
COPY turnserver.conf /etc/turnserver.conf

# Exponer puerto TURN
EXPOSE 3478

# Ejecutar Coturn con la configuración
CMD ["turnserver", "-c", "/etc/turnserver.conf", "--no-cli"]