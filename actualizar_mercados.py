"""
actualizar_mercados.py — Atlas Macro

Baja el precio de cierre de índices bursátiles, acciones argentinas en Wall Street
(ADRs), materias primas, cripto y monedas del mundo, y genera mercados.json con el último año de datos.

Fuentes: Stooq (stooq.com) y, si Stooq no tiene el dato o no responde, Yahoo Finance. El Merval sale
del servicio público de datos de BYMA (open.bymadata.com.ar), que está al día; Yahoo lo dejó de actualizar.
Además, de data912.com (precios de BYMA): el panel líder de acciones en pesos, los CEDEARs
más operados y los bonos en dólares, con su TIR, duración y paridad (calculadas en bonos_ar.py).
Los precios tienen unos 15-20 minutos de demora: no es una cotización en tiempo real.

Uso:
    python3 actualizar_mercados.py
"""

import csv
import io
import json
import sys
import time
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from curl_cffi import requests

import bonos_ar

CARPETA = Path(__file__).parent
ARCHIVO_SALIDA = CARPETA / "mercados.json"

URL_STOOQ = "https://stooq.com/q/d/l/?s={ticker}&i=d&d1={desde}&d2={hasta}"
URL_YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=2y&interval=1d"

# grupo → [(clave, nombre, ticker en Stooq, ticker en Yahoo, moneda, unidad)]
INSTRUMENTOS = {
    "indices": [
        ("sp500", "S&P 500", "^spx", "^GSPC", "USD", "puntos"),
        ("nasdaq", "Nasdaq", "^ndq", "^IXIC", "USD", "puntos"),
        ("merval", "Merval", "^mrv", "^MERV", "ARS", "puntos"),
        ("bovespa", "Bovespa", "^bvp", "^BVSP", "BRL", "puntos"),
        ("eurostoxx", "Euro Stoxx 50", "^stx", "^STOXX50E", "EUR", "puntos"),
        ("nikkei", "Nikkei 225", "^nkx", "^N225", "JPY", "puntos"),
        ("msciw", "MSCI Mundo (ETF URTH)", "urth.us", "URTH", "USD", "US$"),
        ("mscie", "MSCI Emergentes (ETF EEM)", "eem.us", "EEM", "USD", "US$"),
        ("vix", "VIX (índice del miedo)", "^vix", "^VIX", "USD", "puntos"),
    ],
    # tasas de los bonos del gobierno a 10 años: las variaciones van en puntos básicos, no en %
    "tasas": [
        ("t3m", "EE.UU. 3 meses", "13wusy.b", "^IRX", "USD", "tasa"),
        ("t5us", "EE.UU. 5 años", "5usy.b", "^FVX", "USD", "tasa"),
        ("t10us", "EE.UU. 10 años", "10usy.b", "^TNX", "USD", "tasa"),
        ("t30us", "EE.UU. 30 años", "30usy.b", "^TYX", "USD", "tasa"),
    ],
    "adrs": [
        ("ypf", "YPF", "ypf.us", "YPF", "USD", "US$"),
        ("vist", "Vista Energy", "vist.us", "VIST", "USD", "US$"),
        ("tgs", "Transportadora Gas del Sur", "tgs.us", "TGS", "USD", "US$"),
        ("pam", "Pampa Energía", "pam.us", "PAM", "USD", "US$"),
        ("cepu", "Central Puerto", "cepu.us", "CEPU", "USD", "US$"),
        ("edn", "Edenor", "edn.us", "EDN", "USD", "US$"),
        ("ggal", "Grupo Galicia", "ggal.us", "GGAL", "USD", "US$"),
        ("bma", "Banco Macro", "bma.us", "BMA", "USD", "US$"),
        ("bbar", "BBVA Argentina", "bbar.us", "BBAR", "USD", "US$"),
        ("supv", "Supervielle", "supv.us", "SUPV", "USD", "US$"),
        ("meli", "Mercado Libre", "meli.us", "MELI", "USD", "US$"),
        ("glob", "Globant", "glob.us", "GLOB", "USD", "US$"),
        ("teo", "Telecom Argentina", "teo.us", "TEO", "USD", "US$"),
        ("loma", "Loma Negra", "loma.us", "LOMA", "USD", "US$"),
        ("irs", "IRSA", "irs.us", "IRS", "USD", "US$"),
        ("cresy", "Cresud", "cresy.us", "CRESY", "USD", "US$"),
        ("caap", "Corporación América", "caap.us", "CAAP", "USD", "US$"),
    ],
    "materias": [
        ("soja", "Soja", "zs.f", "ZS=F", "USD", "US$ por tonelada"),
        ("maiz", "Maíz", "zc.f", "ZC=F", "USD", "US$ por tonelada"),
        ("trigo", "Trigo", "zw.f", "ZW=F", "USD", "US$ por tonelada"),
        ("brent", "Petróleo Brent", "cb.f", "BZ=F", "USD", "US$ por barril"),
        ("oro", "Oro", "gc.f", "GC=F", "USD", "US$ por onza"),
        ("cobre", "Cobre", "hg.f", "HG=F", "USD", "US$ por libra"),
    ],
    "cripto": [
        ("btc", "Bitcoin", "btcusd", "BTC-USD", "USD", "US$"),
        ("eth", "Ether", "ethusd", "ETH-USD", "USD", "US$"),
    ],
    # unidad "moneda:X" = cuántas X vale un dólar (o al revés para euro y libra, como se cotizan siempre)
    "monedas": [
        ("dxy", "Índice dólar (DXY)", "dx.f", "DX-Y.NYB", "USD", "puntos"),
        ("eur", "Euro", "eurusd", "EURUSD=X", "USD", "moneda:US$ por euro"),
        ("brl", "Real brasileño", "usdbrl", "BRL=X", "BRL", "moneda:reales por dólar"),
        ("clp", "Peso chileno", "usdclp", "CLP=X", "CLP", "moneda:pesos chilenos por dólar"),
        ("mxn", "Peso mexicano", "usdmxn", "MXN=X", "MXN", "moneda:pesos mexicanos por dólar"),
        ("cop", "Peso colombiano", "usdcop", "COP=X", "COP", "moneda:pesos colombianos por dólar"),
        ("jpy", "Yen", "usdjpy", "JPY=X", "JPY", "moneda:yenes por dólar"),
        ("cny", "Yuan", "usdcny", "CNY=X", "CNY", "moneda:yuanes por dólar"),
        ("gbp", "Libra esterlina", "gbpusd", "GBPUSD=X", "USD", "moneda:US$ por libra"),
    ],
}

