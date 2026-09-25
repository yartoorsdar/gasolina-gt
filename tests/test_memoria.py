"""Tests del sistema de memoria persistente de precios (collector/memoria.py).

Semántica que debe mantenerse:
  - Round-trip exportar→importar reproduce la tabla (1 fila por fecha+producto).
  - Determinismo: mismo contenido = archivo byte-idéntico (git sin diff).
  - Re-ejecutar un día ACTUALIZA, no duplica (UNIQUE fecha+producto + upserts).
"""


class TestMemoria:
    def _db_con_filas(self):
        from collector.db import conectar_temporal

        conn = conectar_temporal()
        conn.execute(
            "INSERT INTO precios (fecha, producto, precio, fuente, fetched_at) VALUES (?, ?, ?, ?, ?)",
            ("2026-09-16", "superior", 44.66, "MEM", "2026-09-16T08:00:00-06:00"),
        )
        conn.execute(
            "INSERT INTO precios (fecha, producto, precio, fuente, fetched_at) VALUES (?, ?, ?, ?, ?)",
            ("2026-09-16", "regular", 42.58, "MEM", "2026-09-16T08:00:00-06:00"),
        )
        conn.execute(
            "INSERT INTO precios (fecha, producto, precio, fuente, fetched_at) VALUES (?, ?, ?, ?, ?)",
            ("2026-09-24", "wti", 93.56, "OilPriceAPI", "2026-09-25T08:00:00-06:00"),
        )
        conn.commit()
        return conn

    def test_round_trip(self):
        """Exportar y re-importar en DB vacía reproduce las filas exactas."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from collector.db import conectar_temporal
        from collector.memoria import exportar_memoria, importar_memoria

        conn = self._db_con_filas()
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "mem.csv"
            exportar_memoria(conn=conn, path=path)
            assert path.exists()

            vacia = conectar_temporal()
            n = importar_memoria(path=path, conn=vacia)
            filas = vacia.execute(
                "SELECT fecha, producto, precio FROM precios ORDER BY fecha, producto"
            ).fetchall()

        assert n == 3
        assert [(f["fecha"], f["producto"], f["precio"]) for f in filas] == [
            ("2026-09-16", "regular", 42.58),
            ("2026-09-16", "superior", 44.66),
            ("2026-09-24", "wti", 93.56),
        ]

    def test_determinismo_bytes(self):
        """Dos exports del mismo contenido producen bytes idénticos (cero diff)."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from collector.memoria import exportar_memoria

        conn = self._db_con_filas()
        with TemporaryDirectory() as tmp:
            p1, p2 = Path(tmp) / "a.csv", Path(tmp) / "b.csv"
            exportar_memoria(conn=conn, path=p1)
            exportar_memoria(conn=self._db_con_filas(), path=p2)
            bytes_1, bytes_2 = p1.read_bytes(), p2.read_bytes()

        assert bytes_1 == bytes_2  # mismo contenido → byte-idéntico (cero diff git)

    def test_sin_archivo_no_explota(self):
        """Primer ciclo (sin CSV todavía): importar devuelve 0 y no lanza."""
        from pathlib import Path

        from collector.db import conectar_temporal
        from collector.memoria import importar_memoria

        conn = conectar_temporal()
        n = importar_memoria(path=Path("c:\\tmp\\no_existe_xyz.csv"), conn=conn)
        assert n == 0

    def test_deduplica_grafias_por_fecha(self):
        """'diessel' y 'diésel' del mismo día → UNA fila, la más recién importada."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from collector.db import conectar_temporal
        from collector.memoria import exportar_memoria, importar_memoria

        conn = conectar_temporal()
        conn.execute(
            "INSERT INTO precios (fecha, producto, precio, fuente, fetched_at) VALUES (?, ?, ?, ?, ?)",
            ("2024-01-01", "diessel", 29.47, "Ministerio de Energía y Minas", "2026-09-24T12:00:00-06:00"),
        )
        conn.execute(
            "INSERT INTO precios (fecha, producto, precio, fuente, fetched_at) VALUES (?, ?, ?, ?, ?)",
            ("2024-01-01", "diésel", 30.0, "Ministerio de Energía y Minas", "2026-09-24T23:00:00-06:00"),
        )
        conn.commit()

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "mem.csv"
            exportar_memoria(conn=conn, path=path)

            # El CSV debe tener UNA sola fila para 2024-01-01/diésel (la nueva).
            contenido = path.read_text(encoding="utf-8")
            lineas_diesel_2024 = [
                l for l in contenido.splitlines() if l.startswith("2024-01-01,diésel,")
            ]
            assert len(lineas_diesel_2024) == 1
            assert "30.0" in lineas_diesel_2024[0]  # la de fetched_at más reciente

            # Y el round-trip completo no duplica: 1 fila resultante para ese día.
            vacia = conectar_temporal()
            importar_memoria(path=path, conn=vacia)
            total = vacia.execute(
                "SELECT COUNT(*) FROM precios WHERE fecha='2024-01-01'"
            ).fetchone()[0]

        assert total == 1

    def test_importar_no_pisa_valor_distinto(self):
        """Insert-or-ignore: una fila existente con otro valor se conserva."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from collector.db import conectar_temporal, insertar_precio
        from collector.memoria import exportar_memoria, importar_memoria

        # DB con un valor manual más reciente para el mismo par (fecha, producto)
        conn = conectar_temporal()
        insertar_precio(conn, "2026-09-16", "superior", 45.0, "MEM")
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "mem.csv"
            exportar_memoria(conn=conn, path=path)

            otra = conectar_temporal()
            # La memoria trae 45.0 (del volcado) → coincide; probemos con valor distinto:
            otra.execute(
                "INSERT INTO precios (fecha, producto, precio, fuente, fetched_at) VALUES (?, ?, ?, ?, ?)",
                ("2026-09-16", "superior", 44.0, "otro", "2026-09-01T00:00:00-06:00"),
            )
            otra.commit()
            importar_memoria(path=path, conn=otra)

        # OR IGNORE: la fila preexistente (44.0) no se pisa con la del CSV (45.0).
        assert otra.execute(
            "SELECT precio FROM precios WHERE fecha='2026-09-16' AND producto='superior'"
        ).fetchone()[0] == 44.0
