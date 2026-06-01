"""
EEMM · Monitor de Licitaciones VPP — Ingesta desde datos abiertos PLACSP.

Flujo:
  1. Descarga el/los ZIP de sindicación (datos abiertos oficiales, formato CODICE 2.07 / ATOM).
  2. Parsea las entradas (parsing por local-name → robusto frente a prefijos de namespace).
  3. Filtra candidatos VPP por CPV + palabras clave (classify.es_candidato).
  4. Clasifica por reglas (tipo VPP, nº viviendas, score).
  5. Upsert idempotente en Supabase (clave: expediente + fuente).

No usa IA. Pensado para correr a diario (GitHub Actions cron o Azure Function timer).

Variables de entorno requeridas:
  SUPABASE_URL          https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY  service_role key (SOLO server-side; nunca en el frontend)
Opcionales:
  LOOKBACK_DAYS         (def. 35) solo procesa entradas actualizadas en este margen
  PLACSP_ZIP_URLS       lista separada por comas; si se omite, usa DEFAULT_FEEDS
"""
import os
import io
import re
import sys
import zipfile
import datetime as dt

import requests
from lxml import etree

from classify import es_candidato, clasificar

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "35"))
YEAR = dt.date.today().year

# Feeds oficiales de datos abiertos. El nº de sindicación y el nombre de fichero
# pueden cambiar de un año a otro — VALIDAR contra una muestra real en el 1er run.
DEFAULT_FEEDS = [
    # Licitaciones de perfiles del contratante alojados en PLACSP (confirmado)
    ("PLACSP",
     f"https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_643/"
     f"licitacionesPerfilesContratanteCompleto3_{YEAR}.zip"),
    # Licitaciones agregadas de CCAA con plataforma propia (cobertura nacional).
    ("AGREGADAS",
     f"https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_1044/"
     f"PlataformasAgregadasSinMenores_{YEAR}.zip"),
]

# Códigos CODICE de tipo de contrato (parcial — completar con la code-list oficial)
CONTRATO_TIPO = {
    "1": "Obras", "2": "Suministros", "3": "Servicios",
    "21": "Gestión de servicios públicos / Concesión",
    "31": "Patrimonial", "32": "Privado", "40": "Concesión de obras",
    "50": "Concesión de servicios",
}

# Estados CODICE (parcial — completar con la code-list oficial)
ESTADO = {
    "PRE": "Anuncio previo", "PUB": "En plazo", "EV": "En evaluación",
    "ADJ": "Adjudicada", "RES": "Resuelta", "ANUL": "Anulada",
    "DES": "Desierta", "1": "Anuncio previo", "2": "En plazo",
    "3": "Pendiente adjudicación", "4": "Adjudicada", "5": "Resuelta",
    "6": "Anulada", "7": "Parcialmente adjudicada", "8": "Desierta",
}

# NUTS2 → CCAA (best-effort para el filtro/visión nacional)
NUTS2_CCAA = {
    "ES11": "Galicia", "ES12": "Asturias", "ES13": "Cantabria",
    "ES21": "País Vasco", "ES22": "Navarra", "ES23": "La Rioja",
    "ES24": "Aragón", "ES30": "Madrid", "ES41": "Castilla y León",
    "ES42": "Castilla-La Mancha", "ES43": "Extremadura", "ES51": "Cataluña",
    "ES52": "C. Valenciana", "ES53": "Baleares", "ES61": "Andalucía",
    "ES62": "Murcia", "ES63": "Ceuta", "ES64": "Melilla", "ES70": "Canarias",
}

ATOM_NS = "{http://www.w3.org/2005/Atom}"


# ---------------------------------------------------------------------------
# Helpers de parsing por local-name (ignora el prefijo de namespace)
# ---------------------------------------------------------------------------
def ln(elem):
    return etree.QName(elem).localname if elem.tag is not etree.Comment else ""


def first_text(root, localname):
    for e in root.iter():
        if ln(e) == localname and e.text and e.text.strip():
            return e.text.strip()
    return None


def first_elem(root, localname):
    for e in root.iter():
        if ln(e) == localname:
            return e
    return None