# sector de cada acción argentina (para agruparlas en la página)
SECTOR_ADR = {"ypf": "Petróleo y gas", "vist": "Petróleo y gas", "tgs": "Petróleo y gas", "pam": "Electricidad", "cepu": "Electricidad",
              "edn": "Electricidad", "ggal": "Bancos", "bma": "Bancos", "bbar": "Bancos", "supv": "Bancos",
              "meli": "Tecnología y telecomunicaciones", "glob": "Tecnología y telecomunicaciones", "teo": "Tecnología y telecomunicaciones",
              "loma": "Construcción, inmuebles e infraestructura", "irs": "Construcción, inmuebles e infraestructura",
              "cresy": "Construcción, inmuebles e infraestructura", "caap": "Construcción, inmuebles e infraestructura"}

# ---- BYMA (data912.com): panel líder en pesos, CEDEARs y bonos ----
D912 = "https://data912.com/live/{}"
# panel líder (S&P Merval): ticker en BYMA → (nombre, sector)
PANEL = {
    "YPFD": ("YPF", "Petróleo y gas"), "PAMP": ("Pampa Energía", "Electricidad"), "TGSU2": ("TGS", "Petróleo y gas"),
    "TGNO4": ("Transportadora Gas del Norte", "Petróleo y gas"), "CEPU": ("Central Puerto", "Electricidad"), "EDN": ("Edenor", "Electricidad"),
    "TRAN": ("Transener", "Electricidad"), "GGAL": ("Grupo Galicia", "Bancos"), "BMA": ("Banco Macro", "Bancos"), "BBAR": ("BBVA Argentina", "Bancos"),
    "SUPV": ("Supervielle", "Bancos"), "VALO": ("Grupo Valores", "Bancos"), "BYMA": ("BYMA", "Bancos"), "TECO2": ("Telecom", "Tecnología y telecomunicaciones"),
    "ALUA": ("Aluar", "Industria y materiales"), "TXAR": ("Ternium Argentina", "Industria y materiales"), "LOMA": ("Loma Negra", "Construcción, inmuebles e infraestructura"),
    "IRSA": ("IRSA", "Construcción, inmuebles e infraestructura"), "CRES": ("Cresud", "Construcción, inmuebles e infraestructura"),
    "COME": ("Sociedad Comercial del Plata", "Industria y materiales"), "METR": ("Metrogas", "Petróleo y gas"), "CVH": ("Cablevisión Holding", "Tecnología y telecomunicaciones"),
}
# CEDEARs más operados: ticker → (nombre, sector)
CEDEARS = {
    "AAPL": ("Apple", "Tecnología"), "MSFT": ("Microsoft", "Tecnología"), "NVDA": ("Nvidia", "Tecnología"), "GOOGL": ("Alphabet (Google)", "Tecnología"),
    "AMZN": ("Amazon", "Tecnología"), "META": ("Meta (Facebook)", "Tecnología"), "TSLA": ("Tesla", "Tecnología"), "MELI": ("Mercado Libre", "Tecnología"),
    "XOM": ("ExxonMobil", "Energía"), "CVX": ("Chevron", "Energía"), "VIST": ("Vista Energy", "Energía"), "PBR": ("Petrobras", "Energía"),
    "JPM": ("JPMorgan", "Finanzas"), "BRKB": ("Berkshire Hathaway", "Finanzas"), "V": ("Visa", "Finanzas"),
    "KO": ("Coca-Cola", "Consumo"), "MCD": ("McDonald's", "Consumo"), "WMT": ("Walmart", "Consumo"), "PEP": ("PepsiCo", "Consumo"),
    "JNJ": ("Johnson & Johnson", "Salud"), "PFE": ("Pfizer", "Salud"), "LLY": ("Eli Lilly", "Salud"),
    "VALE": ("Vale", "Minería y oro"), "NEM": ("Newmont", "Minería y oro"), "B": ("Barrick", "Minería y oro"),
    "SPY": ("S&P 500 (SPY)", "Fondos"), "QQQ": ("Nasdaq 100 (QQQ)", "Fondos"), "DIA": ("Dow Jones (DIA)", "Fondos"), "EWZ": ("Brasil (EWZ)", "Fondos"),
    "IBIT": ("Bitcoin (IBIT)", "Fondos"), "ETHA": ("Ether (ETHA)", "Fondos"),
}


