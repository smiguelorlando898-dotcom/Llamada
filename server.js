const express = require('express');
const app = express();

app.use(express.json());

// Endpoint de prueba para Render
app.get('/', (req, res) => {
  res.send('Webhook activo, servidor TURN corriendo');
});

// Endpoint para recibir eventos
app.post('/webhook', (req, res) => {
  console.log('Evento recibido:', req.body);
  res.sendStatus(200);
});

app.listen(80, () => {
  console.log('Webhook escuchando en puerto 80');
});