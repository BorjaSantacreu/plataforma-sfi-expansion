// Azure Function: weekly-digest (HTTP)
// Genera el resumen semanal de pipeline y lo envía al Grupo 5 - Resumen Semanal
// App Settings necesarios:
//   - SUPABASE_URL
//   - SUPABASE_ANON_KEY
//   - PLATFORM_BASE_URL (para construir links a las fichas)

const https = require('https');
const NAVY = '#0E2841';
const GOLD = '#C9A84C';
const CORS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type'
};

function httpsRequest(opts, body) {
    return new Promise(function (resolve, reject) {
        var req = https.request(opts, function (res) {
            var chunks = [];
            res.on('data', function (c) { chunks.push(c); });
            res.on('end', function () {
                resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks).toString('utf8') });
            });
        });
        req.on('error', reject);
        if (body) req.write(body);
        req.end();
    });
}

async function sbGet(path, context) {
    var base = (process.env.SUPABASE_URL || '').replace(/\/+$/, '');
    var fullUrl = base + '/rest/v1/' + path;
    var u = new URL(fullUrl);
    if (context && context.log) context.log('sbGet ' + u.pathname + u.search);
    var res = await httpsRequest({
        hostname: u.hostname,
        path: u.pathname + u.search,
        method: 'GET',
        headers: {
            'apikey': process.env.SUPABASE_ANON_KEY,
            'Authorization': 'Bearer ' + process.env.SUPABASE_ANON_KEY,
            'Accept': 'application/json'
        }
    });
    if (res.status >= 400) {
        var err = new Error('Supabase ' + res.status + ' on ' + path + ': ' + res.body);
        err.status = res.status;
        err.path = path;
        err.responseBody = res.body;
        throw err;
    }
    try { return JSON.parse(res.body); } catch (e) { return []; }
}

function getLastWeekWindow() {
    var now = new Date();
    var dayOfWeek = now.getDay();
    var daysToThisMonday = dayOfWeek === 0 ? 6 : dayOfWeek - 1;
    var thisMonday00 = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    thisMonday00.setDate(thisMonday00.getDate() - daysToThisMonday);
    var lastMonday00 = new Date(thisMonday00);
    lastMonday00.setDate(thisMonday00.getDate() - 7);
    var lastSunday23 = new Date(thisMonday00.getTime() - 1);
    return { from: lastMonday00, to: lastSunday23, toExclusive: thisMonday00 };
}

function ymd(d) {
    var m = String(d.getMonth() + 1).padStart(2, '0');
    var day = String(d.getDate()).padStart(2, '0');
    return d.getFullYear() + '-' + m + '-' + day;
}

function fmtRangoES(from, to) {
    var meses = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'];
    if (from.getMonth() === to.getMonth()) {
        return 'Semana del ' + from.getDate() + ' al ' + to.getDate() + ' de ' + meses[from.getMonth()] + ' de ' + from.getFullYear();
    }
    return 'Semana del ' + from.getDate() + ' de ' + meses[from.getMonth()] + ' al ' + to.getDate() + ' de ' + meses[to.getMonth()] + ' de ' + from.getFullYear();
}

function fmtMoney(n) {
    if (n == null || isNaN(n)) return '—';
    return Number(n).toLocaleString('es-ES', { maximumFractionDigits: 0 }) + ' €';
}

function fmtNum(n) {
    if (n == null || isNaN(n)) return '—';
    return Number(n).toLocaleString('es-ES', { maximumFractionDigits: 0 });
}

function escapeHTML(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, function (c) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]; });
}

function diasEntre(d1, d2) {
    if (!d1 || !d2) return null;
    var t1 = new Date(d1).getTime();
    var t2 = new Date(d2).getTime();
    if (isNaN(t1) || isNaN(t2)) return null;
    return Math.round((t2 - t1) / (1000 * 60 * 60 * 24));
}

function getVerticalGroup(s) {
    var v = s.vertical || '';
    if (v === 'Investment') return 'Investment';
    if (v === 'Inversión Patrimonial') return 'Investment';
    if (v === 'Hospitality' || s.negocio === 'Hotel') return 'Hospitality';
    return 'Living';
}

function calcHHNNEstimado(s) {
    // hhnn_estimados y pct_hhnn no son columnas reales en Supabase; se calculan por defecto del negocio
    var pct;
    if (s.negocio === 'Cooperativa') pct = 11;
    else if (s.negocio === 'Venta libre') pct = 6;
    else pct = 5;
    var pvp = s.pvp_zona_manual || 0;
    var edif = s.superficie_edificable || 0;
    if (pvp && edif) return (pct / 100) * pvp * edif;
    return null;
}

