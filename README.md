# SENATI - Sistema de Asistencia Facial

Sistema profesional de registro de entrada y salida de personal mediante reconocimiento facial.

## Estructura del Proyecto

```
sistema_asistencia/
├── asistencia.db              # Base de datos SQLite
├── fotos_personal/            # Fotos de referencia del personal
├── registrar_personal.py      # Registro de nuevo personal
├── reconocimiento_auto.py     # Reconocimiento facial automático
├── gestion_personal.py        # Gestión de personal (listar, eliminar, etc.)
├── reportes.py                # Reportes y exportación CSV
├── start_server.py            # Inicio rápido del servidor web
├── requirements.txt           # Dependencias
└── web/                       # Aplicación web
    ├── app.py                 # Servidor Flask
    ├── templates/
    │   └── index.html         # Dashboard principal
    ├── static/
    │   ├── css/style.css      # Estilos profesionales
    │   └── js/app.js          # Lógica de la web
    └── backups/               # Backups automáticos
```

## Instalación

1. Instalar dependencias:
   ```bash
   pip install -r requirements.txt
   ```

2. La primera vez que ejecutes, DeepFace descargará automáticamente los modelos pre-entrenados.

## Uso

### 1. Registrar Personal
```bash
python registrar_personal.py
```
- Selecciona tipo: Maestro, Personal o Jefe
- Ingresa datos (nombre, área, cargo, DNI)
- Captura foto con la cámara

### 2. Reconocimiento Automático
```bash
python reconocimiento_auto.py
```
- Cámara siempre activa
- Acércate para registrar entrada/salida automáticamente
- Presiona Q para salir

### 3. Servidor Web (Dashboard)
```bash
python start_server.py
```
O manualmente:
```bash
cd web
python app.py
```
- Abre automáticamente el navegador en http://localhost:5000

## Funcionalidades de la Web

### Dashboard
- Estadísticas en tiempo real (entradas, salidas, dentro, tarde)
- Registros del día con auto-refresh cada 5 segundos
- Alertas automáticas

### Personal
- Grid de tarjetas con foto e información
- Búsqueda en tiempo real
- Filtros por área, tipo y estado

### Asistencias
- Historial completo por fecha
- Filtrado por rango de fechas

### Estadísticas
- Gráfico de asistencia semanal
- Top llegadas tarde
- Horas trabajadas por persona
- Comparativa mensual

### Modo Desarrollador (Admin)
- Palabra clave: `desarrollador` (configurable)
- Contraseña por defecto: `senati2024`
- Acceso a configuración del sistema
- Logs de acceso
- Gestión de días festivos
- Bloqueo por intentos fallidos (3 intentos, 5 minutos)

### Exportar
- Descarga de asistencias a CSV/Excel
- Rango de fechas configurable

## Configuración

Edita la tabla `configuracion` en la base de datos o usa el modo desarrollador:

| Clave | Descripción | Default |
|-------|-------------|---------|
| palabra_clave_admin | Palabra para modo admin | desarrollador |
| contrasena_admin | Contraseña admin | senati2024 |
| horario_maestro | Hora entrada maestros | 07:00 |
| horario_personal | Hora entrada personal | 08:00 |
| horario_jefe | Hora entrada jefes | 08:00 |
| tolerancia_minutos | Minutos de tolerancia | 15 |
| auto_refresh | Segundos entre refrescos | 5 |
| retencion_dias | Días de retención de datos | 30 |
| backup_semanal | Activar backup semanal | 1 |

## Características

- ✅ Reconocimiento facial automático
- ✅ Cámara siempre activa
- ✅ Registro de entrada/salida sin presionar teclas
- ✅ Dashboard web en tiempo real
- ✅ Dark/Light mode
- ✅ Alertas automáticas
- ✅ Exportación a Excel/CSV
- ✅ Modo desarrollador seguro
- ✅ Backup automático semanal
- ✅ Limpieza automática de datos antiguos
- ✅ Días festivos configurables
- ✅ Horarios configurables por tipo de personal
- ✅ Detección de llegadas tarde
- ✅ Responsive (PC, tablet, móvil)

## Notas

- Modelo usado: Facenet
- Detector de rostros: OpenCV
- Umbral de confianza: 0.40
- Asegúrate de tener buena iluminación para mejores resultados