def all_text(root, localname):
    out = []
    for e in root.iter():
        if ln(e) == localname and e.text and e.text.strip():
            out.append(e.text.strip())
    return out


def to_num(s):
    if not s:
        return None
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return None


def to_date(s):
    if not s:
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# Extracción de una entrada ATOM
# ---------------------------------------------------------------------------
def parse_entry(entry, fuente):
    cfs = first_elem(entry, "ContractFolderStatus")
    if cfs is None:
        return None

    expediente = first_text(cfs, "ContractFolderID")
    if not expediente:
        return None

    # URL al detalle (link alternate del entry)
    url = None
    for link in entry.findall(f"{ATOM_NS}link"):
        if link.get("rel") in (None, "alternate"):
            url = link.get("href")
            break

    estado_cod = first_text(cfs, "ContractFolderStatusCode")

    # Proyecto / objeto / importes / CPV
    proj = first_elem(cfs, "ProcurementProject")
    objeto = first_text(proj, "Name") if proj is not None else None
    tipo_cod = first_text(proj, "TypeCode") if proj is not None else None
    tipo_contrato = CONTRATO_TIPO.get(tipo_cod, tipo_cod)

    importe_base = valor_estimado = None
    if proj is not None:
        budget = first_elem(proj, "BudgetAmount")
        if budget is not None:
            importe_base = to_num(first_text(budget, "TaxExclusiveAmount")) \
                or to_num(first_text(budget, "TotalAmount"))
            valor_estimado = to_num(first_text(budget, "EstimatedOverallContractAmount"))

    cpvs = all_text(proj, "ItemClassificationCode") if proj is not None else []

    # Localización
    municipio = provincia = nuts = ccaa = None
    if proj is not None:
        loc = first_elem(proj, "RealizedLocation")
        if loc is not None:
            municipio = first_text(loc, "CityName")
            provincia = first_text(loc, "CountrySubentity")
            nuts = first_text(loc, "CountrySubentityCode")
            if nuts:
                ccaa = NUTS2_CCAA.get(nuts[:4])

    # Órgano de contratación (dentro de LocatedContractingParty)
    organo = None
    lcp = first_elem(cfs, "LocatedContractingParty")
    if lcp is not None:
        pn = first_elem(lcp, "PartyName")
        if pn is not None:
            organo = first_text(pn, "Name")

    # Plazo de presentación
    fecha_limite = None
    tp = first_elem(cfs, "TenderingProcess")
    if tp is not None:
        ddl = first_elem(tp, "TenderSubmissionDeadlinePeriod")
        if ddl is not None:
            fecha_limite = to_date(first_text(ddl, "EndDate"))

    # Resultado / adjudicación
    fecha_adj = adjudicatario = importe_adj = None
    tr = first_elem(cfs, "TenderResult")
    if tr is not None:
        fecha_adj = to_date(first_text(tr, "AwardDate"))
        wp = first_elem(tr, "WinningParty")
        if wp is not None:
            pn = first_elem(wp, "PartyName")
            if pn is not None:
                adjudicatario = first_text(pn, "Name")
        atp = first_elem(tr, "AwardedTenderedProject")
        if atp is not None:
            importe_adj = to_num(first_text(atp, "PayableAmount")) \
                or to_num(first_text(atp, "TaxExclusiveAmount"))

    # Fecha de publicación: usamos el <updated> del entry como aproximación
    fecha_pub = to_date(first_text(entry, "updated"))

    return {
        "expediente": expediente,
        "fuente": fuente,
        "url": url,
        "objeto": objeto,
        "organo": organo,
        "tipo_contrato": tipo_contrato,
        "cpv": cpvs,
        "municipio": municipio,
        "provincia": provincia,
        "ccaa": ccaa,
        "nuts": nuts,
        "estado": ESTADO.get(estado_cod, estado_cod),
        "estado_codigo": estado_cod,
        "fecha_publicacion": fecha_pub,
        "fecha_limite": fecha_limite,
        "fecha_adjudicacion": fecha_adj,
        "importe_base": importe_base,
        "valor_estimado": valor_estimado,
        "importe_adjudicacion": importe_adj,
        "adjudicatario": adjudicatario,
        "moneda": "EUR",
    }