URL_CCL = "https://raw.githubusercontent.com/enzonotario/esjs-argentina-datos-api/main/datos/v1/cotizaciones/dolares/contadoconliqui/index.json"


def merval_en_dolares(salida, anterior):
    """Merval dividido por el dólar contado con liquidación de cada día: lo que hizo la bolsa medida en dólares."""
    merv = next((x for x in salida["grupos"].get("indices", []) if x["clave"] == "merval"), None)
    if not merv:
        return
    ccl = sorted((f["fecha"], f["venta"]) for f in sesion.get(URL_CCL, timeout=60).json() if f.get("venta"))
    # Merval completo (2 años) para calcular bien las variaciones: el de BYMA si llegó, si no Stooq o Yahoo
    datos = MERVAL_DATOS or de_stooq("^mrv") or []
    y = None if MERVAL_DATOS else de_yahoo("^MERV")
    if y and (not datos or y[-1][0] > datos[-1][0]):
        datos = y
    j, c, serie = 0, None, []
    for f, v in datos:
        while j < len(ccl) and ccl[j][0] <= f:
            c = ccl[j][1]
            j += 1
        if c:
            serie.append((f, v / c))
    if len(serie) < 30:
        return
    r = resumen(serie)
    i = salida["grupos"]["indices"].index(merv)
    salida["grupos"]["indices"].insert(i + 1, {"clave": "merval_ccl", "nombre": "Merval en dólares (CCL)", "ticker": "MERVAL/CCL", "moneda": "USD",
                                               "unidad": "puntos", "fuente": "Stooq/Yahoo y ArgentinaDatos", **r})
    print(f"     · Merval en dólares: {r['precio']:,.0f} ({r['fecha']}) · en el año {r['anio']}%")


def de_data912(nombre):
    """{ticker: fila} de una lista de data912 (precios de BYMA con demora)."""
    try:
        r = sesion.get(D912.format(nombre), timeout=40)
    except Exception:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            r = sesion.get(D912.format(nombre), timeout=40, verify=False)
    r.raise_for_status()
    return {x.get("symbol"): x for x in r.json()}


