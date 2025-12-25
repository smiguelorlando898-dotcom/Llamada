#!/usr/bin/env python3
"""
BASE DE DATOS SQLite PARA USUARIOS Y LLAMADAS
"""

import sqlite3
import bcrypt
import json
from datetime import datetime
import os
from pathlib import Path

class DatabaseManager:
    def __init__(self, db_path='webrtc_app.db'):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Inicializa la base de datos y crea las tablas si no existen"""
        os.makedirs(os.path.dirname(self.db_path) or '.', exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Tabla de usuarios
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    avatar_url TEXT,
                    avatar_data BLOB,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_online BOOLEAN DEFAULT 0
                )
            ''')
            
            # Tabla de logs de llamadas
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS calls_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    caller_id INTEGER NOT NULL,
                    callee_id INTEGER NOT NULL,
                    start_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                    end_time DATETIME,
                    duration INTEGER,
                    call_type TEXT,
                    FOREIGN KEY (caller_id) REFERENCES users (id),
                    FOREIGN KEY (callee_id) REFERENCES users (id)
                )
            ''')
            
            # Índices para mejor performance
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_online ON users(is_online)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_calls_user ON calls_log(caller_id, callee_id)')
            
            conn.commit()
    
    def create_user(self, username, password, avatar_data=None):
        """Crea un nuevo usuario en la base de datos"""
        try:
            # Hash de la contraseña
            password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            
            # Generar URL única para el avatar
            avatar_url = f"/avatars/{username}_{int(datetime.now().timestamp())}.png" if avatar_data else None
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO users (username, password_hash, avatar_url, avatar_data, created_at)
                    VALUES (?, ?, ?, ?, ?)
                ''', (username, password_hash, avatar_url, avatar_data, datetime.now().isoformat()))
                conn.commit()
                
                user_id = cursor.lastrowid
                
                # Guardar avatar en sistema de archivos si existe
                if avatar_data:
                    self.save_avatar_to_file(avatar_url, avatar_data)
                
                return {
                    'id': user_id,
                    'username': username,
                    'avatar_url': avatar_url
                }
        except sqlite3.IntegrityError:
            return None  # Usuario ya existe
    
    def save_avatar_to_file(self, avatar_url, avatar_data):
        """Guarda el avatar en el sistema de archivos"""
        try:
            # Extraer el path del avatar_url
            avatar_path = avatar_url.lstrip('/')
            full_path = os.path.join('static', avatar_path)
            
            # Crear directorios si no existen
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            
            # Guardar el archivo
            with open(full_path, 'wb') as f:
                if isinstance(avatar_data, bytes):
                    f.write(avatar_data)
                else:
                    # Si es base64, decodificar
                    import base64
                    f.write(base64.b64decode(avatar_data.split(',')[1]))
            
            return True
        except Exception as e:
            print(f"Error guardando avatar: {e}")
            return False
    
    def verify_user(self, username, password):
        """Verifica las credenciales del usuario"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, username, password_hash, avatar_url
                FROM users WHERE username = ?
            ''', (username,))
            
            result = cursor.fetchone()
            
            if result and bcrypt.checkpw(password.encode('utf-8'), result[2].encode('utf-8')):
                return {
                    'id': result[0],
                    'username': result[1],
                    'avatar_url': result[3]
                }
            return None
    
    def update_user_status(self, user_id, is_online):
        """Actualiza el estado de conexión del usuario"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users 
                SET is_online = ?, last_seen = ?
                WHERE id = ?
            ''', (1 if is_online else 0, datetime.now().isoformat(), user_id))
            conn.commit()
    
    def get_user(self, user_id):
        """Obtiene información de un usuario"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
            user = cursor.fetchone()
            return dict(user) if user else None
    
    def get_all_users(self, exclude_user_id=None):
        """Obtiene todos los usuarios registrados"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            if exclude_user_id:
                cursor.execute('''
                    SELECT id, username, avatar_url, is_online, last_seen
                    FROM users 
                    WHERE id != ?
                    ORDER BY is_online DESC, username ASC
                ''', (exclude_user_id,))
            else:
                cursor.execute('''
                    SELECT id, username, avatar_url, is_online, last_seen
                    FROM users 
                    ORDER BY is_online DESC, username ASC
                ''')
            
            users = cursor.fetchall()
            return [dict(user) for user in users]
    
    def update_user_profile(self, user_id, username=None, password=None, avatar_data=None):
        """Actualiza el perfil del usuario"""
        updates = []
        params = []
        
        if username:
            updates.append("username = ?")
            params.append(username)
        
        if password:
            password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            updates.append("password_hash = ?")
            params.append(password_hash)
        
        if avatar_data:
            # Generar nueva URL para el avatar
            avatar_url = f"/avatars/user_{user_id}_{int(datetime.now().timestamp())}.png"
            updates.append("avatar_url = ?")
            updates.append("avatar_data = ?")
            params.append(avatar_url)
            params.append(avatar_data)
            
            # Guardar en archivo
            self.save_avatar_to_file(avatar_url, avatar_data)
        
        if updates:
            params.append(user_id)
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                query = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"
                cursor.execute(query, params)
                conn.commit()
                
                # Obtener usuario actualizado
                return self.get_user(user_id)
        
        return None
    
    def log_call(self, caller_id, callee_id, call_type, duration):
        """Registra una llamada en el historial"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO calls_log (caller_id, callee_id, call_type, duration, end_time)
                VALUES (?, ?, ?, ?, ?)
            ''', (caller_id, callee_id, call_type, duration, datetime.now().isoformat()))
            conn.commit()
    
    def get_call_history(self, user_id, limit=50):
        """Obtiene el historial de llamadas de un usuario"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
                SELECT cl.*, 
                       u1.username as caller_name,
                       u2.username as callee_name
                FROM calls_log cl
                LEFT JOIN users u1 ON cl.caller_id = u1.id
                LEFT JOIN users u2 ON cl.callee_id = u2.id
                WHERE cl.caller_id = ? OR cl.callee_id = ?
                ORDER BY cl.end_time DESC
                LIMIT ?
            ''', (user_id, user_id, limit))
            
            calls = cursor.fetchall()
            return [dict(call) for call in calls]
    
    def search_users(self, query, exclude_user_id=None):
        """Busca usuarios por nombre de usuario"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            search_query = f"%{query}%"
            
            if exclude_user_id:
                cursor.execute('''
                    SELECT id, username, avatar_url, is_online
                    FROM users 
                    WHERE username LIKE ? AND id != ?
                    ORDER BY is_online DESC, username ASC
                ''', (search_query, exclude_user_id))
            else:
                cursor.execute('''
                    SELECT id, username, avatar_url, is_online
                    FROM users 
                    WHERE username LIKE ?
                    ORDER BY is_online DESC, username ASC
                ''', (search_query,))
            
            users = cursor.fetchall()
            return [dict(user) for user in users]

# Instancia global de la base de datos
db_manager = DatabaseManager()