function fichaUrl(sueloId) {
    var base = process.env.PLATFORM_BASE_URL || 'https://expansion.sficonsulting.es';
    return base + '/?suelo=' + encodeURIComponent(sueloId);
}

function buildDigestHTML(data) {
    var rango = fmtRangoES(data.from, data.to);
    var totalNuevas = data.nuevas.length;
    var totalFase1 = data.movFase1.length;
    var totalSeguim = data.movSeguim.length;
    var invTotal = data.nuevas.reduce(function (s, x) { return s + (Number(x.precio_suelo) || 0); }, 0);
    var hhnnTotal = data.nuevas.reduce(function (s, x) { var h = calcHHNNEstimado(x); return s + (h || 0); }, 0);

    var html = '<div style="font-family:Calibri,Arial,sans-serif;max-width:720px;margin:0 auto;background:#fff;color:#1e293b;">';
    html += '<div style="background:' + NAVY + ';padding:24px 28px;">';
    html += '<div style="color:' + GOLD + ';font-size:12px;letter-spacing:2px;font-weight:700;">SFI · EXPANSIÓN</div>';
    html += '<h1 style="color:#fff;font-size:22px;margin:6px 0 0;font-weight:600;">Resumen Semanal</h1>';
    html += '<div style="color:#fff;opacity:0.75;font-size:13px;margin-top:4px;">' + rango + '</div>';
    html += '</div>';

    html += '<div style="padding:20px 24px 0;">';
    html += '<table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:separate;border-spacing:8px;">';
    html += '<tr>';
    [
        { v: totalNuevas, l: 'Nuevas captadas', c: '#0ea5e9' },
        { v: totalFase1, l: 'Pasaron a Fase 1', c: '#16a34a' },
        { v: totalSeguim, l: 'Pasaron a Seguimiento', c: '#f59e0b' },
        { v: fmtMoney(invTotal), l: 'Inversión nuevas', c: '#8b5cf6' },
        { v: fmtMoney(hhnnTotal), l: 'HHNN potenciales', c: GOLD }
    ].forEach(function (k) {
        html += '<td style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px;text-align:center;vertical-align:top;width:20%;">';
        html += '<div style="font-size:20px;font-weight:700;color:' + k.c + ';line-height:1.2;">' + k.v + '</div>';
        html += '<div style="font-size:11px;color:#64748b;margin-top:4px;">' + k.l + '</div>';
        html += '</td>';
    });
    html += '</tr></table></div>';

    html += '<div style="padding:24px;">';
    html += '<h2 style="color:' + NAVY + ';font-size:16px;border-bottom:2px solid ' + GOLD + ';padding-bottom:6px;margin:0 0 12px;">Nuevas oportunidades captadas</h2>';
    if (totalNuevas === 0) {
        html += '<div style="color:#94a3b8;font-size:13px;font-style:italic;padding:8px 0;">Sin altas esta semana.</div>';
    } else {
        var byVertical = { 'Living': [], 'Investment': [], 'Hospitality': [] };
        data.nuevas.forEach(function (s) { byVertical[getVerticalGroup(s)].push(s); });
        ['Living', 'Investment', 'Hospitality'].forEach(function (vg) {
            var list = byVertical[vg];
            if (!list.length) return;
            list.sort(function (a, b) { return (b.score_total || 0) - (a.score_total || 0); });
            html += '<div style="margin-top:14px;font-size:13px;font-weight:600;color:' + NAVY + ';">' + vg + ' <span style="color:#64748b;font-weight:400;font-size:12px;">(' + list.length + ')</span></div>';
            html += '<table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;margin-top:6px;font-size:12px;">';
            html += '<thead><tr style="background:#f1f5f9;color:#475569;">';
            ['Score', 'Nombre', 'Ciudad', 'Negocio', 'Viv.', 'Precio suelo', 'HHNN est.', 'Resp.'].forEach(function (h) {
                html += '<th style="padding:8px 8px;text-align:left;font-weight:600;border-bottom:1px solid #e2e8f0;">' + h + '</th>';
            });
            html += '</tr></thead><tbody>';
            list.forEach(function (s) {
                var sc = Number(s.score_total) || 0;
                var scColor = sc >= 85 ? '#0ea5e9' : sc >= 70 ? '#16a34a' : sc >= 50 ? '#f59e0b' : '#94a3b8';
                var rowBg = sc >= 70 ? '#fffbeb' : '#fff';
                html += '<tr style="background:' + rowBg + ';border-bottom:1px solid #f1f5f9;">';
                html += '<td style="padding:8px;"><span style="display:inline-block;background:' + scColor + ';color:#fff;font-weight:700;padding:2px 8px;border-radius:10px;font-size:11px;">' + sc + '</span></td>';
                html += '<td style="padding:8px;"><a href="' + fichaUrl(s.id) + '" style="color:' + NAVY + ';font-weight:600;text-decoration:none;">' + escapeHTML(s.nombre || '—') + '</a></td>';
                html += '<td style="padding:8px;color:#475569;">' + escapeHTML(s.ciudad || '—') + '</td>';
                html += '<td style="padding:8px;color:#475569;">' + escapeHTML(s.negocio || '—') + '</td>';
                html += '<td style="padding:8px;color:#475569;">' + fmtNum(s.num_viviendas) + '</td>';
                html += '<td style="padding:8px;color:#475569;">' + fmtMoney(s.precio_suelo) + '</td>';
                html += '<td style="padding:8px;color:#475569;">' + fmtMoney(calcHHNNEstimado(s)) + '</td>';
                html += '<td style="padding:8px;color:#475569;">' + escapeHTML(s.responsable || '—') + '</td>';
                html += '</tr>';
            });
            html += '</tbody></table>';
        });
    }
    html += '</div>';

    html += '<div style="padding:0 24px 24px;">';
    html += '<h2 style="color:' + NAVY + ';font-size:16px;border-bottom:2px solid ' + GOLD + ';padding-bottom:6px;margin:0 0 12px;">Pasaron a Fase 1 — Compra del suelo</h2>';
    if (totalFase1 === 0) {
        html += '<div style="color:#94a3b8;font-size:13px;font-style:italic;padding:8px 0;">Ninguna esta semana.</div>';
    } else {
        html += '<table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;font-size:12px;">';
        html += '<thead><tr style="background:#f1f5f9;color:#475569;">';
        ['Nombre', 'Ciudad', 'Días en Fase 0', 'HHNN', 'Responsable', 'Movido por'].forEach(function (h) {
            html += '<th style="padding:8px;text-align:left;font-weight:600;border-bottom:1px solid #e2e8f0;">' + h + '</th>';
        });
        html += '</tr></thead><tbody>';
        data.movFase1.forEach(function (m) {
            html += '<tr style="border-bottom:1px solid #f1f5f9;">';
            html += '<td style="padding:8px;"><a href="' + fichaUrl(m.suelo.id) + '" style="color:' + NAVY + ';font-weight:600;text-decoration:none;">' + escapeHTML(m.suelo.nombre || '—') + '</a></td>';
            html += '<td style="padding:8px;color:#475569;">' + escapeHTML(m.suelo.ciudad || '—') + '</td>';
            html += '<td style="padding:8px;color:#475569;">' + (m.diasFase0 != null ? m.diasFase0 + ' d' : '—') + '</td>';
            html += '<td style="padding:8px;color:#475569;">' + fmtMoney(calcHHNNEstimado(m.suelo)) + '</td>';
            html += '<td style="padding:8px;color:#475569;">' + escapeHTML(m.suelo.responsable || '—') + '</td>';
            html += '<td style="padding:8px;color:#475569;">' + escapeHTML(m.usuario || '—') + '</td>';
            html += '</tr>';
        });
        html += '</tbody></table>';
    }
    html += '</div>';

    html += '<div style="padding:0 24px 24px;">';
    html += '<h2 style="color:' + NAVY + ';font-size:16px;border-bottom:2px solid ' + GOLD + ';padding-bottom:6px;margin:0 0 12px;">Pasaron a Seguimiento</h2>';
    if (totalSeguim === 0) {
        html += '<div style="color:#94a3b8;font-size:13px;font-style:italic;padding:8px 0;">Ninguna esta semana.</div>';
    } else {
        html += '<table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;font-size:12px;">';
        html += '<thead><tr style="background:#f1f5f9;color:#475569;">';
        ['Nombre', 'Ciudad', 'Días desde captación', 'Motivo', 'Responsable'].forEach(function (h) {
            html += '<th style="padding:8px;text-align:left;font-weight:600;border-bottom:1px solid #e2e8f0;">' + h + '</th>';
        });
        html += '</tr></thead><tbody>';
        data.movSeguim.forEach(function (m) {
            var motivo = m.descripcion || '—';
            html += '<tr style="border-bottom:1px solid #f1f5f9;">';
            html += '<td style="padding:8px;"><a href="' + fichaUrl(m.suelo.id) + '" style="color:' + NAVY + ';font-weight:600;text-decoration:none;">' + escapeHTML(m.suelo.nombre || '—') + '</a></td>';
            html += '<td style="padding:8px;color:#475569;">' + escapeHTML(m.suelo.ciudad || '—') + '</td>';
            html += '<td style="padding:8px;color:#475569;">' + (m.diasDesdeCapt != null ? m.diasDesdeCapt + ' d' : '—') + '</td>';
            html += '<td style="padding:8px;color:#475569;font-size:11px;max-width:240px;">' + escapeHTML(String(motivo).substring(0, 140)) + '</td>';
            html += '<td style="padding:8px;color:#475569;">' + escapeHTML(m.suelo.responsable || '—') + '</td>';
            html += '</tr>';
        });
        html += '</tbody></table>';
    }
    html += '</div>';

    var base = process.env.PLATFORM_BASE_URL || 'https://expansion.sficonsulting.es';
    html += '<div style="background:#f8fafc;border-top:1px solid #e2e8f0;padding:16px 24px;text-align:center;font-size:11px;color:#94a3b8;">';
    html += '<a href="' + base + '" style="color:' + GOLD + ';font-weight:600;text-decoration:none;">Ver pipeline completo →</a>';
    html += '<div style="margin-top:6px;">SFI Consulting · Plataforma de Expansión · Resumen automatizado semanal</div>';
    html += '</div>';

    html += '</div>';
    return html;
}

