#!/usr/bin/env python3
"""
SENATI - Sistema de Asistencia Facial
Script de inicio rápido
"""

import os
import sys
import subprocess
import webbrowser
import time

def main():
    print("=" * 60)
    print("  SENATI - Sistema de Asistencia Facial")
    print("  Iniciando servidor web...")
    print("=" * 60)

    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web')

    if not os.path.exists(web_dir):
        print("Error: No se encuentra la carpeta 'web'")
        print("Asegurate de estar en la carpeta correcta.")
        input("Presiona Enter para salir...")
        return

    os.chdir(web_dir)

    print("\nIniciando servidor en http://localhost:5000")
    print("Abriendo navegador...")

    # Abrir navegador después de 2 segundos
    def open_browser():
        time.sleep(2)
        webbrowser.open('http://localhost:5000')

    import threading
    threading.Thread(target=open_browser, daemon=True).start()

    # Iniciar servidor Flask
    try:
        subprocess.run([sys.executable, 'app.py'], check=True)
    except KeyboardInterrupt:
        print("\n\nServidor detenido.")
    except Exception as e:
        print(f"\nError: {e}")
        input("Presiona Enter para salir...")

if __name__ == '__main__':
    main()
