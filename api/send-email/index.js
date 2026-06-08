// Azure Function: send-email
// Proxy a Microsoft Graph sendMail. Espera body JSON: { to: [emails], subject, html, from? }
// Credenciales en Application Settings de la SWA:
//   - GRAPH_TENANT_ID
//   - GRAPH_CLIENT_ID
//   - GRAPH_CLIENT_SECRET
//   - GRAPH_SENDER_UPN  (mailbox emisor, p.ej. notificaciones@sficonsulting.es)
const https = require('https');

const CORS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
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

async function getGraphToken(tenant, clientId, clientSecret) {
    var form = 'client_id=' + encodeURIComponent(clientId) +
        '&scope=' + encodeURIComponent('https://graph.microsoft.com/.default') +
        '&client_secret=' + encodeURIComponent(clientSecret) +
        '&grant_type=client_credentials';
    var res = await httpsRequest({
        hostname: 'login.microsoftonline.com',
        path: '/' + tenant + '/oauth2/v2.0/token',
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Content-Length': Buffer.byteLength(form)
        }
    }, form);
    if (res.status !== 200) throw new Error('Token error ' + res.status + ': ' + res.body);
    var data = JSON.parse(res.body);
    return data.access_token;
}

async function sendMail(token, sender, payload) {
    var body = JSON.stringify(payload);
    var res = await httpsRequest({
        hostname: 'graph.microsoft.com',
        path: '/v1.0/users/' + encodeURIComponent(sender) + '/sendMail',
        method: 'POST',
        headers: {
            'Authorization': 'Bearer ' + token,
            'Content-Type': 'application/json',
            'Content-Length': Buffer.byteLength(body)
        }
    }, body);
    return res;
}

module.exports = async function (context, req) {
    if (req.method === 'OPTIONS') {
        context.res = { status: 204, headers: Object.assign({}, CORS, { 'Access-Control-Max-Age': '86400' }) };
        return;
    }

    var tenant = process.env.GRAPH_TENANT_ID || 'fb058e82-dd7a-4de9-a0ab-2850c98a7316';
    var clientId = process.env.GRAPH_CLIENT_ID || '199acdf3-4cee-4f53-9389-e4f1e91283fe';
    var clientSecret = process.env.GRAPH_CLIENT_SECRET || '';
    var sender = process.env.GRAPH_SENDER_UPN || 'bsantacreu@sficonsulting.es';

    if (!clientSecret) {
        context.res = { status: 500, headers: Object.assign({ 'Content-Type': 'application/json' }, CORS),
            body: JSON.stringify({ error: 'GRAPH_CLIENT_SECRET no configurado en Application Settings de la SWA' }) };
        return;
    }

    var body = req.body || {};
    if (typeof body === 'string') {
        try { body = JSON.parse(body); } catch (e) { body = {}; }
    }

    var to = body.to;
    if (typeof to === 'string') to = [to];
    if (!Array.isArray(to) || to.length === 0) {
        context.res = { status: 400, headers: Object.assign({ 'Content-Type': 'application/json' }, CORS),
            body: JSON.stringify({ error: 'Falta el campo "to" (array de emails)' }) };
        return;
    }
    var subject = body.subject || '(sin asunto)';
    var html = body.html || body.text || '';
    var sendFrom = body.from || sender;

    var payload = {
        message: {
            subject: subject,
            body: { contentType: 'HTML', content: html },
            toRecipients: to.map(function (e) { return { emailAddress: { address: e } }; })
        },
        saveToSentItems: true
    };
    if (body.cc && Array.isArray(body.cc) && body.cc.length) {
        payload.message.ccRecipients = body.cc.map(function (e) { return { emailAddress: { address: e } }; });
    }

    try {
        var token = await getGraphToken(tenant, clientId, clientSecret);
        var res = await sendMail(token, sendFrom, payload);
        if (res.status >= 200 && res.status < 300) {
            context.res = { status: 200, headers: Object.assign({ 'Content-Type': 'application/json' }, CORS),
                body: JSON.stringify({ ok: true, sent: to.length }) };
        } else {
            context.log.warn('Graph sendMail error', res.status, res.body);
            context.res = { status: res.status, headers: Object.assign({ 'Content-Type': 'application/json' }, CORS),
                body: JSON.stringify({ error: 'Graph error ' + res.status, detail: res.body }) };
        }
    } catch (e) {
        context.log.error('send-email error', e.message);
        context.res = { status: 500, headers: Object.assign({ 'Content-Type': 'application/json' }, CORS),
            body: JSON.stringify({ error: e.message }) };
    }
};