async function gatherData(context) {
    var win = getLastWeekWindow();
    var fromISO = win.from.toISOString();
    var toExclusiveISO = win.toExclusive.toISOString();
    var fromYMD = ymd(win.from);
    var toYMD = ymd(win.to);

    if (context && context.log) context.log('Ventana: ' + fromYMD + ' → ' + toYMD);

    var nuevas = await sbGet('suelos?select=id,nombre,ciudad,provincia,negocio,vertical,num_viviendas,precio_suelo,superficie_edificable,pvp_zona_manual,score_total,responsable,pipeline_estado,fecha_captacion&fecha_captacion=gte.' + fromYMD + '&fecha_captacion=lte.' + toYMD, context);
    var actividades = await sbGet('actividades?select=*&tipo=eq.cambio_fase&fecha=gte.' + fromISO + '&fecha=lt.' + toExclusiveISO, context);

    var movFase1 = [];
    var movSeguim = [];
    var sueloIds = {};
    actividades.forEach(function (a) {
        var cambios = a.cambios || [];
        if (typeof cambios === 'string') { try { cambios = JSON.parse(cambios); } catch (e) { cambios = []; } }
        var faseChange = cambios.find && cambios.find(function (c) { return c.campo === 'pipeline_estado'; });
        if (!faseChange) return;
        var nuevo = faseChange.nuevo || '';
        if (nuevo.indexOf('Fase 1') === 0) {
            sueloIds[a.suelo_id] = true;
            movFase1.push({ act: a, sueloId: a.suelo_id, anterior: faseChange.anterior, nuevo: faseChange.nuevo, usuario: a.usuario, descripcion: a.descripcion });
        } else if (nuevo === 'Seguimiento') {
            sueloIds[a.suelo_id] = true;
            movSeguim.push({ act: a, sueloId: a.suelo_id, anterior: faseChange.anterior, nuevo: faseChange.nuevo, usuario: a.usuario, descripcion: a.descripcion });
        }
    });

    var ids = Object.keys(sueloIds);
    var sueloMap = {};
    nuevas.forEach(function (s) { sueloMap[s.id] = s; });
    if (ids.length) {
        var query = 'suelos?select=id,nombre,ciudad,negocio,responsable,fecha_captacion,pvp_zona_manual,superficie_edificable&id=in.(' + ids.join(',') + ')';
        var sueloRows = await sbGet(query, context);
        sueloRows.forEach(function (s) { sueloMap[s.id] = s; });
    }

    movFase1.forEach(function (m) {
        m.suelo = sueloMap[m.sueloId] || { id: m.sueloId, nombre: '(suelo no encontrado)' };
        m.diasFase0 = diasEntre(m.suelo.fecha_captacion, m.act.fecha);
    });
    movSeguim.forEach(function (m) {
        m.suelo = sueloMap[m.sueloId] || { id: m.sueloId, nombre: '(suelo no encontrado)' };
        m.diasDesdeCapt = diasEntre(m.suelo.fecha_captacion, m.act.fecha);
    });

    return { win: win, nuevas: nuevas, movFase1: movFase1, movSeguim: movSeguim };
}

