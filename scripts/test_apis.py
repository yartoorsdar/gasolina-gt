# ⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️
######## INICIA SCRIPT DE TEST APIS ########
"""Testea las APIs del pipeline de noticias (solo lectura: sin DB, sin export).

Reproduce EXACTAMENTE lo que hace collector/noticias.py en CI para poder ver,
con un prompt mínimo y sin gastar nada más, si cada API responde desde el
runner (mismo pool de IPs windows-latest que daily-update):

  1. Gemini (ruta producción): ping GET /models/{model} + generateContent
     con config.json → llm.base_url/model (override opcional TEST_LLM_MODEL).
  2. Gemini listModels: qué modelos existen para la key (diagnóstico 404/402:
     si el modelo no está en la lista, el ID o la free tier lo explican).
  3. Groq OpenAI-compatible (respaldo): GET /v1/models con Bearer; completado
     solo si TEST_GROQ_MODEL está definido (evita adivinar IDs de modelos).
  4. MyMemory (fallback de traducción, sin key): una traducción EN→ES para
     comprobar que la cuota anónima sigue viva.

Códigos HTTP típicos de Gemini:
  200 OK | 401 key inválida/expirada | 402 cuota/prepagos agotado (billing)
  403 permiso denegado | 404 modelo inexistente para la key | 429 rate-limit

Uso:
    python scripts/test_apis.py                       # llm.* de config.json
    set TEST_LLM_MODEL=gemini-3-flash-lite            # (Windows) probar otro
    python scripts/test_apis.py                       #   modelo sin editar config
    set GROQ_TEST_MODEL=gpt-oss-120b                  # + completado Groq

Salida: tabla PASS/FAIL por API (+ GITHUB_STEP_SUMMARY si existe en CI).
Exit code: 0 si al menos un LLM completó el prompt de prueba; 1 si ninguno.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 30
_GT = timezone(timedelta(hours=-6))

# Prompt mínimo: solo basta a verificar que el modelo responde y emite JSON.
PROMPT_TEST = 'Eres un analista de energia. Responde SOLO con JSON valido: {"ok": true}'


def _hora() -> str:
    now_utc = datetime.now(timezone.utc)
    return f"UTC={now_utc.strftime('%Y-%m-%d %H:%M:%S')} GT={(now_utc.astimezone(_GT)).strftime('%H:%M:%S')}"


def _fp(key: str | None, name: str) -> dict | None:
    """Huella no sensible de la key (prefijo + longitud + sha), misma convención
    que collector/noticias.py:_llm_key_fp. Nunca imprimir el valor en bruto."""
    v = (key or "").strip()
    if not v:
        return None
    import hashlib as _hl
    return {"var": name, "fp": f"{v[:4]}*** len={len(v)} sha={_hl.sha256(v.encode()).hexdigest()[:8]}"}


def _sanear(txt: str) -> str:
    """Quita los query params key=... que las excepciones de requests incluyen."""
    return re.sub(r"key=[^&\s'\"]+", "key=***", txt)[:200]


# ──────────────────────────────────────────────
# 1. Gemini — ruta producción (misma forma de llamada que noticias.py)
# ──────────────────────────────────────────────

def test_gemini(cfg: dict, model_override: str = "") -> dict:
    llm_cfg = cfg.get("llm", {})
    base_url = (llm_cfg.get("base_url") or "").strip().rstrip("/")
    model = model_override.strip() or (llm_cfg.get("model") or "").strip()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()

    r = {"api": "Gemini", "modelo": model, "http_ping": None, "http_prompt": None,
         "ok": False, "respuesta": None, "ms": 0, "modelos_fresh": [], "modelo_en_catalogo": None,
         "tokens_usados": None, "error": None}
    if not base_url or not model:
        r["error"] = "config.json llm.base_url/model vacío"
        return r
    if not api_key:
        r["error"] = "sin GEMINI_API_KEY en el entorno"
        return r

    # a) Ping: GET /models/{model} (idéntico al de noticias.py:_llm_available)
    try:
        t0 = time.time()
        resp = requests.get(f"{base_url}/models/{model}?key={api_key}", timeout=TIMEOUT)
        r["http_ping"] = resp.status_code
        r["ms"] += int((time.time() - t0) * 1000)
    except Exception as exc:
        r["error"] = f"ping: {_sanear(str(exc))}"
        return r

    # b) Catálogo de modelos (diagnóstico: ¿existe el modelo para esta key?)
    try:
        resp = requests.get(f"{base_url}/models?key={api_key}", timeout=TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            r["modelos_fresh"] = [m.get("name", "") for m in data.get("models", [])]
            r["modelo_en_catalogo"] = any(
                (m.get("name") or "").replace("models/", "") == model
                for m in data.get("models", [])
            )
        else:
            r["error"] = f"listModels HTTP {resp.status_code}: {_sanear(resp.text[:150])}"
    except Exception as exc:
        r["error"] = f"listModels: {_sanear(str(exc))}"

    # c) Prompt mínimo (misma forma de llamada que noticias.py:_llm_post Gemini)
    try:
        t0 = time.time()
        resp = requests.post(
            f"{base_url}/models/{model}:generateContent?key={api_key}",
            json={"contents": [{"parts": [{"text": PROMPT_TEST}]}],
                  "generationConfig": {"temperature": 0.3}},
            timeout=TIMEOUT,
        )
        r["http_prompt"] = resp.status_code
        r["ms"] += int((time.time() - t0) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            cand = (data.get("candidates") or [{}])[0]
            texto = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
            r["respuesta"] = texto[:200]
            r["finish_reason"] = cand.get("finishReason")
            uso = data.get("usageMetadata") or {}
            if uso:
                r["tokens_usados"] = (uso.get("promptTokenCount", 0), uso.get("candidatesTokenCount", 0))
            r["ok"] = bool(texto.strip())
            if not r["ok"]:
                r["error"] = "respuesta vacía"
        else:
            try:
                msg = (resp.json() or {}).get("message") or resp.text[:150]
            except Exception:
                msg = resp.text[:150]
            r["error"] = f"prompt HTTP {resp.status_code}: {_sanear(str(msg))}"
    except Exception as exc:
        r["http_prompt"] = None
        r["error"] = (r["error"] + " | " if r["error"] else "") + f"prompt: {_sanear(str(exc))}"

    return r


# ──────────────────────────────────────────────
# 2. Groq OpenAI-compatible (respaldo) — solo si hay GROK_API_KEY
# ──────────────────────────────────────────────

def test_groq(model_override: str = "") -> dict | None:
    api_key = os.environ.get("GROK_API_KEY", "").strip()
    if not api_key:
        return None
    base = "https://api.groq.com/openai"
    r = {"api": "Groq", "modelo": model_override or "(solo ping)", "http_ping": None,
         "http_prompt": None, "ok": False, "respuesta": None, "ms": 0,
         "modelos_fresh": [], "error": None}

    try:
        t0 = time.time()
        resp = requests.get(f"{base}/v1/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=TIMEOUT)
        r["http_ping"] = resp.status_code
        r["ms"] += int((time.time() - t0) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            r["modelos_fresh"] = [m.get("id", "") for m in (data.get("data") or [])][:40]
        else:
            r["error"] = f"ping HTTP {resp.status_code}: {_sanear(resp.text[:150])}"
            return r
    except Exception as exc:
        r["error"] = f"ping: {_sanear(str(exc))}"
        return r

    if model_override.strip():
        try:
            t0 = time.time()
            resp = requests.post(
                f"{base}/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": model_override.strip(),
                      "messages": [{"role": "user", "content": PROMPT_TEST}],
                      "temperature": 0.3, "max_tokens": 64},
                timeout=TIMEOUT,
            )
            r["http_prompt"] = resp.status_code
            r["ms"] += int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                texto = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                r["respuesta"] = (texto or "")[:200]
                r["ok"] = bool((texto or "").strip())
            else:
                r["error"] = f"prompt HTTP {resp.status_code}: {_sanear(resp.text[:150])}"
        except Exception as exc:
            r["http_prompt"] = None
            r["error"] = (r["error"] + " | " if r["error"] else "") + f"prompt: {_sanear(str(exc))}"

    return r


# ──────────────────────────────────────────────
# 3. MyMemory — fallback de traducción EN→ES (sin key)
# ──────────────────────────────────────────────

def test_mymemory() -> dict:
    r = {"api": "MyMemory", "ok": False, "respuesta": None, "ms": 0, "error": None}
    try:
        t0 = time.time()
        resp = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": "Oil prices rise after refinery attack", "langpair": "en|es"},
            timeout=TIMEOUT,
        )
        r["ms"] = int((time.time() - t0) * 1000)
        resp.raise_for_status()
        data = resp.json()
        if data.get("quotaFinished"):
            r["error"] = "cuota diaria anónima agotada"
        else:
            trad = ((data.get("responseData") or {}).get("translatedText") or "").strip()
            r["respuesta"] = trad[:120] or None
            r["ok"] = bool(trad)
            if not r["ok"]:
                r["error"] = "traducción vacía"
    except Exception as exc:
        r["error"] = _sanear(str(exc))
    return r


# ──────────────────────────────────────────────
# Salida + orquestación
# ──────────────────────────────────────────────

def main() -> int:
    cfg_path = ROOT / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}

    print("=" * 60)
    print("gasolina-gt — Test APIs (solo lectura)")
    print(f"Hora: {_hora()} | runner={os.environ.get('RUNNER_OS', 'local')}")
    for name in ("GEMINI_API_KEY", "GROK_API_KEY"):
        fp = _fp(os.environ.get(name), name)
        print(f"  {name}: {fp['fp'] if fp else 'SIN KEY'}")
    print("=" * 60)

    resultados = []
    gemini = test_gemini(cfg, os.environ.get("TEST_LLM_MODEL", ""))
    resultados.append(gemini)
    groq = test_groq(os.environ.get("GROQ_TEST_MODEL", "").strip())
    if groq:
        resultados.append(groq)
    mymem = test_mymemory()
    resultados.append(mymem)

    # ── Tabla por API ──
    for r in resultados:
        estado = "OK  " if r.get("ok") else ("SKIP" if (r["api"] == "Groq" and not r.get("http_ping")) else "FAIL")
        partes = [f"{r['api']} ({r.get('modelo', '-')})"]
        if r.get("http_ping") is not None:
            partes.append(f"ping={r['http_ping']}")
        if r.get("http_prompt") is not None:
            partes.append(f"prompt={r['http_prompt']}")
        print(f"[{estado}] {' | '.join(partes)} | {r['ms']}ms")

    if not any(r["api"] == "Groq" for r in resultados):
        print("[INFO] Groq omitido: sin GROK_API_KEY en el entorno (o key vacía)")

    g = resultados[0]
    modelos = [m for m in (g.get("modelos_fresh") or []) if "flash" in m or "lite" in m]
    if modelos:
        en_catalogo = "SÍ" if g.get("modelo_en_catalogo") else "NO ⚠️"
        print(f"[INFO] Gemini {len(g['modelos_fresh'])} modelos en catálogo; modelo '{g['modelo']}' presente: {en_catalogo}")
        for m in modelos[:25]:
            marca = "  ← objetivo" if (m.replace("models/", "") == g["modelo"]) else ""
            print(f"       - {m}{marca}")
    if g.get("respuesta"):
        tok = g.get("tokens_usados") or ("?", "?")
        print(f"[INFO] Gemini respondió ({g.get('finish_reason', '?')}, prompt={tok[0]} out={tok[1]} tokens): {g['respuesta']!r}")
    if g.get("error"):
        print(f"[FAIL] Gemini: {g['error']}")

    for r in resultados[1:]:
        if not r["ok"] and "Groq" in r["api"]:
            ms = [m for m in (r.get("modelos_fresh") or [])]
            if ms:
                print(f"[INFO] Groq {len(ms)} modelos disponibles (primeros): {', '.join(ms[:12])}")
        if r.get("respuesta"):
            print(f"[OK  ] {r['api']} respondió: {r['respuesta']!r}")
        elif r.get("error"):
            print(f"[FAIL] {r['api']}: {r['error']}")

    # ── Step summary (CI) + log local ──
    resumen_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if resumen_path:
        md = ["## Test APIs — LLM de noticias", "", f"Hora: {_hora()}", ""]
        for r in resultados:
            icono = "✅" if r.get("ok") else ("— " if (r["api"] == "Groq" and not r.get("http_ping")) else "❌")
            md.append(f"{icono} **{r['api']}** ({r.get('modelo', '-')}): {r.get('error') or 'OK'}")
        with open(resumen_path, "a", encoding="utf-8") as f:
            f.write("\n".join(md) + "\n")

    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "apis_test.json").write_text(
        json.dumps({"probado": datetime.now(timezone.utc).isoformat(), "resultados": resultados},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Exit 0 si al menos un LLM completó el prompt (MyMemory no cuenta: es informativo)
    llm_ok = any(r["api"] in ("Gemini", "Groq") and r.get("ok") for r in resultados)
    print(f"\n{'LLM SÍ responde' if llm_ok else 'LLM NO respondió'} — exit 0 si al menos un LLM completó el prompt")
    return 0 if llm_ok else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
######## FINALIZA SCRIPT DE TEST APIS ########
# ⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️
