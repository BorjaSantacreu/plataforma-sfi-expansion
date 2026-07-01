"""
EEMM · Monitor VPP — Ingesta BOE (enajenaciones patrimoniales, subastas, concesiones inmobiliarias).
Flujo:
  1. Recorre los sumarios diarios del BOE (Sección V: Anuncios) de los últimos N días.
  2. Filtra entradas por keywords inmobiliarios en el título.
  3. Para cada candidato, descarga el XML completo y extrae datos.
  4. Clasifica y puntúa con las mismas reglas que PLACSP.
  5. Upsert en Azure PostgreSQL (misma tabla vpp_licitaciones, fuente='BOE').
Variables de entorno:
  AZURE_PG_CONN      postgresql://usuario:pass@host:5432/postgres?sslmode=require
  BOE_LOOKBACK_DAYS  (def. 35)
"""
import os
import re
import sys
import time
import datetime as dt
import requests
import psycopg2
import psycopg2.extras

# Importar clasificador compartido
from classify import es_candidato, clasificar

PG_CONN = os.environ.get("AZURE_PG_CONN", "")
LOOKBACK_DAYS = int(os.environ.get("BOE_LOOKBACK_DAYS", os.environ.get("LOOKBACK_DAYS", "35")))
BOE_API = "https://www.boe.es/datosabiertos/api"


def _pg():
    return psycopg2.connect(PG_CONN)


def _adapt(v):
    if isinstance(v, (dict, list)):
        return psycopg2.extras.Json(v)
    return v

# Keywords para filtrar títulos de la Sección V del BOE
KW_INMUEBLE = [
    "enajenación", "enajenacion", "subasta", "concesión", "concesion",
    "parcela", "solar", "suelo", "inmueble", "bien inmueble", "bienes inmuebles",
    "vivienda", "viviendas", "residencial", "edifici", "finca",
    "derecho de superficie", "permuta", "venta de terreno", "alienación",
    "aprovechamiento urbanístico", "aprovechamiento urbanistico",
    "vpo", "vpp", "protegida", "protegidas", "asequible",
]
# Mapeamos departamento → provincia/CCAA (best-effort por nombre del organismo)
CCAA_KEYWORDS = {
    "Andalucía": ["andaluc", "sevilla", "málaga", "malaga", "córdoba", "cordoba", "granada", "jaén", "jaen", "almería", "almeria", "huelva", "cádiz", "cadiz"],
    "C. Valenciana": ["valencian", "valencia", "alicante", "castellón", "castellon"],
    "Cataluña": ["cataluñ", "catalun", "barcelona", "tarragona", "girona", "lleida"],
    "Madrid": ["madrid"],
    "País Vasco": ["país vasco", "pais vasco", "euskadi", "vizcaya", "guipúzcoa", "álava"],
    "Islas Baleares": ["balear", "mallorca", "ibiza", "menorca"],
    "Islas Canarias": ["canari", "tenerife", "gran canaria", "las palmas"],
    "Aragón": ["aragón", "aragon", "zaragoza", "huesca", "teruel"],
    "Castilla y León": ["castilla y león", "castilla y leon", "valladolid", "burgos", "salamanca", "león", "leon", "palencia", "segovia", "soria", "ávila", "avila", "zamora"],
    "Castilla-La Mancha": ["castilla-la mancha", "castilla la mancha", "toledo", "ciudad real", "albacete", "cuenca", "guadalajara"],
    "Murcia": ["murcia"],
    "Galicia": ["galicia", "coruña", "vigo", "pontevedra", "lugo", "ourense"],
    "Navarra": ["navarra", "pamplona"],
    "Asturias": ["asturias", "oviedo", "gijón", "gijon"],
    "Cantabria": ["cantabria", "santander"],
    "La Rioja": ["rioja", "logroño"],
    "Extremadura": ["extremadura", "badajoz", "cáceres", "caceres"],
}


def detect_ccaa(text):
    t = text.lower()
    for ccaa, kws in CCAA_KEYWORDS.items():
        for kw in kws:
            if kw in t:
                return ccaa
    return None


def detect_municipio(text):
    """Intenta extraer municipio del título o departamento."""
    m = re.search(r"[Aa]yuntamiento\s+de\s+([A-ZÁÉÍÓÚÑa-záéíóúñ\s\-]+?)(?:\.|,|$)", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:Diputación|Gobierno|Junta)\s+(?:de|del)\s+([A-ZÁÉÍÓÚÑa-záéíóúñ\s\-]+?)(?:\.|,|$)", text)
    if m:
        return m.group(1).strip()
    return None


