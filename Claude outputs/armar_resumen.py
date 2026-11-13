"""
armar_resumen.py — Atlas Macro

Arma resumen.json: el resumen del día (y de la semana) que la portada muestra como stories.
No baja casi nada nuevo: lee los JSON que ya generan los otros scripts (mercados, argentina,
bcra, calendario) y elige qué contar.

Qué cuenta como "significativo": cada movimiento se mide contra lo que esa misma serie suele
moverse. Para cada instrumento con historia,

    z = variación de hoy / desvío de las variaciones de los últimos 20 días

Un 2% en un bono vale mucho más que un 2% en Bitcoin. Las placas eligen los |z| más altos.
Si nada pasa de |z| = 1,5, el resumen lo dice: "día tranquilo".

El riesgo país y las TIR de los bonos no traen historia diaria en los otros JSON, así que este
script guarda su propia historia corta en resumen_hist.json (los últimos 40 días).
"""

import json
import statistics
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

AQUI = Path(__file__).parent
AR = ZoneInfo("America/Argentina/Buenos_Aires")
URL_RIESGO = "https://raw.githubusercontent.com/enzonotario/esjs-argentina-datos-api/main/datos/v1/finanzas/indices/riesgo-pais/index.json"
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
UMBRAL = 1.5            # |z| desde el que un movimiento cuenta como "fuera de lo normal"
GLOBALES = ["GD30", "GD35", "GD38", "GD41", "GD46"]


def leer(nombre, defecto=None):
    try:
        return json.loads((AQUI / nombre).read_text(encoding="utf-8"))
    except Exception:
        return defecto


def num(x, d=1):
    """Número con coma decimal y punto de miles, como se escribe acá."""
    s = f"{x:,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fecha_txt(f, con_dia=True):
    d = date.fromisoformat(f) if isinstance(f, str) else f
    base = f"{d.day} de {MESES[d.month - 1]}"
    return f"{DIAS[d.weekday()]} {base}" if con_dia else base


# ---------- matemática de las series ----------

def cambios(serie, tasa=False):
    """Variaciones diarias: % para precios, puntos básicos para tasas."""
    out = []
    for (f0, v0), (f1, v1) in zip(serie, serie[1:]):
        if v0 is None or v1 is None or (not tasa and v0 == 0):
            continue
        out.append((f1, (v1 - v0) * 100 if tasa else (v1 / v0 - 1) * 100))
    return out


def analizar(inst, hoy_mercado):
    """Cambio del día, z, cambio de la semana y desde cuándo no se veía un movimiento así."""
    serie = [p for p in (inst.get("serie") or []) if p[1] is not None]
    if len(serie) < 25 or not inst.get("fecha"):
        return None
    if (hoy_mercado - date.fromisoformat(inst["fecha"])).days > 3:      # dato viejo (feriado allá, fuente caída)
        return None
    tasa = inst.get("unidad") == "tasa"
    if serie[-1][0] != inst["fecha"]:
        serie = serie + [[inst["fecha"], inst["precio"]]]
    cs = cambios(serie, tasa)
    if len(cs) < 21:
        return None
    hoy = cs[-1][1]
    previos = [c for _, c in cs[-21:-1]]
    sd = statistics.pstdev(previos) or None
    z = hoy / sd if sd else 0.0

    # desde cuándo no había un movimiento tan grande en esa dirección
    desde = None
    for f, c in reversed(cs[:-1]):
        if (hoy > 0 and c >= hoy) or (hoy < 0 and c <= hoy):
            desde = f
            break
    sesiones = len(cs) - 1 - next((i for i, (f, _) in enumerate(cs) if f == desde), len(cs) - 1) if desde else len(cs)

    # semana: contra el último cierre de la semana anterior
    lunes = hoy_mercado - timedelta(days=hoy_mercado.weekday())
    base = next((v for f, v in reversed(serie) if date.fromisoformat(f) < lunes), None)
    precio = serie[-1][1]
    semana = None
    if base:
        semana = (precio - base) * 100 if tasa else (precio / base - 1) * 100
    dias_sem = sum(1 for f, _ in serie if date.fromisoformat(f) >= lunes) or 1
    z_sem = semana / (sd * dias_sem ** 0.5) if (sd and semana is not None) else 0.0

    return {"clave": inst["clave"], "nombre": inst["nombre"], "precio": precio, "unidad": inst.get("unidad"),
            "tasa": tasa, "cambio": hoy, "z": z, "semana": semana, "z_sem": z_sem,
            "desde": desde if sesiones >= 20 else None, "sin_igual": desde is None}


