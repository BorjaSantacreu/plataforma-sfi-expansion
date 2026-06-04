"""
EEMM · Monitor VPP — Notificación semanal por email via Microsoft Graph API.

Consulta Supabase por licitaciones nuevas (últimos 7 días) y urgentes (plazo ≤7 días),
genera un email HTML con resumen y lo envía via Microsoft Graph (Azure AD App Registration).

Variables de entorno:
  SUPABASE_URL, SUPABASE_SERVICE_KEY
  MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET
  NOTIFY_EMAIL     (default: bsantacreu@sficonsulting.es)
  NOTIFY_FROM      (default: bsantacreu@sficonsulting.es)
"""
import os
import sys
import datetime as dt
import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
MS_TENANT_ID = os.environ.get("MS_TENANT_ID", "")
MS_CLIENT_ID = os.environ.get("MS_CLIENT_ID", "")
MS_CLIENT_SECRET = os.environ.get("MS_CLIENT_SECRET", "")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "bsantacreu@sficonsulting.es")
NOTIFY_FROM = os.environ.get("NOTIFY_FROM", "bsantacreu@sficonsulting.es")

ESTADOS_CERRADOS = ["Adjudicada", "Resuelta", "Desierta", "Anulada", "Parcialmente adjudicada"]

PLATFORM_URL = "https://lively-plant-019445f1e.2.azurestaticapps.net/"