# ---------------------------------------------------------------------------
# Recorrido del ZIP
# ---------------------------------------------------------------------------
def iter_entries(zip_bytes):
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".atom"):
                continue
            with zf.open(name) as fh:
                try:
                    tree = etree.parse(fh)
                except etree.XMLSyntaxError as e:
                    print(f"  [warn] XML inválido en {name}: {e}", file=sys.stderr)
                    continue
                for entry in tree.iter(f"{ATOM_NS}entry"):
                    yield entry


def reciente(rec, cutoff):
    fp = rec.get("fecha_publicacion") or rec.get("fecha_limite")
    if not fp:
        return True  # sin fecha → mejor procesarlo
    return fp >= cutoff


# ---------------------------------------------------------------------------
# Supabase upsert (PostgREST, merge-duplicates por (expediente, fuente))
# ---------------------------------------------------------------------------
def upsert(rows):
    if not rows:
        return 0
    endpoint = f"{SUPABASE_URL}/rest/v1/vpp_licitaciones?on_conflict=expediente,fuente"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    total = 0
    for i in range(0, len(rows), 500):  # lotes de 500
        chunk = rows[i:i + 500]
        r = requests.post(endpoint, headers=headers, json=chunk, timeout=120)
        if r.status_code >= 300:
            print(f"  [error] upsert {r.status_code}: {r.text[:400]}", file=sys.stderr)
            r.raise_for_status()
        total += len(chunk)
    return total


def log_run(fuente, leidas, candidatos, upserted, error=None):
    """Deja traza de la ejecución en vpp_ingest_log (auditoría / salud del sistema)."""
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/vpp_ingest_log",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            json={
                "fuente": fuente, "entries_leidas": leidas,
                "candidatos": candidatos, "insertados": upserted,
                "actualizados": 0, "error": (error or "")[:500] or None,
            },
            timeout=30,
        )
    except requests.RequestException as e:
        print(f"  [warn] no se pudo registrar el run: {e}", file=sys.stderr)


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("Faltan SUPABASE_URL / SUPABASE_SERVICE_KEY")

    feeds = DEFAULT_FEEDS
    env_urls = os.environ.get("PLACSP_ZIP_URLS")
    if env_urls:
        feeds = [("PLACSP", u.strip()) for u in env_urls.split(",") if u.strip()]

    cutoff = (dt.date.today() - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    now_iso = dt.datetime.utcnow().isoformat() + "Z"
    candidatos = []
    leidas = 0

    for fuente, url in feeds:
        print(f"Descargando {fuente}: {url}")
        try:
            resp = requests.get(url, timeout=600)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  [error] descarga fallida: {e}", file=sys.stderr)
            continue

        for entry in iter_entries(resp.content):
            leidas += 1
            rec = parse_entry(entry, fuente)
            if not rec:
                continue
            if not reciente(rec, cutoff):
                continue
            if not es_candidato(rec["objeto"], rec["organo"],
                                rec["tipo_contrato"], rec["cpv"]):
                continue
            clasificar(rec)
            rec["last_seen"] = now_iso
            rec["updated_at"] = now_iso
            candidatos.append(rec)

    # Deduplicar por (expediente, fuente) — quedarse con la versión más reciente
    seen = {}
    for rec in candidatos:
        key = (rec["expediente"], rec["fuente"])
        seen[key] = rec  # la última aparición sobreescribe
    candidatos = list(seen.values())

    print(f"Leídas {leidas} entradas · {len(candidatos)} candidatos VPP (dedup)")
    error = None
    n = 0
    try:
        n = upsert(candidatos)
        print(f"Upsert OK: {n} registros")
    except Exception as e:  # noqa: BLE001
        error = str(e)
        print(f"[error] {error}", file=sys.stderr)
    log_run("PLACSP", leidas, len(candidatos), n, error)
    if error:
        sys.exit(1)


if __name__ == "__main__":
    main()
