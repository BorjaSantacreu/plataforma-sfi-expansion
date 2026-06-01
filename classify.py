"""
Clasificación por reglas de las licitaciones candidatas (sin IA).
Determina tipo VPP, si es vivienda protegida, nº de viviendas y un score 0-100.
Todo determinista y mantenible. Si en el futuro se quiere una capa IA,
se aplica SOLO sobre los candidatos que pasan por aquí.
"""
import re
import unicodedata


# --- CPV de interés (se compara por prefijo con startswith) -----------------
CPV_PREFIJOS = (
    "70111",  # promoción de propiedades inmobiliarias residenciales
    "70123",  # venta de bienes inmuebles (residenciales)
    "70110",  # servicios de promoción inmobiliaria
    "70200",  # alquiler / leasing de inmuebles propios
    "45211",  # construcción de viviendas multifamiliares/individuales
    "45210",  # construcción de edificios
    "70000",  # servicios inmobiliarios (paraguas, genera algo de ruido)
)

# --- Palabras clave (texto ya normalizado: sin acentos, minúsculas) ---------
KW_VPP = (
    "vivienda protegida", "viviendas protegidas", "vivienda de proteccion",
    "vpp", "vpo", "vppl", "vppb", "vivienda asequible", "viviendas asequibles",
    "alquiler asequible", "regimen de proteccion",
)
KW_RESIDENCIAL = (
    "vivienda", "viviendas", "residencial", "promocion de viviendas",
    "parcela residencial", "suelo residencial", "solar residencial",
)
KW_PERMUTA = ("permuta",)
KW_BTR = (
    "derecho de superficie", "build to rent", "btr", "alquiler asequible",
    "arrendamiento", "concesion administrativa",
)
KW_VENTA = ("enajenacion", "venta", "compraventa", "subasta de", "alienacion")

# "Suelo / derecho" = lo que de verdad interesa a un promotor (adquirir suelo o
# derechos para promover). "Obra" = contrato de construcción (otro negocio).
KW_SUELO = (
    "enajenacion", "derecho de superficie", "permuta", "concesion",
    "venta de suelo", "venta de parcela", "venta de solar", "compraventa",
    "subasta", "alienacion", "aprovechamiento urbanistico", "patrimonial",
)
KW_OBRA = (
    "contrato de obras", "ejecucion de obras", "obras de construccion",
    "obras de edificacion", "redaccion de proyecto", "direccion de obra",
    "rehabilitacion", "reforma",
)

# Acepta separador de miles (1.250 / 1,250) y captura el número completo.
RE_VIVIENDAS = re.compile(r"(\d{1,3}(?:[.,]\d{3})*|\d{1,4})\s*(?:viviendas?|vpp|vpo)", re.IGNORECASE)


def norm(texto):
    """Minúsculas + sin acentos para matching robusto."""
    if not texto:
        return ""
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.lower()


def cpv_relevante(cpvs):
    for c in cpvs or []:
        cc = (c or "").replace("-", "").strip()
        if cc.startswith(CPV_PREFIJOS):
            return True
    return False


def texto_residencial(t_norm):
    return any(k in t_norm for k in KW_RESIDENCIAL) or any(k in t_norm for k in KW_VPP)


def es_candidato(objeto, organo, tipo_contrato, cpvs):
    """
    True si la licitación merece guardarse. Criterio amplio a propósito:
    preferimos sobre-capturar y descartar al revisar que perder una oportunidad.
    """
    t = norm(f"{objeto} {organo} {tipo_contrato}")
    if cpv_relevante(cpvs) and texto_residencial(t):
        return True
    if any(k in t for k in KW_VPP):
        return True
    # permuta/derecho de superficie/enajenación con contexto residencial
    if texto_residencial(t) and (
        any(k in t for k in KW_PERMUTA)
        or "derecho de superficie" in t
        or any(k in t for k in KW_VENTA)
    ):
        return True
    return False


def clasificar_tipo(objeto, tipo_contrato):
    t = norm(f"{objeto} {tipo_contrato}")
    if any(k in t for k in KW_PERMUTA):
        return "Permuta"
    if "derecho de superficie" in t:
        return "BTR / Concesión"
    if "concesion" in t and any(k in t for k in ("alquiler", "arrendamiento", "btr", "build to rent")):
        return "BTR / Concesión"
    if any(k in t for k in ("build to rent", "alquiler asequible")) or " btr" in f" {t}":
        return "BTR / Concesión"
    if any(k in t for k in KW_VENTA):
        return "Venta"
    return "Otro / revisar"


def clasificar_naturaleza(objeto, tipo_contrato):
    """
    Distingue lo que interesa a un PROMOTOR (adquirir suelo/derechos para promover)
    de un contrato de OBRA (construir para la administración = otro negocio).
    Clave para 'decidir a cuál presentarse'.
    """
    t = norm(f"{objeto} {tipo_contrato}")
    suelo = any(k in t for k in KW_SUELO)
    obra = any(k in t for k in KW_OBRA)
    if suelo and not obra:
        return "Suelo / derecho"
    if obra and not suelo:
        return "Obra"
    if suelo and obra:
        return "Suelo / derecho"  # prevalece la adquisición de suelo
    return "Otro"


def detectar_vpp(objeto, organo):
    t = norm(f"{objeto} {organo}")
    return any(k in t for k in KW_VPP)


def detectar_viviendas(objeto):
    m = RE_VIVIENDAS.search(objeto or "")
    if not m:
        return None
    try:
        n = int(m.group(1).replace(".", "").replace(",", ""))
        return n if 0 < n < 10000 else None
    except ValueError:
        return None


def calcular_score(es_vpp, tipo_vpp, naturaleza, importe, num_viviendas):
    """Score 0-100. Pondera relevancia para Expansión SFI (foco: adquirir suelo VPP)."""
    s = 0
    if es_vpp:
        s += 30
    if naturaleza == "Suelo / derecho":
        s += 30                      # lo que de verdad busca un promotor
    elif naturaleza == "Obra":
        s += 5                       # otro negocio; lo dejamos visible pero abajo
    if tipo_vpp in ("Venta", "BTR / Concesión", "Permuta"):
        s += 15
    if importe:
        if importe >= 5_000_000:
            s += 15
        elif importe >= 1_000_000:
            s += 9
        elif importe >= 200_000:
            s += 4
    if num_viviendas:
        if num_viviendas >= 50:
            s += 10
        elif num_viviendas >= 20:
            s += 6
        elif num_viviendas >= 1:
            s += 3
    return min(s, 100)


def clasificar(rec):
    """Enriquece un registro (dict) con los campos de clasificación."""
    objeto = rec.get("objeto", "")
    organo = rec.get("organo", "")
    tipo_contrato = rec.get("tipo_contrato", "")

    rec["es_vpp"] = detectar_vpp(objeto, organo)
    rec["tipo_vpp"] = clasificar_tipo(objeto, tipo_contrato)
    rec["naturaleza"] = clasificar_naturaleza(objeto, tipo_contrato)
    rec["num_viviendas"] = detectar_viviendas(objeto)
    rec["score"] = calcular_score(
        rec["es_vpp"], rec["tipo_vpp"], rec["naturaleza"],
        rec.get("importe_base"), rec["num_viviendas"]
    )
    return rec