async function runDigest(context) {
    var data = await gatherData(context);
    var html = buildDigestHTML({ from: data.win.from, to: data.win.to, nuevas: data.nuevas, movFase1: data.movFase1, movSeguim: data.movSeguim });

    var emails = [];
    var groupName = 'Grupo 5 - Resumen Semanal';
    try {
        var cfg = await sbGet('configuracion?clave=eq.emailGroups&select=valor', context);
        if (cfg && cfg[0] && cfg[0].valor && cfg[0].valor.grupo5) {
            emails = cfg[0].valor.grupo5.emails || [];
            groupName = cfg[0].valor.grupo5.nombre || groupName;
        }
    } catch (e) { if (context && context.log) context.log.warn('No se pudo leer emailGroups:', e.message); }

    var counts = { nuevas: data.nuevas.length, movFase1: data.movFase1.length, movSeguim: data.movSeguim.length };
    if (!emails.length) {
        if (context && context.log) context.log.warn('Grupo 5 sin emails — no se envía nada');
        return { ok: false, reason: 'grupo5 sin emails', counts: counts };
    }

    var subject = '[SFI Expansión] ' + fmtRangoES(data.win.from, data.win.to) + ' — ' + counts.nuevas + ' nuevas, ' + counts.movFase1 + ' a Fase 1';
    var sendPayload = JSON.stringify({ to: emails, subject: subject, html: html });
    var sendUrl = process.env.PLATFORM_BASE_URL || 'https://lively-plant-019445f1e.2.azurestaticapps.net';
    var sendHost = new URL(sendUrl).hostname;
    var sendRes = await httpsRequest({
        hostname: sendHost,
        path: '/api/send-email',
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(sendPayload) }
    }, sendPayload);

    if (sendRes.status >= 200 && sendRes.status < 300) {
        if (context && context.log) context.log('Resumen enviado a ' + emails.length + ' destinatarios');
        return { ok: true, sent_to: emails, group: groupName, counts: counts };
    } else {
        if (context && context.log) context.log.error('send-email error ' + sendRes.status + ': ' + sendRes.body);
        return { ok: false, reason: 'send-email error', status: sendRes.status, detail: sendRes.body, counts: counts };
    }
}

