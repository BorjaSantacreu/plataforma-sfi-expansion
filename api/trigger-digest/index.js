// Azure Function: trigger-digest
// Dispara el workflow weekly-digest en GitHub Actions usando GITHUB_PAT
// Necesita en App Settings: GITHUB_PAT (con scope 'repo' o 'workflow')

const https = require('https');
const CORS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type'
};

function postJSON(opts, body) {
    return new Promise(function (resolve, reject) {
        var req = https.request(opts, function (res) {
            var chunks = [];
            res.on('data', function (c) { chunks.push(c); });
            res.on('end', function () { resolve({ status: res.statusCode, body: Buffer.concat(chunks).toString('utf8') }); });
        });
        req.on('error', reject);
        if (body) req.write(body);
        req.end();
    });
}

module.exports = async function (context, req) {
    if (req.method === 'OPTIONS') {
        context.res = { status: 204, headers: Object.assign({}, CORS, { 'Access-Control-Max-Age': '86400' }) };
        return;
    }
    var token = process.env.GITHUB_PAT;
    var repo = process.env.GITHUB_REPO || 'BorjaSantacreu/plataforma-sfi-expansion';
    var workflow = process.env.GITHUB_WORKFLOW_FILE || 'weekly-digest.yml';
    if (!token) {
        context.res = { status: 500, headers: Object.assign({ 'Content-Type': 'application/json' }, CORS),
            body: JSON.stringify({ error: 'GITHUB_PAT no configurado en Application Settings' }) };
        return;
    }
    try {
        var body = JSON.stringify({ ref: 'main' });
        var res = await postJSON({
            hostname: 'api.github.com',
            path: '/repos/' + repo + '/actions/workflows/' + workflow + '/dispatches',
            method: 'POST',
            headers: {
                'Authorization': 'Bearer ' + token,
                'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28',
                'User-Agent': 'SFI-Expansion-Platform',
                'Content-Type': 'application/json',
                'Content-Length': Buffer.byteLength(body)
            }
        }, body);
        if (res.status === 204) {
            context.res = { status: 200, headers: Object.assign({ 'Content-Type': 'application/json' }, CORS),
                body: JSON.stringify({ ok: true, message: 'Workflow disparado. El correo llegara en 30-60 segundos.',
                    workflow_url: 'https://github.com/' + repo + '/actions/workflows/' + workflow }) };
        } else {
            context.res = { status: res.status, headers: Object.assign({ 'Content-Type': 'application/json' }, CORS),
                body: JSON.stringify({ error: 'GitHub dispatch failed', status: res.status, detail: res.body }) };
        }
    } catch (e) {
        context.res = { status: 500, headers: Object.assign({ 'Content-Type': 'application/json' }, CORS),
            body: JSON.stringify({ error: e.message }) };
    }
};
