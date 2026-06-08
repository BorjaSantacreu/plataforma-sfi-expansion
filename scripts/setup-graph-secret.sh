#!/bin/bash
# Crea un client secret en el App Registration y lo configura en la SWA

set -e

if ! az account show > /dev/null 2>&1; then
  echo "Sesion Azure no valida. Ejecuta: az login"
  exit 1
fi

echo "=== Creando client secret en App Registration 199acdf3 ==="
GS=$(az ad app credential reset \
  --id 199acdf3-4cee-4f53-9389-e4f1e91283fe \
  --display-name "SFI Weekly Digest $(date +%Y%m%d)" \
  --years 2 \
  --append \
  --output tsv --query "password" 2>&1)

if [ -z "$GS" ] || echo "$GS" | grep -qi "error\|fail\|permission"; then
  echo "ERROR al crear secret:"
  echo "$GS"
  exit 1
fi
echo "OK - Secret creado (longitud ${#GS})"

echo ""
echo "=== Configurando GRAPH_CLIENT_SECRET en SWA ==="
az staticwebapp appsettings set \
  --name sfi-plataforma-expansion \
  --resource-group sfi-plataforma-expansion_group \
  --setting-names "GRAPH_CLIENT_SECRET=$GS" \
  --output none
echo "OK - App setting actualizada"

echo ""
echo "=== Esperando 25s a que la Function recargue env ==="
sleep 25

echo ""
echo "=== Test POST /api/weekly-digest ==="
curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X POST \
  "https://expansion.sficonsulting.es/api/weekly-digest" \
  -H "Content-Type: application/json" -d '{}'
