"""
bonos_ar.py — Atlas Macro

Cronograma de pagos de los bonos en dólares de la reestructuración de 2020 (Bonares y Globales)
y el cálculo de su rendimiento (TIR), duración y paridad a partir del precio.

Todos pagan el 9 de enero y el 9 de julio. Los montos son por cada 100 dólares de valor nominal
original (así cotizan en BYMA). El interés de cada semestre se calcula sobre lo que queda por
amortizar al empezar el semestre, con la tasa que rige en ese período (los cupones son escalonados).

En BYMA los bonos cotizan con los intereses corridos incluidos ("precio sucio"), así que la TIR
se calcula directo contra el precio.
"""

from datetime import date


def _fechas(desde, hasta):
    """Fechas de pago (9 de enero y 9 de julio) entre dos fechas, inclusive."""
    out, y = [], desde.year
    while True:
        for m in (1, 7):
            f = date(y, m, 9)
            if desde <= f <= hasta:
                out.append(f)
        if y > hasta.year:
            return out
        y += 1


def _cuotas(primera, ultima, total=100.0):
    """Amortización en cuotas semestrales iguales entre dos fechas."""
    fs = _fechas(primera, ultima)
    return {f: total / len(fs) for f in fs}


# tasa anual del cupón según el período: [(desde el pago de esta fecha en adelante, tasa %)]
CUPON_35 = [(date(2020, 9, 4), 0.125), (date(2021, 7, 9), 1.125), (date(2022, 7, 9), 1.5), (date(2023, 7, 9), 3.625),
            (date(2024, 7, 9), 4.125), (date(2027, 7, 9), 4.75), (date(2028, 7, 9), 5.0)]
CUPON_46 = [(date(2020, 9, 4), 0.125), (date(2021, 7, 9), 1.125), (date(2022, 7, 9), 1.5), (date(2023, 7, 9), 3.625),
            (date(2024, 7, 9), 4.125), (date(2027, 7, 9), 4.375), (date(2028, 7, 9), 5.0)]
BONOS = {
    "29": {"vence": date(2029, 7, 9), "amort": _cuotas(date(2025, 1, 9), date(2029, 7, 9)),
           "cupon": [(date(2020, 9, 4), 1.0)]},
    "30": {"vence": date(2030, 7, 9), "amort": {date(2024, 7, 9): 4.0, **_cuotas(date(2025, 1, 9), date(2030, 7, 9), 96.0)},
           "cupon": [(date(2020, 9, 4), 0.125), (date(2021, 7, 9), 0.5), (date(2023, 7, 9), 0.75), (date(2027, 7, 9), 1.75)]},
    "35": {"vence": date(2035, 7, 9), "amort": _cuotas(date(2031, 1, 9), date(2035, 7, 9)), "cupon": CUPON_35},
    "38": {"vence": date(2038, 1, 9), "amort": _cuotas(date(2027, 7, 9), date(2038, 1, 9)),
           "cupon": [(date(2020, 9, 4), 0.125), (date(2021, 7, 9), 2.0), (date(2022, 7, 9), 3.875), (date(2023, 7, 9), 4.25), (date(2024, 7, 9), 5.0)]},
    "41": {"vence": date(2041, 7, 9), "amort": _cuotas(date(2028, 1, 9), date(2041, 7, 9)),
           "cupon": [(date(2020, 9, 4), 0.125), (date(2021, 7, 9), 2.5), (date(2022, 7, 9), 3.5), (date(2029, 7, 9), 4.875)]},
    "46": {"vence": date(2046, 7, 9), "amort": _cuotas(date(2025, 1, 9), date(2046, 7, 9)), "cupon": CUPON_46},
}
# ticker → (serie, ley): los Bonares (AL, AE) son ley argentina; los Globales (GD), ley Nueva York
TICKERS = {"AL29": ("29", "Argentina"), "AL30": ("30", "Argentina"), "AL35": ("35", "Argentina"), "AE38": ("38", "Argentina"), "AL41": ("41", "Argentina"),
           "GD29": ("29", "Nueva York"), "GD30": ("30", "Nueva York"), "GD35": ("35", "Nueva York"), "GD38": ("38", "Nueva York"),
           "GD41": ("41", "Nueva York"), "GD46": ("46", "Nueva York")}


def _tasa(bono, inicio_periodo):
    t = bono["cupon"][0][1]
    for desde, tasa in bono["cupon"]:
        if inicio_periodo >= desde:
            t = tasa
    return t


def flujos(serie, hoy):
    """[(fecha, interés, amortización, residual antes del pago)] de los pagos que quedan, y el residual y los intereses corridos de hoy."""
    b = BONOS[serie]
    fechas = _fechas(date(2021, 1, 9), b["vence"])
    residual, anterior, out = 100.0, date(2020, 9, 4), []
    corrido, residual_hoy = 0.0, None
    for f in fechas:
        tasa = _tasa(b, anterior)
        interes = residual * tasa / 100 / 2 if anterior != date(2020, 9, 4) else residual * tasa / 100 * (f - anterior).days / 360
        am = b["amort"].get(f, 0.0)
        if f > hoy:
            if residual_hoy is None:
                residual_hoy = residual
                dias = _dias360(anterior, hoy)
                corrido = residual * tasa / 100 * dias / 360
            out.append((f, interes, am, residual))
        residual -= am
        anterior = f
    return out, (residual_hoy or 0.0), corrido


def _dias360(a, b):
    """Días entre dos fechas con la convención 30/360."""
    d1, d2 = min(a.day, 30), min(b.day, 30)
    return (b.year - a.year) * 360 + (b.month - a.month) * 30 + (d2 - d1)


def metricas(ticker, precio, hoy=None):
    """TIR (% anual efectiva), duración modificada (años), paridad (%), residual y valor técnico."""
    hoy = hoy or date.today()
    serie, ley = TICKERS[ticker]
    fl, residual, corrido = flujos(serie, hoy)
    if not fl or not precio:
        return None
    pagos = [((f - hoy).days / 365, i + a) for f, i, a, _ in fl]

    def vp(r):
        return sum(c / (1 + r) ** t for t, c in pagos)

    lo, hi = -0.5, 3.0                     # búsqueda por bisección: el precio baja cuando la tasa sube
    for _ in range(200):
        mid = (lo + hi) / 2
        if vp(mid) > precio:
            lo = mid
        else:
            hi = mid
    tir = (lo + hi) / 2
    dur = sum(t * c / (1 + tir) ** t for t, c in pagos) / vp(tir)
    tecnico = residual + corrido
    return {"ticker": ticker, "ley": ley, "vence": BONOS[serie]["vence"].isoformat(), "precio": precio,
            "tir": round(tir * 100, 2), "duracion": round(dur / (1 + tir / 2), 2),   # duración modificada con la convención semestral del mercado "paridad": round(precio / tecnico * 100, 1),
            "residual": round(residual, 2), "tecnico": round(tecnico, 2),
            "proximo": {"fecha": fl[0][0].isoformat(), "interes": round(fl[0][1], 3), "amort": round(fl[0][2], 3)}}