def parte_byma():
    """Panel líder en pesos, CEDEARs y bonos en dólares (con TIR) desde data912."""
    print("· BYMA (data912): panel líder, CEDEARs y bonos")
    salida = {}
    acc = de_data912("arg_stocks")
    salida["panel"] = [{"ticker": t, "nombre": n, "sector": sec, "precio": acc[t].get("c"), "dia": acc[t].get("pct_change"), "volumen": acc[t].get("v")}
                       for t, (n, sec) in PANEL.items() if t in acc and acc[t].get("c")]
    ced = de_data912("arg_cedears")
    salida["cedears"] = [{"ticker": t, "nombre": n, "sector": sec, "precio": ced[t].get("c"), "dia": ced[t].get("pct_change"), "volumen": ced[t].get("v"),
                          "usd": (ced.get(t + "D") or {}).get("c")}
                         for t, (n, sec) in CEDEARS.items() if t in ced and ced[t].get("c")]
    bon = de_data912("arg_bonds")
    salida["bonos"] = []
    for t in bonos_ar.TICKERS:
        fila = bon.get(t + "D")                                   # precio en dólares MEP
        if not fila or not fila.get("c"):
            continue
        m = bonos_ar.metricas(t, fila["c"])
        if m and 0 < m["tir"] < 60:
            m["dia"] = fila.get("pct_change")
            salida["bonos"].append(m)
    print(f"     · panel {len(salida['panel'])} · CEDEARs {len(salida['cedears'])} · bonos {len(salida['bonos'])}: "
          + ", ".join(f"{b['ticker']} {b['tir']:.1f}%" for b in salida["bonos"][:4]))
    return salida


# Los granos cotizan en centavos de dólar por bushel: los pasamos a dólares por tonelada
TONELADAS_POR_BUSHEL = {"soja": 0.0272155, "trigo": 0.0272155, "maiz": 0.0254012}

sesion = requests.Session(impersonate="chrome")


STOOQ_FALLAS = [0]
URL_BYMA_INDICE = "https://open.bymadata.com.ar/vanoms-be-core/rest/api/bymadata/free/chart/index-historical-series/history"
MERVAL_DATOS = []


def de_byma_indice(simbolo):
    """[(fecha, cierre), ...] de un índice de BYMA (M = S&P Merval), de su servicio público de datos (con demora)."""
    hasta = int(time.time())
    params = {"symbol": simbolo, "resolution": "D", "from": hasta - 730 * 86400, "to": hasta}
    try:
        try:
            r = sesion.get(URL_BYMA_INDICE, params=params, timeout=40)
        except Exception:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                r = sesion.get(URL_BYMA_INDICE, params=params, timeout=40, verify=False)
        d = r.json()
    except Exception:
        return None
    if d.get("s") != "ok" or not d.get("t"):
        return None
    datos = {}
    for t, c in zip(d["t"], d["c"]):
        if c:
            datos[datetime.fromtimestamp(t, timezone(timedelta(hours=-3))).strftime("%Y-%m-%d")] = c
    return sorted(datos.items()) or None


def de_stooq(ticker):
    """[(fecha, cierre), ...] desde Stooq, o None si no anduvo. Si falla 3 veces seguidas, no se insiste en esta corrida."""
    if STOOQ_FALLAS[0] >= 3:
        return None
    hasta = date.today()
    desde = hasta - timedelta(days=730)
    url = URL_STOOQ.format(ticker=ticker, desde=desde.strftime("%Y%m%d"), hasta=hasta.strftime("%Y%m%d"))
    try:
        r = sesion.get(url, timeout=40)
    except Exception:
        return None
    if r.status_code != 200 or not r.text.startswith("Date"):
        STOOQ_FALLAS[0] += 1
        return None                                  # Stooq responde "No data", un aviso de límite o una página en vez del CSV
    STOOQ_FALLAS[0] = 0
    filas = list(csv.DictReader(io.StringIO(r.text)))
    datos = [(f["Date"], float(f["Close"])) for f in filas if f.get("Close") not in (None, "", "null")]
    return datos or None


