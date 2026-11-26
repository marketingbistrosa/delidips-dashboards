"""
Trae los precios de Rappi y Pedidos Ya desde el reporte de Trade Marketing
(Firebase "trade-marketing-delidips", el de Sol) y los guarda en delivery.json
para que el dashboard los muestre en la pestaña Online.

Solo LEE la base de Sol. No modifica nada.

Uso:
  python delivery_update.py                      # lee Firestore (necesita FIREBASE_SA_JSON)
  python delivery_update.py --local datos.json   # prueba con un export local
"""
import json, os, re, sys, unicodedata
from datetime import datetime, timezone

CADENAS = {"rappi": "RAPPI", "pedidosya": "PEDIDOS YA"}

CATEGORIAS = {"salsas": "Salsas Kansas", "hummus": "Kyros Hummus", "panificados": "Panificados Kansas"}

# productoId del reporte de Sol -> (categoría, marca, producto) con los nombres del dashboard
PRODUCTOS = {
    "caesar": ("Salsas Kansas", "KANSAS", "CAESAR"),
    "cheddar": ("Salsas Kansas", "KANSAS", "CHEDDAR SAUCE"),
    "barbacoa": ("Salsas Kansas", "KANSAS", "BARBACOA"),
    "vinagreta": ("Salsas Kansas", "KANSAS", "VINAGRETA"),
    "chimichurri": ("Salsas Kansas", "KANSAS", "CHIMICHURRI"),
    "honey-mustard": ("Salsas Kansas", "KANSAS", "HONEY MUSTARD"),
    "aioli": ("Salsas Kansas", "KANSAS", "AIOLI SAUCE"),
    "american-ketchup": ("Salsas Kansas", "KANSAS", "KETCHUP AMERICAN"),
    "curry-ketchup": ("Salsas Kansas", "KANSAS", "KETCHUP CURRY"),
    "spicy-ketchup": ("Salsas Kansas", "KANSAS", "KETCHUP SPICY"),
    "h-clasico": ("Kyros Hummus", "KYROS", "CLÁSICO"),
    "h-pimenton": ("Kyros Hummus", "KYROS", "PIMENTÓN"),
    "h-oliva": ("Kyros Hummus", "KYROS", "CON ACEITE DE OLIVA"),
    "h-remolacha": ("Kyros Hummus", "KYROS", "REMOLACHA"),
    "h-ajo": ("Kyros Hummus", "KYROS", "AJO AHUMADO"),
    "h-porotos": ("Kyros Hummus", "KYROS", "POROTO NEGRO"),
    "babaganush": ("Kyros Hummus", "KYROS", "BABAGANUSH"),
    "pan-clasica": ("Panificados Kansas", "KANSAS", "PAN DE HAMBURGUESA CLÁSICO"),
    "pan-sesamo": ("Panificados Kansas", "KANSAS", "PAN DE HAMBURGUESA SÉSAMO"),
    "pan-queso": ("Panificados Kansas", "KANSAS", "PAN DE HAMBURGUESA QUESO"),
    "pan-pancho": ("Panificados Kansas", "KANSAS", "PAN DE PANCHO"),
    "pan-campero": ("Panificados Kansas", "KANSAS", "PAN DE CAMPO"),
}


def num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.-]", "", str(v))
    s = re.sub(r"\.(?=\d{3}(\D|$))", "", s).replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def limpiar_marca(m):
    m = str(m or "").strip()
    # "Khüne" mal escrito -> Kühne
    if unicodedata.normalize("NFD", m).encode("ascii", "ignore").decode().lower() in ("khune", "kuhne"):
        return "KÜHNE"
    return m.upper() or "SIN MARCA"


def fila(cadena, fecha, cat, marca, producto, lista, final, mecanica, propio):
    lista = lista or final
    final = final or lista
    if not lista:
        return None
    en_oferta = final < lista - 0.5
    return {
        "fecha": fecha,
        "canal": "ecommerce",
        "cadena": CADENAS[cadena],
        "categoría": cat,
        "marca": marca,
        "producto": producto,
        "precio_regular": round(lista),
        "precio_oferta": round(final) if en_oferta else "",
        "en_oferta": "SI" if en_oferta else "NO",
        "pct_descuento": round((1 - final / lista) * 100) if en_oferta else "",
        "dinámica": mecanica or "",
        "estado": "Visto",
        "es_kansas": "SI" if propio else "NO",
        "zona": "Online",
        "responsable": "Trade Marketing",
        "fuente": "trade-marketing",
    }


def convertir(clips, precios):
    filas = {}

    def agregar(f, clave):
        if f:
            filas[clave] = f  # si hay dos cargas del mismo producto el mismo día, queda la última

    for c in clips:
        cad = c.get("cadena")
        if cad not in CADENAS or not c.get("fecha"):
            continue
        for it in c.get("items") or []:
            lista, final = num(it.get("precioLista")), num(it.get("precioFinal"))
            pid = it.get("productoId")
            if it.get("propio"):
                if pid not in PRODUCTOS:  # combos y productos sin identificar: no se comparan
                    continue
                cat, marca, prod = PRODUCTOS[pid]
            else:
                if str(it.get("mecanica") or "").lower() == "combo":
                    continue
                cat = CATEGORIAS.get(it.get("categoria"))
                if not cat:
                    continue
                marca, prod = limpiar_marca(it.get("marca")), str(it.get("producto") or "").strip().upper()
                if not prod:
                    continue
            agregar(fila(cad, c["fecha"], cat, marca, prod, lista, final, it.get("mecanica"), it.get("propio")),
                    (cad, c["fecha"], marca, prod))

    for p in precios:  # precios cargados a mano en el reporte de Sol
        cad, pid = p.get("cadena"), p.get("productoId")
        if cad not in CADENAS or pid not in PRODUCTOS or not p.get("fecha"):
            continue
        cat, marca, prod = PRODUCTOS[pid]
        agregar(fila(cad, p["fecha"], cat, marca, prod, num(p.get("lista")), num(p.get("final")), p.get("mecanica"), True),
                (cad, p["fecha"], marca, prod))

    return sorted(filas.values(), key=lambda r: (r["fecha"], r["cadena"], r["marca"], r["producto"]))


def leer_firestore():
    from google.cloud import firestore
    from google.oauth2 import service_account

    info = json.loads(os.environ["FIREBASE_SA_JSON"])
    creds = service_account.Credentials.from_service_account_info(info)
    db = firestore.Client(project=info.get("project_id", "trade-marketing-delidips"), credentials=creds)
    clips = [d.to_dict() for d in db.collection("clips").where("cadena", "in", list(CADENAS)).stream()]
    precios = [d.to_dict() for d in db.collection("precios").where("cadena", "in", list(CADENAS)).stream()]
    return clips, precios


def main():
    if "--local" in sys.argv:
        d = json.load(open(sys.argv[sys.argv.index("--local") + 1], encoding="utf-8"))
        clips, precios = list((d.get("clips") or {}).values()), list((d.get("precios") or {}).values())
    else:
        clips, precios = leer_firestore()
    rows = convertir(clips, precios)
    out = {"status": "ok", "actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rows": rows}
    with open("delivery.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"{len(rows)} precios de Rappi / Pedidos Ya guardados en delivery.json")


if __name__ == "__main__":
    main()