# ---------- textos ----------

def verbo(c, tasa=False):
    if tasa:
        return "subió" if c > 0 else "bajó"
    return "subió" if c > 0 else "bajó"


def cambio_txt(c, tasa=False):
    return f"{num(abs(c), 0)} pb" if tasa else f"{num(abs(c), 1)}%"


def nota_desde(a):
    tipo = "suba" if a["cambio"] > 0 else "baja"
    if a["sin_igual"]:
        return f"su mayor {tipo} del último año"
    if a["desde"]:
        return f"su mayor {tipo} desde el {fecha_txt(a['desde'], False)}"
    return None


def fila(nombre, valor, cambio, tipo="pct", sub=None, destacado=False, inverso=False):
    return {"nombre": nombre, "valor": valor, "cambio": None if cambio is None else round(cambio, 2),
            "tipo": tipo, "sub": sub, "destacado": destacado, "inverso": inverso}


def valor_inst(a):
    u = a["unidad"] or ""
    if a["tasa"]:
        return f"{num(a['precio'], 2)}%"
    if u.startswith("moneda"):
        return num(a["precio"], 2 if a["precio"] < 20 else 1)
    if u.startswith("US$"):
        return f"US$ {num(a['precio'], 2 if a['precio'] < 1000 else 0)}"
    return num(a["precio"], 0 if a["precio"] >= 1000 else 2)


# ---------- armado ----------