def de_yahoo(ticker):
    """[(fecha, cierre), ...] desde Yahoo Finance, o None si no anduvo."""
    try:
        r = sesion.get(URL_YAHOO.format(ticker=ticker), timeout=40)
        res = r.json()["chart"]["result"][0]
    except Exception:
        return None
    tiempos = res.get("timestamp") or []
    cierres = res["indicators"]["quote"][0].get("close") or []
    datos = [(datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d"), c) for t, c in zip(tiempos, cierres) if c is not None]
    return datos or None


def resumen(datos, tasa=False):
    """Último precio y variaciones: del día, del mes, del año y de 12 meses."""
    fechas = [f for f, _ in datos]
    ult_f, ult = datos[-1]
    anterior = datos[-2][1] if len(datos) > 1 else None

    def precio_al(dia):
        """Último cierre en o antes de esa fecha."""
        cand = [v for f, v in datos if f <= dia]
        return cand[-1] if cand else None

    fin = date.fromisoformat(ult_f)
    hace_mes = precio_al((fin - timedelta(days=30)).isoformat())
    fin_anio_pasado = precio_al(f"{fin.year - 1}-12-31")
    hace_anio = precio_al((fin - timedelta(days=365)).isoformat())
    if tasa:                                    # tasas: diferencia en puntos básicos (1 pb = 0,01 punto)
        var = lambda a: round((ult - a) * 100, 1) if a is not None else None
    else:
        var = lambda a: round((ult / a - 1) * 100, 2) if a else None
    # para el gráfico: último año, un punto por día hábil
    ultimo_anio = [(f, round(v, 4)) for f, v in datos if f >= (fin - timedelta(days=366)).isoformat()]
    return {
        "fecha": ult_f, "precio": round(ult, 4),
        "dia": var(anterior), "mes": var(hace_mes), "anio": var(fin_anio_pasado), "doce": var(hace_anio),
        "serie": ultimo_anio,
    }


def main():
    print("Atlas Macro · mercados")
    anterior = json.load(open(ARCHIVO_SALIDA, encoding="utf-8")) if ARCHIVO_SALIDA.exists() else {}
    salida = {"generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "fuente": "Stooq, Yahoo Finance y data912.com (precios de BYMA)", "grupos": {}}
    ok = falla = 0
    for grupo, lista in INSTRUMENTOS.items():
        salida["grupos"][grupo] = []
        for clave, nombre, t_stooq, t_yahoo, moneda, unidad in lista:
            datos, fuente = (de_byma_indice("M"), "BYMA") if clave == "merval" else (None, "")
            if datos:
                MERVAL_DATOS[:] = datos
            else:
                datos, fuente = de_stooq(t_stooq), "Stooq"
            viejo_stooq = datos and datos[-1][0] < (date.today() - timedelta(days=4)).isoformat()
            if not datos or viejo_stooq:                   # Stooq a veces deja de actualizar alguno (le pasó al Merval)
                y = de_yahoo(t_yahoo) if t_yahoo else None
                if y and (not datos or y[-1][0] > datos[-1][0]):
                    datos, fuente = y, "Yahoo"
            if not datos:
                falla += 1
                viejo = next((x for x in anterior.get("grupos", {}).get(grupo, []) if x["clave"] == clave), None)
                print(f"     ⚠️  {nombre}: no respondió ni Stooq ni Yahoo{' (dejo el dato anterior)' if viejo else ''}")
                if viejo:
                    salida["grupos"][grupo].append(viejo)
                continue
            if clave in TONELADAS_POR_BUSHEL and datos[-1][1] > 100:      # viene en centavos por bushel
                datos = [(f, v / 100 / TONELADAS_POR_BUSHEL[clave]) for f, v in datos]
            r = resumen(datos, tasa=unidad == "tasa")
            salida["grupos"][grupo].append({"clave": clave, "nombre": nombre, "ticker": t_yahoo or t_stooq.upper(), "moneda": moneda,
                                             "unidad": unidad, "fuente": fuente, **({"sector": SECTOR_ADR[clave]} if clave in SECTOR_ADR else {}), **r})
            ok += 1
            dia = f"{r['dia']:+.2f}%" if r["dia"] is not None else "—"
            print(f"     · {nombre:<16} {r['precio']:>14,.2f}  {dia:>8}  ({r['fecha']}, {fuente})")
            time.sleep(0.6)                                               # sin apurar a los servidores

    try:
        merval_en_dolares(salida, anterior)
    except Exception as e:
        print(f"     ⚠️  Merval en dólares: {str(e)[:100]}")
    try:
        salida["byma"] = parte_byma()
        salida["byma"]["generado"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    except Exception as e:
        print(f"     ❌ BYMA: {str(e)[:120]} (dejo el dato anterior)")
        if "byma" in anterior:
            salida["byma"] = anterior["byma"]
    if ok == 0:
        sys.exit("❌ No llegó ningún precio: dejo el mercados.json anterior")
    with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as archivo:
        json.dump(salida, archivo, ensure_ascii=False, separators=(",", ":"))
    kb = ARCHIVO_SALIDA.stat().st_size / 1024
    print(f"✅ mercados.json listo · {ok} instrumentos{f' · {falla} sin datos' if falla else ''} · {kb:,.0f} KB")


if __name__ == "__main__":
    main()
