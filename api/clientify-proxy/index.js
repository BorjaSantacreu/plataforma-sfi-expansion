const https = require('https');
const url = require('url');

// Proxy para Clientify API — evita CORS al llamar desde el frontend
// Azure SWA stripea el header Authorization, así que la key va aquí
const CLIENTIFY_TARGET = 'https://api.clientify.net/v1/';
const CLIENTIFY_API_KEY = process.env.CLIENTIFY_API_KEY || 'd8ba53f111260a3668a588266ce1aa355df315c9';

module.exports = async function (context, req) {
    // CORS preflight
    if (req.method === 'OPTIONS') {
        context.res = {
            status: 204,
            headers: {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, PUT, PATCH, DELETE, OPTIONS',
                'Access-Control-Allow-Headers': 'Authorization, Content-Type, X-CF-Token',
                'Access-Control-Max-Age': '86400'
            }
        };
        return;
    }

    var path = req.params.path || '';
    var targetUrl = CLIENTIFY_TARGET + path;

    // Forward query params (excluir 'code' que Azure inyecta)
    var queryParts = [];
    Object.keys(req.query || {}).forEach(function(k) {
        if (k !== 'code') {
            queryParts.push(encodeURIComponent(k) + '=' + encodeURIComponent(req.query[k]));
        }
    });
    if (queryParts.length > 0) {
        targetUrl += '?' + queryParts.join('&');
    }

    var parsed = new url.URL(targetUrl);

    var headers = {
        'Content-Type': 'application/json',
        'Authorization': 'Token ' + CLIENTIFY_API_KEY
    };

    var options = {
        hostname: parsed.hostname,
        path: parsed.pathname + parsed.search,
        method: req.method,
        headers: headers
    };

    return new Promise(function(resolve) {
        var proxyReq = https.request(options, function(proxyRes) {
            var body = '';
            proxyRes.on('data', function(chunk) { body += chunk; });
            proxyRes.on('end', function() {
                context.res = {
                    status: proxyRes.statusCode,
                    headers: {
                        'Content-Type': proxyRes.headers['content-type'] || 'application/json',
                        'Access-Control-Allow-Origin': '*'
                    },
                    body: body,
                    isRaw: true
                };
                resolve();
            });
        });

        proxyReq.on('error', function(err) {
            context.log.error('Clientify proxy error:', err.message);
            context.res = {
                status: 502,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ error: 'Proxy error: ' + err.message })
            };
            resolve();
        });

        if (req.body && (req.method === 'POST' || req.method === 'PUT' || req.method === 'PATCH')) {
            var bodyStr = typeof req.body === 'string' ? req.body : JSON.stringify(req.body);
            proxyReq.setHeader('Content-Length', Buffer.byteLength(bodyStr));
            proxyReq.write(bodyStr);
        }

        proxyReq.end();
    });
};