def main():
    ahora = datetime.now(AR)
    m = leer("mercados.json", {})
    ar = leer("argentina.json", {})
    bcra = leer("bcra.json", {})
    cal = leer("calendario.json", {})
    hist = leer("resumen_hist.json", {}) or {}

    grupos = m.get("grupos", {})
    fechas = [x["fecha"] for g in grupos.values() for x in g if x.get("fecha")]
    hoy_m = max(date.fromisoformat(f) for f in fechas) if fechas else ahora.date()
    hoy_s = hoy_m.isoformat()

    an = {}
    for g, lista in grupos.items():
        for inst in lista:
            a = analizar(inst, hoy_m)
            if a:
                a["grupo"] = g
                an[a["clave"]] = a

    # riesgo país diario
    riesgo = []
    try:
        with urllib.request.urlopen(URL_RIESGO, timeout=30) as r:
            riesgo = [(x["fecha"], x["valor"]) for x in json.load(r)][-40:]
    except Exception as e:
        print(f"⚠️  riesgo país diario: {e}")
    if not riesgo:
        u = (ar.get("riesgo_pais") or {}).get("ultimo")
        riesgo = sorted({**{f: h["riesgo"] for f, h in hist.items() if h.get("riesgo")}, **({u["fecha"]: u["valor"]} if u else {})}.items())

    # historia propia: TIR y precio de los bonos, brecha
    bonos = {b["ticker"]: b for b in (m.get("byma") or {}).get("bonos", [])}
    hist[hoy_s] = {"tir": {t: b["tir"] for t, b in bonos.items()}, "precio": {t: b["precio"] for t, b in bonos.items()},
                   "riesgo": riesgo[-1][1] if riesgo else None}
    hist = dict(sorted(hist.items())[-40:])
    previas = [f for f in hist if f < hoy_s]
    ayer = hist[previas[-1]] if previas else None
    lunes_s = (hoy_m - timedelta(days=hoy_m.weekday())).isoformat()
    sem_prev = [f for f in hist if f < lunes_s]
    base_sem = hist[sem_prev[-1]] if sem_prev else None

    dia = []

    # 1. Portada del día
    merv, ccl_idx = an.get("merval"), an.get("merval_ccl")
    r_hoy = riesgo[-1] if riesgo else None
    r_ant = riesgo[-2] if len(riesgo) > 1 else None
    candidatos = [a for a in an.values() if a["clave"] not in ("merval_ccl",)]
    top = max(candidatos, key=lambda a: abs(a["z"]), default=None)
    grandes = []
    if merv:
        grandes.append(fila("Merval", num(merv["precio"], 0), merv["cambio"]))
    casas = ar.get("casas", {})
    ccl = casas.get("contadoconliqui")
    if ccl and ccl.get("antes", {}).get("dia"):
        v = ccl["ultimo"]["venta"]
        grandes.append(fila("Dólar CCL", f"$ {num(v, 0)}", (v / ccl["antes"]["dia"] - 1) * 100))
    if r_hoy:
        grandes.append(fila("Riesgo país", num(r_hoy[1], 0), (r_hoy[1] - r_ant[1]) if r_ant else None, "pb", inverso=True))
    if top and abs(top["z"]) >= UMBRAL:
        texto = (f"Lo más llamativo: {top['nombre']} {verbo(top['cambio'])} {cambio_txt(top['cambio'], top['tasa'])}, "
                 f"{num(abs(top['z']), 1)} veces lo que se mueve en un día normal.")
        titulo = "Un día movido" if sum(abs(a["z"]) >= 2 for a in candidatos) >= 3 else "El día en 30 segundos"
    else:
        texto = "Día tranquilo: nada se movió fuera de lo normal."
        titulo = "El día en 30 segundos"
    dia.append({"id": "hoy", "circulo": "Hoy", "kicker": fecha_txt(hoy_m).capitalize(), "titulo": titulo,
                "texto": texto, "grandes": grandes, "link": "#mercados", "link_txt": "Ver Mercados"})

    # 2. Acciones
    panel = [p for p in (m.get("byma") or {}).get("panel", []) if p.get("dia") is not None]
    if panel:
        orden = sorted(panel, key=lambda p: p["dia"], reverse=True)
        suben = sum(p["dia"] > 0 for p in panel)
        bajan = sum(p["dia"] < 0 for p in panel)
        titulo = "Día verde en la bolsa" if suben > bajan * 1.5 else "Día rojo en la bolsa" if bajan > suben * 1.5 else "Bolsa mixta"
        filas = [fila(p["nombre"], f"$ {num(p['precio'], 0 if p['precio'] >= 100 else 2)}", p["dia"], sub=p["ticker"]) for p in orden[:3]]
        filas += [fila(p["nombre"], f"$ {num(p['precio'], 0 if p['precio'] >= 100 else 2)}", p["dia"], sub=p["ticker"]) for p in orden[-3:][::-1]]
        adrs = [a for a in an.values() if a["grupo"] == "adrs"]
        texto = f"{suben} de {len(panel)} acciones del panel líder subieron y {bajan} bajaron."
        nota = None
        if adrs:
            fuerte = max(adrs, key=lambda a: abs(a["z"]))
            nd = nota_desde(fuerte)
            nota = (f"En Nueva York, {fuerte['nombre']} {verbo(fuerte['cambio'])} {cambio_txt(fuerte['cambio'])}"
                    + (f", {nd}." if nd and abs(fuerte["z"]) >= UMBRAL else "."))
        dia.append({"id": "acciones", "circulo": "Acciones", "kicker": "Panel líder en BYMA", "titulo": titulo, "texto": texto,
                     "filas": filas, "nota": nota, "link": "#mercados", "link_txt": "Ver el mapa del mercado"})

    # 3. Bonos
    glob = [bonos[t] for t in GLOBALES if t in bonos]
    if glob:
        filas = []
        for b in glob:
            d_tir = (b["tir"] - ayer["tir"][b["ticker"]]) * 100 if ayer and b["ticker"] in ayer.get("tir", {}) else None
            filas.append(fila(b["ticker"], f"{num(b['tir'], 1)}%", d_tir if d_tir is not None else b.get("dia"),
                              "pb" if d_tir is not None else "pct", sub=f"vence {date.fromisoformat(b['vence']).year}",
                              inverso=d_tir is not None))
        corto, largo = glob[0], glob[-1]
        pend = largo["tir"] - corto["tir"]
        texto = f"El {corto['ticker']} rinde {num(corto['tir'], 1)}% y el {largo['ticker']} {num(largo['tir'], 1)}%: "
        if pend > 0.2:
            texto += f"prestarle al país a más largo plazo rinde {num(pend, 1)} puntos más."
        elif pend < -0.2:
            texto += f"la curva está invertida, los bonos cortos rinden {num(-pend, 1)} puntos más que los largos."
        else:
            texto += "la curva está casi plana."
        if ayer and all(t in ayer.get("tir", {}) for t in (corto["ticker"], largo["ticker"])):
            pend_ant = ayer["tir"][largo["ticker"]] - ayer["tir"][corto["ticker"]]
            dp = (pend - pend_ant) * 100
            titulo = "La curva se empinó" if dp > 5 else "La curva se aplanó" if dp < -5 else "La curva, sin cambios grandes"
            sub_tit = "Las columnas muestran cuánto cambió la TIR (en puntos básicos)."
        else:
            prom = statistics.mean(b["dia"] for b in glob if b.get("dia") is not None)
            titulo = "Los bonos subieron" if prom > 0.3 else "Los bonos bajaron" if prom < -0.3 else "Bonos estables"
            sub_tit = "Las columnas muestran cuánto cambió el precio en el día."
        grandes = [fila("Riesgo país", num(r_hoy[1], 0), (r_hoy[1] - r_ant[1]) if r_ant else None, "pb", inverso=True)] if r_hoy else []
        dia.append({"id": "bonos", "circulo": "Bonos", "kicker": "Bonos en dólares (Globales)", "titulo": titulo, "texto": texto,
                    "grandes": grandes, "filas": filas, "nota": sub_tit, "link": "#mercados", "link_txt": "Ver la curva"})

    # 4. Dólar y BCRA
    nombres = [("oficial", "Oficial"), ("bolsa", "MEP"), ("contadoconliqui", "CCL"), ("blue", "Blue")]
    en_texto = {"Oficial": "oficial", "MEP": "MEP", "CCL": "contado con liqui", "Blue": "blue"}
    grandes, movs = [], []
    for k, n in nombres:
        c = casas.get(k)
        if not c:
            continue
        v, a0 = c["ultimo"]["venta"], (c.get("antes") or {}).get("dia")
        ch = (v / a0 - 1) * 100 if a0 else None
        grandes.append(fila(n, f"$ {num(v, 0)}", ch))
        if ch is not None:
            movs.append((abs(ch), n, ch))
    if grandes:
        filas = []
        of, cl = casas.get("oficial"), casas.get("contadoconliqui")
        if of and cl:
            brecha = (cl["ultimo"]["venta"] / of["ultimo"]["venta"] - 1) * 100
            filas.append(fila("Brecha CCL vs. oficial", f"{num(brecha, 1)}%", None))
        compras = bcra.get("compras") or []
        if compras and (hoy_m - date.fromisoformat(compras[-1][0])).days <= 4:      # si el dato es viejo, no lo mostramos
            f, v = compras[-1]
            filas.append(fila("Compras del BCRA", f"US$ {num(v, 0)} M", None, sub=fecha_txt(f, False)))
        res = bcra.get("reservas") or []
        if len(res) > 1:
            (f1, v1), (_, v0) = res[-1], res[-2]
            filas.append(fila("Reservas brutas", f"US$ {num(v1, 0)} M", None, sub=f"{'+' if v1 >= v0 else '−'}US$ {num(abs(v1 - v0), 0)} M en el día"))
        big = max(movs, default=None)
        if big and big[0] >= 0.3:
            titulo = f"El dólar {en_texto[big[1]]} {verbo(big[2])} {cambio_txt(big[2])}"
        else:
            titulo = "Dólar quieto"
        dia.append({"id": "dolar", "circulo": "Dólar", "kicker": "Dólar y BCRA", "titulo": titulo, "grandes": grandes,
                    "filas": filas, "link": "#bcra", "link_txt": "Ver Dólar y BCRA"})

    # 5. Mundo
    afuera = [a for a in an.values() if a["grupo"] in ("indices", "tasas", "materias", "cripto", "monedas")
              and a["clave"] not in ("merval", "merval_ccl")]
    if afuera:
        orden = sorted(afuera, key=lambda a: abs(a["z"]), reverse=True)[:6]
        filas = [fila(a["nombre"], valor_inst(a), a["cambio"], "pb" if a["tasa"] else "pct",
                      sub=("fuera de lo normal" if abs(a["z"]) >= 2 else None), destacado=abs(a["z"]) >= 2,
                      inverso=a["tasa"]) for a in orden]
        t = orden[0]
        titulo = (f"Afuera, lo que más se movió: {t['nombre']}" if abs(t["z"]) >= UMBRAL else "Afuera, un día tranquilo")
        sp = an.get("sp500")
        texto = f"El S&P 500 {verbo(sp['cambio'])} {cambio_txt(sp['cambio'])}." if sp else None
        dia.append({"id": "mundo", "circulo": "Mundo", "kicker": "Mercados del mundo", "titulo": titulo, "texto": texto,
                    "filas": filas, "nota": "Ordenados por cuánto se salieron de lo normal, no por el tamaño del movimiento.",
                    "link": "#mercados", "link_txt": "Ver todos los mercados"})

    # 6. Datos publicados hoy (INDEC)
    hoy_cal = [e for e in cal.get("eventos", []) if e.get("fecha") == ahora.date().isoformat()]
    if hoy_cal:
        grandes = []
        for e in hoy_cal:
            if e.get("tema") == "inflacion":
                infl = (ar.get("inflacion") or {}).get("mensual") or []
                try:
                    mes, anio = e["periodo"].split(" de ")
                    clave = f"{anio}-{MESES.index(mes) + 1:02d}"
                    v = next(v for f, v in infl if f == clave)
                    grandes.append(fila(f"Inflación de {mes}", f"{num(v, 1)}%", None))
                except Exception:
                    pass
        filas = [fila(e["titulo"], "", None, sub=e.get("periodo")) for e in hoy_cal]
        dia.append({"id": "dato", "circulo": "Datos", "kicker": "Publicado hoy por el INDEC",
                    "titulo": "Salieron datos nuevos" if len(hoy_cal) > 1 else hoy_cal[0]["titulo"],
                    "grandes": grandes, "filas": filas, "link": "#calendario", "link_txt": "Ver el calendario"})

    # ---------- la semana ----------
    semana = []
    grandes = []
    if merv and merv["semana"] is not None:
        grandes.append(fila("Merval", num(merv["precio"], 0), merv["semana"]))
    if ccl and ccl.get("antes", {}).get("semana"):
        grandes.append(fila("Dólar CCL", f"$ {num(ccl['ultimo']['venta'], 0)}", (ccl["ultimo"]["venta"] / ccl["antes"]["semana"] - 1) * 100))
    r_lunes = next((v for f, v in reversed(riesgo) if f < lunes_s), None)
    if r_hoy and r_lunes:
        grandes.append(fila("Riesgo país", num(r_hoy[1], 0), r_hoy[1] - r_lunes, "pb", inverso=True))
    con_sem = [a for a in an.values() if a["semana"] is not None and a["clave"] != "merval_ccl"]
    top_s = max(con_sem, key=lambda a: abs(a["z_sem"]), default=None)
    texto = (f"Lo que más se salió de lo normal: {top_s['nombre']}, que {verbo(top_s['semana'])} "
             f"{cambio_txt(top_s['semana'], top_s['tasa'])} en la semana." if top_s and abs(top_s["z_sem"]) >= UMBRAL
             else "Una semana sin sobresaltos.")
    semana.append({"id": "sem", "circulo": "Semana", "kicker": f"Semana del {fecha_txt(lunes_s, False)}",
                   "titulo": "La semana en 30 segundos", "texto": texto, "grandes": grandes})
    adrs = sorted([a for a in con_sem if a["grupo"] == "adrs"], key=lambda a: a["semana"], reverse=True)
    if adrs:
        filas = [fila(a["nombre"], valor_inst(a), a["semana"]) for a in adrs[:3] + adrs[-3:][::-1]]
        semana.append({"id": "sem-acc", "circulo": "Acciones", "kicker": "Acciones argentinas en Nueva York",
                       "titulo": "Las que más subieron y bajaron", "filas": filas, "link": "#mercados", "link_txt": "Ver Mercados"})
    if glob and base_sem:
        filas = [fila(b["ticker"], f"{num(b['tir'], 1)}%", (b["tir"] - base_sem["tir"][b["ticker"]]) * 100, "pb",
                      sub=f"vence {date.fromisoformat(b['vence']).year}", inverso=True)
                 for b in glob if b["ticker"] in base_sem.get("tir", {})]
        if filas:
            semana.append({"id": "sem-bonos", "circulo": "Bonos", "kicker": "Bonos en dólares (Globales)",
                           "titulo": "Cómo cambió la TIR en la semana", "filas": filas, "link": "#mercados", "link_txt": "Ver la curva"})
    afuera_s = sorted([a for a in con_sem if a["grupo"] in ("indices", "tasas", "materias", "cripto", "monedas")
                       and a["clave"] != "merval"], key=lambda a: abs(a["z_sem"]), reverse=True)[:6]
    if afuera_s:
        filas = [fila(a["nombre"], valor_inst(a), a["semana"], "pb" if a["tasa"] else "pct",
                      sub=("fuera de lo normal" if abs(a["z_sem"]) >= 2 else None), destacado=abs(a["z_sem"]) >= 2,
                      inverso=a["tasa"]) for a in afuera_s]
        semana.append({"id": "sem-mundo", "circulo": "Mundo", "kicker": "Mercados del mundo",
                       "titulo": "Lo que más se movió en la semana", "filas": filas, "link": "#mercados", "link_txt": "Ver Mercados"})

    out = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"), "fecha": hoy_s,
           "hora": ahora.strftime("%H:%M"), "dia": dia, "semana": semana}
    (AQUI / "resumen.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    (AQUI / "resumen_hist.json").write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")
    print(f"✅ resumen.json: {len(dia)} placas del día, {len(semana)} de la semana")
    for s in dia + semana:
        print(f"   · {s['circulo']:<9} {s['titulo']}")


if __name__ == "__main__":
    main()