def get_ms_token():
    """Obtiene un access token de Microsoft Graph via client credentials."""
    r = requests.post(
        f"https://login.microsoftonline.com/{MS_TENANT_ID}/oauth2/v2.0/token",
        data={
            "client_id": MS_CLIENT_ID,
            "client_secret": MS_CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    if r.status_code >= 300:
        print(f"[error] MS token {r.status_code}: {r.text[:300]}", file=sys.stderr)
        r.raise_for_status()
    return r.json()["access_token"]


def fetch_licitaciones():
    cols = "id,expediente,fuente,url,objeto,organo,tipo_vpp,naturaleza,num_viviendas,score,municipio,provincia,ccaa,estado,fecha_publicacion,fecha_limite,importe_base,valor_estimado,estado_interno,responsable_vpp,prioridad"
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/vpp_licitaciones?select={cols}&order=score.desc&limit=5000",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def fmt_eur(n):
    if n is None:
        return "—"
    return f"{n:,.0f} €".replace(",", ".")


def dias_restantes(fecha):
    if not fecha:
        return None
    try:
        d = dt.date.fromisoformat(fecha)
        return (d - dt.date.today()).days
    except (ValueError, TypeError):
        return None


def estado_real(r):
    estado = r.get("estado", "")
    if estado in ESTADOS_CERRADOS:
        return estado
    if r.get("fecha_limite"):
        d = dias_restantes(r["fecha_limite"])
        if d is not None and d < 0:
            return "Vencida"
    return estado or "Desconocido"


def build_email(all_rows):
    hoy = dt.date.today()
    hace7d = (hoy - dt.timedelta(days=7)).isoformat()

    activas = [r for r in all_rows if estado_real(r) not in ESTADOS_CERRADOS + ["Vencida"] and r.get("estado_interno") != "Descartada SFI"]
    nuevas = [r for r in activas if r.get("fecha_publicacion") and r["fecha_publicacion"] >= hace7d]
    nuevas.sort(key=lambda r: r.get("score", 0), reverse=True)

    urgentes = []
    for r in activas:
        d = dias_restantes(r.get("fecha_limite"))
        if d is not None and 0 <= d <= 7:
            r["_dias"] = d
            urgentes.append(r)
    urgentes.sort(key=lambda r: r["_dias"])

    top = sorted(activas, key=lambda r: r.get("score", 0), reverse=True)[:5]
    en_seguimiento = [r for r in all_rows if r.get("estado_interno") == "En seguimiento"]

    if not nuevas and not urgentes:
        return None

    gold = "#C9A84C"
    navy = "#0E2841"

    def row_html(r, show_dias=False):
        importe = r.get("importe_base") or r.get("valor_estimado")
        ubicacion = ", ".join(filter(None, [r.get("municipio"), r.get("provincia")]))
        dias_txt = ""
        if show_dias and "_dias" in r:
            d = r["_dias"]
            dias_txt = f' <span style="color:#DC2626;font-weight:700;">{"¡HOY!" if d == 0 else f"{d}d"}</span>'
        return f"""
        <tr style="border-bottom:1px solid #e2e8f0;">
            <td style="padding:8px 10px;font-size:13px;max-width:300px;">
                <strong>{(r.get('objeto') or '—')[:80]}</strong>
                <div style="color:#6B7D96;font-size:11px;">{r.get('organo', '')[:60]}</div>
            </td>
            <td style="padding:8px 10px;font-size:12px;">{ubicacion or '—'}</td>
            <td style="padding:8px 10px;font-size:12px;text-align:center;">{r.get('num_viviendas') or '—'}</td>
            <td style="padding:8px 10px;font-size:12px;text-align:right;font-weight:600;">{fmt_eur(importe)}{dias_txt}</td>
            <td style="padding:8px 10px;font-size:12px;text-align:center;font-weight:700;color:{gold};">{r.get('score', 0)}</td>
        </tr>"""

    def section_header(title, count, color):
        return f'<h2 style="color:{color};font-size:16px;margin:24px 0 8px;border-bottom:2px solid {color};padding-bottom:4px;">{title} ({count})</h2>'

    def table_start():
        return """<table style="width:100%;border-collapse:collapse;margin-bottom:16px;">
        <thead><tr style="background:#f8fafc;"><th style="padding:6px 10px;font-size:11px;text-align:left;">Objeto</th>
        <th style="padding:6px 10px;font-size:11px;">Ubicación</th>
        <th style="padding:6px 10px;font-size:11px;text-align:center;">Viv.</th>
        <th style="padding:6px 10px;font-size:11px;text-align:right;">Importe</th>
        <th style="padding:6px 10px;font-size:11px;text-align:center;">Score</th></tr></thead><tbody>"""

    html = f"""
    <div style="font-family:'Segoe UI',Calibri,Arial,sans-serif;max-width:700px;margin:0 auto;background:#fff;">
        <div style="background:{navy};padding:20px 24px;">
            <div style="color:{gold};font-size:12px;font-weight:700;letter-spacing:1px;">SFI · EXPANSIÓN</div>
            <h1 style="color:#fff;font-size:20px;margin:4px 0 0;">Resumen semanal — Licitaciones VPP</h1>
            <div style="color:#8A9AB5;font-size:13px;margin-top:4px;">{hoy.strftime('%d/%m/%Y')} · {len(activas)} licitaciones activas</div>
        </div>
        <div style="padding:20px 24px;">
            <div style="display:flex;gap:12px;margin-bottom:20px;">
                <div style="flex:1;background:#f0fdf4;border:1px solid #86efac;border-radius:8px;padding:12px;text-align:center;">
                    <div style="font-size:24px;font-weight:700;color:#16A34A;">{len(nuevas)}</div>
                    <div style="font-size:11px;color:#6B7D96;">Nuevas esta semana</div>
                </div>
                <div style="flex:1;background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;padding:12px;text-align:center;">
                    <div style="font-size:24px;font-weight:700;color:#DC2626;">{len(urgentes)}</div>
                    <div style="font-size:11px;color:#6B7D96;">Vencen en ≤7 días</div>
                </div>
                <div style="flex:1;background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;padding:12px;text-align:center;">
                    <div style="font-size:24px;font-weight:700;color:{gold};">{len(en_seguimiento)}</div>
                    <div style="font-size:11px;color:#6B7D96;">En seguimiento</div>
                </div>
            </div>
    """

    if urgentes:
        html += section_header("⏰ Vencen esta semana", len(urgentes), "#DC2626")
        html += table_start()
        for r in urgentes[:10]:
            html += row_html(r, show_dias=True)
        html += "</tbody></table>"

    if nuevas:
        html += section_header("🆕 Nuevas esta semana", len(nuevas), "#16A34A")
        html += table_start()
        for r in nuevas[:10]:
            html += row_html(r)
        html += "</tbody></table>"

    if top:
        html += section_header("🏆 Top oportunidades activas", len(top), gold)
        html += table_start()
        for r in top:
            html += row_html(r)
        html += "</tbody></table>"

    html += f"""
            <div style="margin-top:24px;text-align:center;">
                <a href="{PLATFORM_URL}" style="display:inline-block;background:{gold};color:{navy};font-weight:700;text-decoration:none;padding:12px 24px;border-radius:8px;font-size:14px;">
                    Abrir Monitor VPP →
                </a>
            </div>
            <div style="margin-top:20px;padding-top:16px;border-top:1px solid #e2e8f0;font-size:11px;color:#94a3b8;text-align:center;">
                SFI Consulting · Plataforma de Expansión · Generado automáticamente
            </div>
        </div>
    </div>
    """
    return html


def send_email(token, html, nuevas_count, urgentes_count):
    """Envía via Microsoft Graph API."""
    hoy = dt.date.today().strftime("%d/%m/%Y")
    subject = f"🏗️ VPP Semanal: {nuevas_count} nuevas"
    if urgentes_count:
        subject += f", {urgentes_count} urgentes"

    r = requests.post(
        f"https://graph.microsoft.com/v1.0/users/{NOTIFY_FROM}/sendMail",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": html},
                "toRecipients": [{"emailAddress": {"address": NOTIFY_EMAIL}}],
            },
            "saveToSentItems": "false",
        },
        timeout=30,
    )
    if r.status_code >= 300:
        print(f"[error] Graph {r.status_code}: {r.text[:400]}", file=sys.stderr)
        r.raise_for_status()
    print(f"✅ Email enviado a {NOTIFY_EMAIL} desde {NOTIFY_FROM}")


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        sys.exit("Faltan SUPABASE_URL / SUPABASE_SERVICE_KEY")
    if not MS_TENANT_ID or not MS_CLIENT_ID or not MS_CLIENT_SECRET:
        print("[warn] MS_TENANT_ID/MS_CLIENT_ID/MS_CLIENT_SECRET no configurados — generando HTML pero no enviando", file=sys.stderr)

    all_rows = fetch_licitaciones()
    print(f"Cargadas {len(all_rows)} licitaciones")

    html = build_email(all_rows)
    if not html:
        print("Sin novedades esta semana — no se envía email")
        return

    hoy = dt.date.today()
    hace7d = (hoy - dt.timedelta(days=7)).isoformat()
    activas = [r for r in all_rows if estado_real(r) not in ESTADOS_CERRADOS + ["Vencida"]]
    nuevas = len([r for r in activas if r.get("fecha_publicacion") and r["fecha_publicacion"] >= hace7d])
    urgentes = len([r for r in activas if dias_restantes(r.get("fecha_limite")) is not None and 0 <= dias_restantes(r.get("fecha_limite")) <= 7])

    if MS_TENANT_ID and MS_CLIENT_ID and MS_CLIENT_SECRET:
        token = get_ms_token()
        send_email(token, html, nuevas, urgentes)
    else:
        with open("/tmp/vpp_email_preview.html", "w") as f:
            f.write(html)
        print("Preview guardado en /tmp/vpp_email_preview.html")


if __name__ == "__main__":
    main()
