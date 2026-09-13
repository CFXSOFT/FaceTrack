import sqlite3
import os
import pickle
import numpy as np

DB_PATH = "asistencia.db"

def conectar_db():
    return sqlite3.connect(DB_PATH)

def listar_personal():
    print("\n" + "=" * 70)
    print("  PERSONAL REGISTRADO - SENATI")
    print("=" * 70)

    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, dni, nombre, tipo_personal, area, cargo, activo, fecha_registro FROM personal ORDER BY id")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("No hay personal registrado.")
        return

    print(f"{'ID':<5} {'DNI':<12} {'Nombre':<25} {'Tipo':<10} {'Area':<15} {'Cargo':<15} {'Estado':<8}")
    print("-" * 70)
    for row in rows:
        estado = "Activo" if row[6] else "Inactivo"
        dni = row[1] if row[1] else "N/A"
        print(f"{row[0]:<5} {dni:<12} {row[2]:<25} {row[3]:<10} {row[4] or 'N/A':<15} {row[5] or 'N/A':<15} {estado:<8}")
    print("-" * 70)
    print(f"Total: {len(rows)} personas registradas")

def buscar_personal():
    print("\n--- BUSCAR PERSONAL ---")
    criterio = input("Buscar por nombre, DNI o area: ").strip()
    if not criterio:
        return

    conn = conectar_db()
    cursor = conn.cursor()
    sql = "SELECT id, dni, nombre, tipo_personal, area, cargo, activo FROM personal WHERE nombre LIKE ? OR dni LIKE ? OR area LIKE ?"
    param = f"%{criterio}%"
    cursor.execute(sql, (param, param, param))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("No se encontraron resultados.")
        return

    print(f"\n{'ID':<5} {'DNI':<12} {'Nombre':<25} {'Tipo':<10} {'Area':<15} {'Cargo':<15}")
    print("-" * 70)
    for row in rows:
        dni = row[1] if row[1] else "N/A"
        print(f"{row[0]:<5} {dni:<12} {row[2]:<25} {row[3]:<10} {row[4] or 'N/A':<15} {row[5] or 'N/A':<15}")

def eliminar_personal():
    print("\n--- ELIMINAR PERSONAL ---")
    listar_personal()

    id_personal = input("\nID del personal a eliminar (o Enter para cancelar): ").strip()
    if not id_personal:
        return

    try:
        id_personal = int(id_personal)
    except ValueError:
        print("ID invalido.")
        return

    conn = conectar_db()
    cursor = conn.cursor()

    # Verificar que existe (icono_path/horarios_bloques/justificaciones son usados por la
    # app web sobre la misma base de datos, asi que tambien se limpian aqui para que no
    # queden residuos sin importar por cual interfaz se elimino a la persona)
    columnas = {row[1] for row in cursor.execute("PRAGMA table_info(personal)")}
    campo_icono = "icono_path" if "icono_path" in columnas else None
    if campo_icono:
        cursor.execute(f"SELECT nombre, foto_path, {campo_icono} FROM personal WHERE id = ?", (id_personal,))
    else:
        cursor.execute("SELECT nombre, foto_path FROM personal WHERE id = ?", (id_personal,))
    resultado = cursor.fetchone()

    if not resultado:
        print("No se encontro personal con ese ID.")
        conn.close()
        return

    nombre, foto_path = resultado[0], resultado[1]
    icono_path = resultado[2] if campo_icono else None
    confirmar = input(f"Estas seguro de eliminar a '{nombre}'? (s/n): ").strip().lower()

    if confirmar != "s":
        print("Eliminacion cancelada.")
        conn.close()
        return

    # Eliminar foto e icono si existen
    for ruta in (foto_path, icono_path):
        if ruta and os.path.exists(ruta):
            os.remove(ruta)
            print("Archivo eliminado:", ruta)

    tablas_hijas = {r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    asistencias_eliminadas = 0
    if "registros_asistencia" in tablas_hijas:
        cursor.execute("DELETE FROM registros_asistencia WHERE personal_id = ?", (id_personal,))
        asistencias_eliminadas = cursor.rowcount
    if "justificaciones" in tablas_hijas:
        cursor.execute("DELETE FROM justificaciones WHERE personal_id = ?", (id_personal,))
    if "horarios_bloques" in tablas_hijas:
        cursor.execute("DELETE FROM horarios_bloques WHERE personal_id = ?", (id_personal,))

    # Eliminar personal
    cursor.execute("DELETE FROM personal WHERE id = ?", (id_personal,))

    conn.commit()
    conn.close()

    print(f"'{nombre}' eliminado exitosamente.")
    print(f"Registros de asistencia eliminados: {asistencias_eliminadas}")

def desactivar_personal():
    print("\n--- DESACTIVAR/ACTIVAR PERSONAL ---")
    listar_personal()

    id_personal = input("\nID del personal a cambiar estado: ").strip()
    if not id_personal:
        return

    try:
        id_personal = int(id_personal)
    except ValueError:
        print("ID invalido.")
        return

    conn = conectar_db()
    cursor = conn.cursor()

    cursor.execute("SELECT nombre, activo FROM personal WHERE id = ?", (id_personal,))
    resultado = cursor.fetchone()

    if not resultado:
        print("No se encontro personal con ese ID.")
        conn.close()
        return

    nombre, activo = resultado
    nuevo_estado = 0 if activo else 1
    estado_texto = "activar" if nuevo_estado else "desactivar"

    confirmar = input(f"Deseas {estado_texto} a '{nombre}'? (s/n): ").strip().lower()
    if confirmar != "s":
        print("Operacion cancelada.")
        conn.close()
        return

    cursor.execute("UPDATE personal SET activo = ? WHERE id = ?", (nuevo_estado, id_personal))
    conn.commit()
    conn.close()

    print(f"'{nombre}' ahora esta {'ACTIVO' if nuevo_estado else 'INACTIVO'}.")

def menu_gestion():
    while True:
        print("\n" + "=" * 60)
        print("  GESTION DE PERSONAL - SENATI")
        print("=" * 60)
        print("1. Listar todo el personal")
        print("2. Buscar personal")
        print("3. Eliminar personal")
        print("4. Activar/Desactivar personal")
        print("5. Salir")

        opcion = input("\nElige una opcion: ").strip()

        if opcion == "1":
            listar_personal()
        elif opcion == "2":
            buscar_personal()
        elif opcion == "3":
            eliminar_personal()
        elif opcion == "4":
            desactivar_personal()
        elif opcion == "5":
            print("Hasta luego!")
            break
        else:
            print("Opcion no valida.")

if __name__ == "__main__":
    menu_gestion()