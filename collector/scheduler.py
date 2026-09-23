"""Programador de actualizaciones automáticas para gasolina-gt.

Soporta dos modos:
  1. Ejecución única: python scheduler.py --run-all
     Corre todos los colectores una vez + exportación opcional.

  2. Programación repetitiva: python scheduler.py --schedule N
     Ejecuta cada N segundos (ej: 3600 = cada hora).

También genera un archivo XML para Windows Task Scheduler:
    python scheduler.py --export-task "Actualización gasolina-gt"

Dependencias: solo stdlib (sched, threading, subprocess).
"""

import json
import logging
import os
import sched
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ──────────────────────────────────────────────
# Configuración
# ──────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_DIR / f"scheduler_{datetime.now().strftime('%Y%m%d')}.log",
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("scheduler")

# Flag para detener el loop gracefulmente
_running = True


def _signal_handler(sig, frame):
    """Maneña Ctrl+C para detener el scheduler gracefully."""
    global _running
    logger.info("Recibido SIGINT, deteniendo programador...")
    _running = False


# ──────────────────────────────────────────────
# Ejecución de colectores
# ──────────────────────────────────────────────

def run_all(export: bool = True) -> dict:
    """Ejecuta todos los colectores y exporta JSON.

    Args:
        export: Si True, ejecuta main.py --all --export.

    Returns:
        Dict con resumen de la ejecución.
    """
    logger.info("Iniciando actualizacion completa...")

    cmd = [sys.executable, str(PROJECT_ROOT / "collector" / "main.py"), "--all"]
    if export:
        cmd.append("--export")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=False,  # mostrar output en consola
            timeout=300,  # max 5 minutos por ejecucion
        )
        if result.returncode == 0:
            logger.info("Actualizacion completada exitosamente")
            return {"status": "ok", "returncode": 0}
        else:
            logger.error(f"Falló con codigo {result.returncode}")
            return {"status": "error", "returncode": result.returncode}

    except subprocess.TimeoutExpired:
        logger.error("Timeout: ejecucion exedio 5 minutos")
        return {"status": "timeout"}
    except Exception as exc:
        logger.error(f"Error inesperado: {exc}")
        return {"status": "error", "message": str(exc)}


# ──────────────────────────────────────────────
# Programador con threading (no blocking)
# ──────────────────────────────────────────────

def _scheduler_loop(interval_seconds: int):
    """Loop principal del programador."""
    logger.info(f"Programador activo — cada {interval_seconds}s ({interval_seconds/60:.1f} min)")
    run_count = 0

    while _running:
        try:
            run_count += 1
            inicio = datetime.now()
            logger.info(f"[ejecucion #{run_count}] Iniciando...")

            resultado = run_all(export=True)

            fin = datetime.now()
            duracion = (fin - inicio).total_seconds()
            logger.info(
                f"[ejecucion #{run_count}] Completado en {duracion:.1f}s "
                f"({resultado.get('status', '?')})"
            )

        except Exception as exc:
            logger.error(f"[ejecucion #{run_count}] Error: {exc}")

        # Esperar hasta la proxima ejecucion (Ctrl+C interrumpe este sleep)
        try:
            for _ in range(interval_seconds):
                if not _running:
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            _running = False

    logger.info(f"Programador detenido. Total de ejecuciones: {run_count}")


def schedule(interval_seconds: int = 3600, background: bool = True):
    """Inicia el programador en un hilo separado.

    Args:
        interval_seconds: Segundos entre ejecuciones (default: 1 hora).
        background: Si True, corre en segundo plano.
    """
    signal.signal(signal.SIGINT, _signal_handler)

    if background:
        import threading

        thread = threading.Thread(target=_scheduler_loop, args=(interval_seconds,), daemon=True)
        thread.start()
        logger.info(f"Programador iniciado en hilo separado (intervalo: {interval_seconds}s)")
        return thread
    else:
        _scheduler_loop(interval_seconds)


# ──────────────────────────────────────────────
# Generador de Windows Task Scheduler XML
# ──────────────────────────────────────────────