def detect_provincia(text):
    """Best-effort: busca nombre de provincia en el texto."""
    provincias = [
        "A Coruña", "Álava", "Albacete", "Alicante", "Almería", "Asturias", "Ávila",
        "Badajoz", "Barcelona", "Bizkaia", "Burgos", "Cáceres", "Cádiz", "Cantabria",
        "Castellón", "Ciudad Real", "Córdoba", "Cuenca", "Gipuzkoa", "Girona", "Granada",
        "Guadalajara", "Huelva", "Huesca", "Illes Balears", "Jaén", "La Rioja", "Las Palmas",
        "León", "Lleida", "Lugo", "Madrid", "Málaga", "Murcia", "Navarra", "Ourense",
        "Palencia", "Pontevedra", "Salamanca", "Santa Cruz de Tenerife", "Segovia", "Sevilla",
        "Soria", "Tarragona", "Teruel", "Toledo", "Valencia", "Valladolid", "Zamora", "Zaragoza",
    ]
    t = text.lower()
    for p in provincias:
        if p.lower() in t:
            return p
    return None


def extract_importe(text):
    """Busca importes en euros en el texto."""
    matches = re.findall(r"(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?)\s*(?:euros|€|EUR)", text, re.IGNORECASE)
    if matches:
        importes = []
        for m in matches:
            try:
                importes.append(float(m.replace(".", "").replace(",", ".")))
            except ValueError:
                pass
        return max(importes) if importes else None
    return None


def fetch_sumario(fecha_str):
    """Descarga el sumario del BOE de una fecha (YYYYMMDD)."""
    url = f"{BOE_API}/boe/sumario/{fecha_str}"
    try:
        r = requests.get(url, headers={"Accept": "application/json"}, timeout=30)
        if r.status_code == 404:
            return None  # No hay BOE ese día (festivo, fin de semana)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        print(f"  [warn] Error descargando sumario {fecha_str}: {e}", file=sys.stderr)
        return None


def fetch_documento(boe_id):
    """Descarga el texto completo de un documento BOE."""
    url = f"https://www.boe.es/diario_boe/xml.php?id={boe_id}"
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.text
    except requests.RequestException:
        return ""


def parse_sumario_entries(sumario_json):
    """Extrae entries de Sección V (Anuncios) del sumario."""
    entries = []
    try:
        data = sumario_json.get("data", {})
        sumario = data.get("sumario", {})
        diario = sumario.get("diario", [])
        if isinstance(diario, dict):
            diario = [diario]
        for d in diario:
            secciones = d.get("seccion", [])
            if isinstance(secciones, dict):
                secciones = [secciones]
            for sec in secciones:
                sec_num = sec.get("num", "")
                if sec_num not in ("5", "V", "5B", "5A", "4", "3"):
                    if sec_num not in ("3", "4"):
                        continue
                departamentos = sec.get("departamento", [])
                if isinstance(departamentos, dict):
                    departamentos = [departamentos]
                for dep in departamentos:
                    dep_nombre = dep.get("nombre", "")
                    epigrafes = dep.get("epigrafe", [])
                    if isinstance(epigrafes, dict):
                        epigrafes = [epigrafes]
                    for epi in epigrafes:
                        items = epi.get("item", [])
                        if isinstance(items, dict):
                            items = [items]
                        for item in items:
                            entries.append({
                                "id": item.get("id", ""),
                                "titulo": item.get("titulo", ""),
                                "url_pdf": item.get("url_pdf", ""),
                                "url_html": item.get("url_html", ""),
                                "departamento": dep_nombre,
                                "seccion": sec_num,
                                "fecha": data.get("sumario", {}).get("meta", {}).get("pub", ""),
                            })
    except Exception as e:
        print(f"  [warn] Error parseando sumario: {e}", file=sys.stderr)
    return entries


def es_inmueble(titulo, departamento):
    """Filtra por keywords inmobiliarios."""
    texto = f"{titulo} {departamento}".lower()
    return any(kw in texto for kw in KW_INMUEBLE)