module.exports = async function (context, req) {
    if (req && req.method === 'OPTIONS') {
        context.res = { status: 204, headers: Object.assign({}, CORS, { 'Access-Control-Max-Age': '86400' }) };
        return;
    }
    if (!process.env.SUPABASE_URL || !process.env.SUPABASE_ANON_KEY) {
        context.res = { status: 500, headers: Object.assign({ 'Content-Type': 'application/json' }, CORS),
            body: JSON.stringify({ error: 'SUPABASE_URL o SUPABASE_ANON_KEY no configurados' }) };
        return;
    }
    var preview = req && req.query && req.query.preview === '1';
    try {
        if (preview) {
            var data = await gatherData(context);
            var html = buildDigestHTML({ from: data.win.from, to: data.win.to, nuevas: data.nuevas, movFase1: data.movFase1, movSeguim: data.movSeguim });
            context.res = { status: 200, headers: Object.assign({ 'Content-Type': 'text/html; charset=utf-8' }, CORS), body: html };
            return;
        }
        var result = await runDigest(context);
        var statusCode;
        if (result.ok) statusCode = 200;
        else if (result.reason === 'grupo5 sin emails') statusCode = 200;
        else statusCode = 500;
        context.res = { status: statusCode, headers: Object.assign({ 'Content-Type': 'application/json' }, CORS), body: JSON.stringify(result) };
    } catch (e) {
        context.log.error('weekly-digest error:', e.message, e.stack);
        context.res = { status: 500, headers: Object.assign({ 'Content-Type': 'application/json' }, CORS),
            body: JSON.stringify({ error: e.message, path: e.path, supabase_status: e.status, supabase_body: e.responseBody }) };
    }
};