def export_task_xml(
    task_name: str = "gasolina-gt-update",
    interval_minutes: int = 60,
    output_path: Path = None,
) -> Path:
    """Genera un archivo XML para Windows Task Scheduler.

    Args:
        task_name: Nombre de la tarea en el programador de Windows.
        interval_minutes: Frecuencia en minutos (default: 60).
        output_path: Ruta del archivo XML generado.

    Returns:
        Path al archivo XML generado.
    """
    if output_path is None:
        output_path = PROJECT_ROOT / f"{task_name}.xml"

    # Calcular intervalo en segundos para el trigger
    seconds = interval_minutes * 60

    xml_content = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Actualiza precios de combustible y petrolero — {task_name}</Description>
    <URI>\\{{5BDA7795-31B7-497E-9DB7-9C6D0F0421DC}}\\{task_name}</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <Repetition>
        <Interval>PT{seconds}S</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{sys.executable}</Command>
      <Arguments>"{PROJECT_ROOT / 'collector' / 'main.py'}" --all --export</Arguments>
      <WorkingDirectory>{PROJECT_ROOT}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""

    with open(output_path, "w", encoding="utf-16") as f:
        f.write(xml_content)

    logger.info(f"XML generado: {output_path}")
    return output_path


def install_task(task_name: str = "gasolina-gt-update", interval_minutes: int = 60):
    """Instala la tarea en Windows Task Scheduler.

    Args:
        task_name: Nombre de la tarea.
        interval_minutes: Frecuencia en minutos.
    """
    xml_path = export_task_xml(task_name, interval_minutes)

    logger.info(f"Instalando tarea '{task_name}' en Windows Task Scheduler...")
    subprocess.run(
        ["schtasks", "/Create", "/TN", task_name, "/XML", str(xml_path), "/F"],
        capture_output=True,
        text=True,
    )
    logger.info(f"Tarea instalada: {task_name}")


def uninstall_task(task_name: str = "gasolina-gt-update"):
    """Elimina la tarea de Windows Task Scheduler.

    Args:
        task_name: Nombre de la tarea a eliminar.
    """
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", task_name, "/F"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        logger.info(f"Tarea eliminada: {task_name}")
    else:
        logger.error(f"Error al eliminar tarea: {result.stderr}")


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def main():
    """Entry point para la linea de comandos."""
    import argparse as _argparse

    parser = _argparse.ArgumentParser(
        description="gasolina-gt — Programador de actualizaciones automaticas",
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        epilog="""\
Ejemplos:
  # Ejecucion unica ahora + exportar JSON
  python scheduler.py --run-all

  # Programar cada hora (3600 segundos)
  python scheduler.py --schedule 3600

  # Generar XML para Windows Task Scheduler
  python scheduler.py --export-task "gasolina-gt" --interval-min 60

  # Instalar directamente en Windows Task Scheduler
  python scheduler.py --install-task --interval-min 60

  # Desinstalar tarea de Windows
  python scheduler.py --uninstall-task
        """,
    )

    parser.add_argument(
        "--run-all", action="store_true", help="Ejecutar todos los colectores una vez"
    )
    parser.add_argument(
        "--schedule",
        type=int,
        metavar="SEGUNDOS",
        help="Programar ejecuciones repetidas cada N segundos",
    )
    parser.add_argument(
        "--export-task",
        type=str,
        metavar="NOMBRE",
        help="Generar XML para Windows Task Scheduler",
    )
    parser.add_argument(
        "--install-task", action="store_true", help="Instalar en Windows Task Scheduler"
    )
    parser.add_argument(
        "--uninstall-task", type=str, default="", metavar="NOMBRE", help="Eliminar tarea de Windows"
    )
    parser.add_argument(
        "--interval-min",
        type=int,
        default=60,
        help="Intervalo en minutos para tareas (default: 60)",
    )

    args = parser.parse_args()

    # --run-all: ejecucion unica
    if args.run_all:
        logger.info("=" * 50)
        logger.info("Actualizacion rapida — gasolina-gt")
        logger.info("=" * 50)
        resultado = run_all(export=True)
        sys.exit(0 if resultado.get("status") == "ok" else 1)

    # --schedule: programacion repetida
    if args.schedule:
        schedule(interval_seconds=args.schedule, background=False)
        return

    # --export-task: generar XML
    if args.export_task:
        path = export_task_xml(args.export_task, args.interval_min)
        print(f"\nXML generado: {path}")
        print("\nPara instalar manualmente:")
        print(
            f'  schtasks /Create /TN "{args.export_task}" /XML "{path}" /F'
        )
        return

    # --install-task: instalar en Windows
    if args.install_task:
        install_task(args.export_task or "gasolina-gt-update", args.interval_min)
        print(f"\nTarea instalada. Para verificar:")
        print(f'  schtasks /Query /TN "{args.export_task or "gasolina-gt-update"}"')
        return

    # --uninstall-task: eliminar tarea
    if args.uninstall_task:
        uninstall_task(args.uninstall_task)
        return

    # Sin argumentos → mostrar ayuda
    parser.print_help()


if __name__ == "__main__":
    main()
