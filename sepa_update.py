#!/usr/bin/env python3
"""Actualiza data/sepa_diario.csv con los precios SEPA (Precios Claros) de nuestros
productos y de la competencia.

Uso:
  python scripts/sepa_update.py --download        # baja los ZIP nuevos del portal (lo usa GitHub Actions)
  python scripts/sepa_update.py archivo.zip ...    # procesa ZIP ya descargados

Solo guarda un resumen diario por cadena y producto (mediana entre sucursales),
así el archivo queda liviano.
"""
import csv, io, json, os, statistics, sys, tempfile, urllib.request, zipfile, datetime as dt
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))  # archivos en la raiz del repo
EANS = os.path.join(ROOT, 'sepa_eans.csv')
OUT = os.path.join(ROOT, 'sepa_diario.csv')
STATE = os.path.join(ROOT, 'sepa_estado.json')

BASE = 'https://datos.produccion.gob.ar/dataset/6f47ec76-d1ce-4e34-a7e1-621fe9b1d0b5/resource/'
RECURSOS = {
    'lunes': '0a9069a9-06e8-4f98-874d-da5578693290/download/sepa_lunes.zip',
    'martes': '9dc06241-cc83-44f4-8e25-c9b1636b8bc8/download/sepa_martes.zip',
    'miercoles': '1e92cd42-4f94-4071-a165-62c4cb2ce23c/download/sepa_miercoles.zip',
    'jueves': 'd076720f-a7f0-4af8-b1d6-1b99d5a90c14/download/sepa_jueves.zip',
    'viernes': '91bc072a-4726-44a1-85ec-4a8467aad27e/download/sepa_viernes.zip',
    'sabado': 'b3c3da5d-213d-41e7-8d74-f23fda0a3c30/download/sepa_sabado.zip',
    'domingo': 'f8e75128-515a-436e-bf8d-5c63a62f2005/download/sepa_domingo.zip',
}
COLS = ['fecha', 'cadena', 'EAN', 'precio_lista', 'precio_promo', 'sucursales', 'sucursales_promo']


def grupo_cadena(nombre):
    n = (nombre or '').strip()
    u = n.upper()
    if n in ('Market', 'Express', 'Maxi', 'Hipermercado Carrefour') or 'CARREFOUR' in u: return 'Carrefour'
    if 'DIA' in u.split() or 'SUPERMERCADOS DIA' in u: return 'DIA'
    if 'CHANGO' in u: return 'Changomás'
    if 'COTO' in u: return 'Coto'
    if 'JUMBO' in u: return 'Jumbo'
    if 'DISCO' in u: return 'Disco'
    if u == 'VEA' or u.startswith('VEA '): return 'Vea'
    return n


def leer_eans():
    with open(EANS, encoding='utf-8') as f:
        return {r['EAN'].lstrip('0') for r in csv.DictReader(f)}


def num(x):
    try:
        v = float(str(x).replace(',', '.'))
        return v if v > 0 else None
    except Exception:
        return None


def procesar_zip(path, eans):
    """Devuelve {fecha: filas_resumen} de un ZIP diario de SEPA."""
    precios = defaultdict(lambda: {'lista': [], 'promo': [], 'n_promo': 0})
    fechas = set()
    with zipfile.ZipFile(path) as outer:
        for info in outer.infolist():
            if not info.filename.endswith('.zip') or info.file_size == 0:
                continue
            fecha = info.filename.split('/')[0]
            try:
                dt.date.fromisoformat(fecha)
            except ValueError:
                fecha = info.filename.split('_')[-2] if '_' in info.filename else None
            if not fecha:
                continue
            fechas.add(fecha)
            try:
                inner = zipfile.ZipFile(io.BytesIO(outer.read(info)))
            except Exception as e:
                print(f'  ! no se pudo abrir {info.filename}: {e}')
                continue
            names = {os.path.basename(n): n for n in inner.namelist()}
            if 'productos.csv' not in names or 'comercio.csv' not in names:
                continue
            banderas = {}
            with inner.open(names['comercio.csv']) as fh:
                for r in csv.DictReader(io.TextIOWrapper(fh, encoding='utf-8-sig', errors='replace'), delimiter='|'):
                    if r.get('id_bandera'):
                        banderas[(r.get('id_comercio'), r['id_bandera'])] = r.get('comercio_bandera_nombre', '')
            with inner.open(names['productos.csv']) as fh:
                for r in csv.DictReader(io.TextIOWrapper(fh, encoding='utf-8-sig', errors='replace'), delimiter='|'):
                    ean = (r.get('id_producto') or '').strip().lstrip('0')
                    if ean not in eans:
                        continue
                    lista = num(r.get('productos_precio_lista'))
                    if not lista:
                        continue
                    cad = grupo_cadena(banderas.get((r.get('id_comercio'), r.get('id_bandera')), r.get('id_comercio')))
                    k = (fecha, cad, ean)
                    precios[k]['lista'].append(lista)
                    promo = num(r.get('productos_precio_unitario_promo1'))
                    if promo and promo < lista * 0.99:
                        precios[k]['promo'].append(promo)
                        precios[k]['n_promo'] += 1
    filas = []
    for (fecha, cad, ean), v in precios.items():
        filas.append({
            'fecha': fecha, 'cadena': cad, 'EAN': ean,
            'precio_lista': round(statistics.median(v['lista']), 2),
            'precio_promo': round(statistics.median(v['promo']), 2) if v['promo'] else '',
            'sucursales': len(v['lista']), 'sucursales_promo': v['n_promo'],
        })
    return fechas, filas


def guardar(fechas, filas):
    existentes = []
    if os.path.exists(OUT):
        with open(OUT, encoding='utf-8') as f:
            existentes = [r for r in csv.DictReader(f) if r['fecha'] not in fechas]
    todas = existentes + filas
    todas.sort(key=lambda r: (r['fecha'], r['cadena'], r['EAN']))
    with open(OUT, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(todas)
    dias = sorted({r['fecha'] for r in todas})
    print(f'  -> {len(filas)} filas nuevas; histórico: {len(todas)} filas, {len(dias)} días ({dias[0]} a {dias[-1]})')


def descargar_nuevos(eans):
    estado = json.load(open(STATE)) if os.path.exists(STATE) else {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,application/zip,*/*;q=0.8',
        'Accept-Language': 'es-AR,es;q=0.9',
        'Referer': 'https://datos.produccion.gob.ar/dataset/sepa-precios',
    }
    for dia, ruta in RECURSOS.items():
        url = BASE + ruta
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=900)
        except Exception as e:
            print(f'{dia}: no se pudo descargar ({e})')
            continue
        mod = r.headers.get('Last-Modified', '')
        if mod and estado.get(dia) == mod:
            print(f'{dia}: sin cambios ({mod})')
            r.close()
            continue
        print(f'{dia}: descargando ({mod})')
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
            try:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    tmp.write(chunk)
            except Exception as e:
                print(f'{dia}: error al descargar ({e})')
                continue
            finally:
                r.close()
        try:
            fechas, filas = procesar_zip(tmp.name, eans)
            if filas:
                guardar(fechas, filas)
            estado[dia] = mod
        finally:
            os.unlink(tmp.name)
    json.dump(estado, open(STATE, 'w'), indent=1, ensure_ascii=False)

if __name__ == '__main__':
    eans = leer_eans()
    args = sys.argv[1:]
    if not args or args == ['--download']:
        descargar_nuevos(eans)
    else:
        for p in args:
            print(p)
            fechas, filas = procesar_zip(p, eans)
            guardar(fechas, filas)