def entry_to_record(entry, doc_text=""):
    """Convierte una entry del BOE en un registro para la BD."""
    titulo = entry["titulo"]
    depto = entry["departamento"]
    full_text = f"{titulo} {depto} {doc_text}"
    municipio = detect_municipio(f"{titulo} {depto}")
    provincia = detect_provincia(full_text)
    ccaa = detect_ccaa(full_text)
    importe = extract_importe(doc_text) if doc_text else extract_importe(titulo)
    boe_url = ""
    if entry.get("url_html"):
        boe_url = "https://www.boe.es" + entry["url_html"] if entry["url_html"].startswith("/") else entry["url_html"]
    elif entry.get("id"):
        boe_url = f"https://www.boe.es/diario_boe/txt.php?id={entry['id']}"
    fecha_pub = None
    if entry.get("fecha"):
        m = re.search(r"(\d{4})(\d{2})(\d{2})", entry["fecha"])
        if m:
            fecha_pub = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    rec = {
        "expediente": entry["id"],
        "fuente": "BOE",
        "url": boe_url,
        "objeto": titulo,
        "organo": depto,
        "tipo_contrato": "Patrimonial",
        "cpv": [],
        "municipio": municipio,
        "provincia": provincia,
        "ccaa": ccaa,
        "nuts": None,
        "estado": "Publicado",
        "estado_codigo": None,
        "fecha_publicacion": fecha_pub,
        "fecha_limite": None,
        "fecha_adjudicacion": None,
        "importe_base": importe,
        "valor_estimado": None,
        "importe_adjudicacion": None,
        "adjudicatario": None,
        "moneda": "EUR",
    }
    return rec


# ---------------------------------------------------------------------------
# Escritura en Azure PostgreSQL (merge-duplicates por (expediente, fuente))
# ---------------------------------------------------------------------------
def upsert(rows):
    if not rows:
        return 0
    cols = []
    for r in rows:
        for k in r.keys():
            if k not in cols:
                cols.append(k)
    collist = ",".join('"%s"' % c for c in cols)
    ph = ",".join(["%s"] * len(cols))
    updates = ",".join('"%s"=EXCLUDED."%s"' % (c, c) for c in cols
                       if c not in ("expediente", "fuente"))
    sql = ("INSERT INTO vpp_licitaciones (%s) VALUES (%s) "
           "ON CONFLICT (expediente, fuente) DO UPDATE SET %s") % (collist, ph, updates)
    total = 0
    conn = _pg()
    try:
        with conn:
            with conn.cursor() as cur:
                for i in range(0, len(rows), 500):
                    chunk = rows[i:i + 500]
                    data = [[_adapt(r.get(c)) for c in cols] for r in chunk]
                    psycopg2.extras.execute_batch(cur, sql, data, page_size=500)
                    total += len(chunk)
    finally:
        conn.close()
    return total


def log_run(fuente, leidas, candidatos, upserted, error=None):
    try:
        conn = _pg()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO vpp_ingest_log (fuente, entries_leidas, candidatos, "
                    "insertados, actualizados, error) VALUES (%s,%s,%s,%s,%s,%s)",
                    (fuente, leidas, candidatos, upserted, 0, (error or "")[:500] or None))
        conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] no se pudo registrar el run: {e}", file=sys.stderr)


def main():
    if not PG_CONN:
        sys.exit("Falta AZURE_PG_CONN")
    now_iso = dt.datetime.utcnow().isoformat() + "Z"
    candidatos = []
    total_entries = 0
    total_sumarios = 0
    print(f"BOE Ingesta: {LOOKBACK_DAYS} días hacia atrás")
    for days_ago in range(LOOKBACK_DAYS):
        fecha = dt.date.today() - dt.timedelta(days=days_ago)
        fecha_str = fecha.strftime("%Y%m%d")
        sumario = fetch_sumario(fecha_str)
        if not sumario:
            continue
        total_sumarios += 1
        entries = parse_sumario_entries(sumario)
        total_entries += len(entries)
        for entry in entries:
            if not es_inmueble(entry["titulo"], entry["departamento"]):
                continue
            doc_text = ""
            if entry.get("id"):
                doc_text = fetch_documento(entry["id"])
                time.sleep(0.2)  # Rate limit cortesía
            rec = entry_to_record(entry, doc_text)
            if es_candidato(rec["objeto"], rec["organo"], rec["tipo_contrato"], rec["cpv"]):
                clasificar(rec)
            else:
                rec["tipo_vpp"] = "Otro / revisar"
                rec["naturaleza"] = "Suelo / derecho"
                rec["es_vpp"] = False
                rec["num_viviendas"] = None
                rec["score"] = 25
            rec["last_seen"] = now_iso
            rec["updated_at"] = now_iso
            candidatos.append(rec)
    # Deduplicar
    seen = {}
    for rec in candidatos:
        key = (rec["expediente"], rec["fuente"])
        seen[key] = rec
    candidatos = list(seen.values())
    print(f"BOE: {total_sumarios} sumarios, {total_entries} entries, {len(candidatos)} candidatos inmobiliarios")
    error = None
    n = 0
    try:
        n = upsert(candidatos)
        print(f"Upsert OK: {n} registros BOE")
    except Exception as e:  # noqa: BLE001
        error = str(e)
        print(f"[error] {error}", file=sys.stderr)
    log_run("BOE", total_entries, len(candidatos), n, error)
    if error:
        sys.exit(1)


if __name__ == "__main__":
    main()
