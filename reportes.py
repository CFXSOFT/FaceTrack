import sqlite3
from datetime import datetime, timedelta
import csv
import os

DB_PATH = "asistencia.db"

def conectar_db():
    return sqlite3.connect(DB_PATH)

def ver_personal():
    print("\n" + "=" * 70)
    print("  PERSONAL REGISTRADO - SENATI")
    print("=" * 70)

    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, dni, nombre, tipo_personal, area, cargo, activo FROM personal ORDER BY tipo_personal, nombre")

    rows = cursor.fetchall()
    if not rows:
        print("No hay personal registrado.")
        return

    for row in rows:
        estado = "Activo" if row[6] else "Inactivo"
        dni = row[1] if row[1] else "N/A"
        print(f"ID: {row[0]} | {dni} | {row[2]} | {row[3].upper()} | {row[4] or 'N/A'} | {row[5] or 'N/A'} | {estado}")

    conn.close()

def ver_asistencias_hoy():
    print("\n" + "=" * 70)
    print("  ASISTENCIAS DE HOY - SENATI")
    print("=" * 70)

    conn = conectar_db()
    cursor = conn.cursor()

    hoy = datetime.now().strftime("%Y-%m-%d")
    sql = "SELECT r.id, p.nombre, p.tipo_personal, r.tipo, r.fecha_hora, r.confianza FROM registros_asistencia r JOIN personal p ON r.personal_id = p.id WHERE date(r.fecha_hora) = ? ORDER BY r.fecha_hora DESC"
    cursor.execute(sql, (hoy,))

    rows = cursor.fetchall()
    if not rows:
        print("No hay registros de asistencia hoy.")
        return

    for row in rows:
        print(f"{row[1]} ({row[2].upper()}) | {row[3].upper()} | {row[4]} | Confianza: {row[5]*100:.1f}%")

    conn.close()

def exportar_csv(fecha_inicio=None, fecha_fin=None):
    if fecha_inicio is None:
        fecha_inicio = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    if fecha_fin is None:
        fecha_fin = datetime.now().strftime("%Y-%m-%d")

    conn = conectar_db()
    cursor = conn.cursor()

    sql = "SELECT p.nombre, p.tipo_personal, p.area, p.cargo, r.tipo, r.fecha_hora, r.confianza FROM registros_asistencia r JOIN personal p ON r.personal_id = p.id WHERE date(r.fecha_hora) BETWEEN ? AND ? ORDER BY r.fecha_hora DESC"
    cursor.execute(sql, (fecha_inicio, fecha_fin))

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("No hay registros para exportar en ese rango.")
        return

    filename = f"asistencias_SENATI_{fecha_inicio}_a_{fecha_fin}.csv"
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Nombre", "Tipo", "Area", "Cargo", "Tipo Registro", "Fecha y Hora", "Confianza"])
        for row in rows:
            writer.writerow([row[0], row[1], row[2] or "N/A", row[3] or "N/A", row[4], row[5], f"{row[6]*100:.1f}%"])

    print(f"\nReporte exportado: {filename}")
    print(f"Total registros: {len(rows)}")

def menu():
    while True:
        print("\n" + "=" * 60)
        print("  MENU DE REPORTES - SENATI")
        print("=" * 60)
        print("1. Ver personal registrado")
        print("2. Ver asistencias de hoy")
        print("3. Exportar asistencias a CSV")
        print("4. Salir")

        opcion = input("\nElige una opcion: ").strip()

        if opcion == "1":
            ver_personal()
        elif opcion == "2":
            ver_asistencias_hoy()
        elif opcion == "3":
            inicio = input("Fecha inicio (YYYY-MM-DD, Enter=hoy-30d): ").strip()
            fin = input("Fecha fin (YYYY-MM-DD, Enter=hoy): ").strip()
            exportar_csv(inicio or None, fin or None)
        elif opcion == "4":
            print("Hasta luego!")
            break
        else:
            print("Opcion no valida.")

if __name__ == "__main__":
    menu()
