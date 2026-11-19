"""
actualizar_velas.py — Atlas Macro

Baja las velas diarias (apertura, máximo, mínimo, cierre y volumen) de los últimos 5 años de cada
instrumento que se puede graficar en la página de Análisis técnico, y las guarda en la carpeta velas/:

    velas/indice.json        la lista de todo lo que hay, para el buscador
    velas/<id>.json          las velas de cada uno

Qué se baja (la misma lista que usa actualizar_mercados.py, así no hay que mantener dos):
    · las acciones del panel líder y los CEDEARs, en pesos, como cotizan en BYMA (Yahoo: "GGAL.BA")
    · las acciones de EE. UU. que están detrás de cada CEDEAR, en dólares (Yahoo: "AAPL")
    · los ADRs argentinos, índices, tasas, materias primas, cripto y monedas

La fuente es Yahoo Finance. Las velas semanales y mensuales las arma la página a partir de las diarias.
Si un instrumento falla, se deja el archivo que ya estaba (no se borra nada).
"""

import json
import time
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

from curl_cffi import requests

from actualizar_mercados import CEDEARS, INSTRUMENTOS, PANEL

AQUI = Path(__file__).parent
CARPETA = AQUI / "velas"
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5y&interval=1d&events=div%2Csplits"
NO_VA = {"merval"}       # Yahoo dejó de actualizar el Merval (la página lo toma de BYMA)
US_DE_CEDEAR = {"BRKB": "BRK-B"}   # el ticker de Nueva York cuando no coincide con el del CEDEAR

sesion = requests.Session(impersonate="chrome")


def bajar(ticker):
    url = URL.format(ticker=ticker)
    try:
        r = sesion.get(url, timeout=40)
    except Exception:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            r = sesion.get(url, timeout=40, verify=False)
    res = r.json()["chart"]["result"][0]
    meta, ts = res["meta"], res.get("timestamp") or []
    q = res["indicators"]["quote"][0]
    desfase = meta.get("gmtoffset", 0)          # para que la fecha sea la del día del mercado, no la de Greenwich
    filas = {}
    for i, t in enumerate(ts):
        o, h, l, c = (q.get(k, [None] * len(ts))[i] for k in ("open", "high", "low", "close"))
        if None in (o, h, l, c) or c <= 0:
            continue
        v = (q.get("volume") or [None] * len(ts))[i] or 0
        dia = datetime.fromtimestamp(t + desfase, timezone.utc).strftime("%Y-%m-%d")
        d = 4 if c < 10 else 2
        filas[dia] = [dia, round(o, d), round(max(h, o, c), d), round(min(l, o, c), d), round(c, d), int(v)]
    return [filas[k] for k in sorted(filas)], meta


def lista():
    """[(id, nombre, ticker de Yahoo, grupo, moneda)] de todo lo que se grafica."""
    out = []
    for t, (nombre, _) in PANEL.items():
        out.append((f"panel-{t}", nombre, f"{t}.BA", "Panel líder (BYMA)", "ARS"))
    for t, (nombre, _) in CEDEARS.items():
        out.append((f"cedear-{t}", f"{nombre} (CEDEAR)", f"{t}.BA", "CEDEARs (BYMA)", "ARS"))
        out.append((f"eeuu-{t}", nombre, US_DE_CEDEAR.get(t, t), "Acciones de EE. UU.", "USD"))
    nombres = {"indices": "Índices", "tasas": "Tasas", "adrs": "ADRs argentinos (Nueva York)",
               "materias": "Materias primas", "cripto": "Cripto", "monedas": "Monedas"}
    for g, items in INSTRUMENTOS.items():
        for clave, nombre, _, tk, moneda, _ in items:
            if clave not in NO_VA:
                out.append((f"m-{clave}", nombre, tk, nombres.get(g, g), moneda))
    return out


def main():
    CARPETA.mkdir(exist_ok=True)
    indice, ok, fallas = [], 0, []
    previo = {x["id"]: x for x in json.loads((CARPETA / "indice.json").read_text(encoding="utf-8"))} if (CARPETA / "indice.json").exists() else {}
    for id_, nombre, tk, grupo, moneda in lista():
        try:
            velas, meta = bajar(tk)
            if len(velas) < 20:
                raise ValueError(f"solo {len(velas)} velas")
            hace = (datetime.now(timezone.utc) - datetime.fromisoformat(velas[-1][0]).replace(tzinfo=timezone.utc)).days
            datos = {"id": id_, "nombre": nombre, "ticker": tk, "grupo": grupo, "moneda": meta.get("currency") or moneda,
                     "actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"), "velas": velas}
            (CARPETA / f"{id_}.json").write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            ult, ant = velas[-1][4], velas[-2][4]
            indice.append({"id": id_, "nombre": nombre, "ticker": tk.replace(".BA", ""), "grupo": grupo, "moneda": datos["moneda"],
                           "ultimo": ult, "cambio": round((ult / ant - 1) * 100, 2), "fecha": velas[-1][0], "volumen": any(v[5] for v in velas[-60:])})
            ok += 1
            print(f"   · {id_:<16} {len(velas):>5} velas, hasta {velas[-1][0]}" + (f"  (⚠️ dato de hace {hace} días)" if hace > 5 else ""))
        except Exception as e:
            fallas.append(id_)
            print(f"   ⚠️  {id_} ({tk}): {str(e)[:90]}")
            if id_ in previo and (CARPETA / f"{id_}.json").exists():
                indice.append(previo[id_])          # queda el que estaba
        time.sleep(0.25)
    if ok == 0:
        raise SystemExit("❌ No se pudo bajar ninguna vela: no toco nada")
    (CARPETA / "indice.json").write_text(json.dumps(indice, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"✅ velas: {ok} instrumentos" + (f" · fallaron {len(fallas)}: {', '.join(fallas)}" if fallas else ""))


if __name__ == "__main__":
    print("Atlas Macro · velas para el análisis técnico")
    main()
