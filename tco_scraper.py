#!/usr/bin/env python3
"""Scraper del TCO (BCB). Descarga el fragmento HTML, lo parsea y actualiza los datos.

Uso:
  python tco_scraper.py               # descarga desde el BCB
  python tco_scraper.py respuesta.html  # parsea un HTML guardado (útil para pruebas)

Salidas (carpeta data/):
  bancos_largo.csv  -> fecha, entidad, metrica, valor (histórico acumulado, sin duplicados)
  tco.json          -> serie diaria del agregado: fecha, tco, monto, n_trans (para la página)
"""
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

# El endpoint responde con un GET simple, sin parámetros (el BCB define la ventana de fechas).
URL = "https://www.bcb.gob.bo/bcb_tco_publico_evolutivo.php"
METHOD = "GET"
PARAMS = {}
HEADERS = {"User-Agent": "Mozilla/5.0 (script de investigacion academica)"}

DATA = Path("data")
MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
         "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}


def num(txt):
    """'9,72' -> 9.72 ; '3.957.089' -> 3957089 ; '—' -> None"""
    t = txt.strip()
    if t in ("", "—", "–", "-"):
        return None
    return float(t.replace(".", "").replace(",", "."))


def fechas(cabeceras, inicio):
    """Las cabeceras vienen como '26-jun' (sin año): se infiere desde la fecha de corte inicial."""
    y, prev_m, out = inicio.year, inicio.month, []
    for h in cabeceras:
        d, m = h.strip().split("-")
        m = MESES[m.lower()[:3]]
        if m < prev_m:          # cambio de año (dic -> ene)
            y += 1
        prev_m = m
        out.append(date(y, m, int(d)))
    return out


def metrica(titulo):
    t = titulo.lower()
    if "compra" in t:
        return "tco_compra"
    if "monto" in t:
        return "monto_usd"
    if "transacciones" in t:
        return "n_transacciones"
    return None


def parse(html):
    soup = BeautifulSoup(html, "html.parser")
    filas, vigencia = [], None
    nota = soup.select_one(".bcb-vigencia-note")
    if nota:
        vigencia = nota.get_text(" ", strip=True)
    for tit in soup.select(".bcb-title"):
        texto = tit.get_text(" ", strip=True)
        met = metrica(texto)
        m = re.search(r"(\d{2})/(\d{2})/(\d{4})\s*→", texto)
        caja = tit.find_next_sibling("div", class_="bcb-scroller")
        if not (met and m and caja and caja.find("table")):
            continue
        inicio = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        tabla = caja.find("table")
        cab = [th.get_text(strip=True) for th in tabla.select("thead th")][1:]
        fch = fechas(cab, inicio)
        for tr in tabla.select("tbody tr"):
            celdas = [td.get_text(strip=True) for td in tr.find_all("td")]
            ent, vals = celdas[0], celdas[1:]
            if len(vals) != len(fch):
                raise ValueError(f"Columnas != fechas en {ent} ({met})")
            for f, v in zip(fch, vals):
                filas.append((f, ent, met, num(v)))
    df = pd.DataFrame(filas, columns=["fecha", "entidad", "metrica", "valor"])
    if df.empty or not df["entidad"].str.startswith("BANCOS (MEDIANA").any():
        raise RuntimeError("No se encontró la fila agregada del TCO: ¿cambió el formato?")
    return df, vigencia


def actualizar(nuevo):
    DATA.mkdir(exist_ok=True)
    nuevo["fecha"] = pd.to_datetime(nuevo["fecha"])
    p = DATA / "bancos_largo.csv"
    if p.exists():
        viejo = pd.read_csv(p, parse_dates=["fecha"])
        df = pd.concat([viejo, nuevo]).drop_duplicates(["fecha", "entidad", "metrica"], keep="last")
    else:
        df = nuevo
    df = df.sort_values(["fecha", "metrica", "entidad"])
    df.to_csv(p, index=False)

    agg = {"BANCOS (MEDIANA": "tco", "BANCOS (MONTOS": "monto", "BANCOS (NÚMERO": "n_trans"}
    piezas = []
    for pref, col in agg.items():
        s = df[df["entidad"].str.startswith(pref)].set_index("fecha")["valor"].rename(col)
        piezas.append(s)
    serie = pd.concat(piezas, axis=1).sort_index().reset_index()
    serie["fecha"] = serie["fecha"].dt.strftime("%Y-%m-%d")
    serie = serie.astype(object).where(serie.notna(), None)
    (DATA / "tco.json").write_text(json.dumps(serie.to_dict("records"), ensure_ascii=False))
    return df, serie


def descargar(intentos=3):
    ultimo = None
    for i in range(intentos):
        try:
            r = requests.request(METHOD, URL, headers=HEADERS,
                                 params=PARAMS if METHOD == "GET" else None,
                                 data=PARAMS if METHOD == "POST" else None, timeout=60)
            r.raise_for_status()
            r.encoding = "utf-8"
            return r.text
        except requests.RequestException as e:
            ultimo = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"No se pudo descargar el TCO: {ultimo}")


if __name__ == "__main__":
    html = Path(sys.argv[1]).read_text(encoding="utf-8") if len(sys.argv) > 1 else descargar()
    nuevo, vig = parse(html)
    _, serie = actualizar(nuevo)
    print(f"OK: {len(nuevo)} registros | {len(serie)} días | último: {serie.iloc[-1].to_dict()} | {vig}")
