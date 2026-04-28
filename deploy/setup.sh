#!/bin/bash
# setup.sh — Adiciona um novo cliente ao VPS
# Uso: ./setup.sh nome_cliente subdominio.seudominio.com.br porta
# Ex:  ./setup.sh joao joao.meubot.com.br 8003

set -e

NOME=$1
DOMINIO=$2
PORTA=$3

if [ -z "$NOME" ] || [ -z "$DOMINIO" ] || [ -z "$PORTA" ]; then
  echo "Uso: ./setup.sh <nome_cliente> <dominio> <porta>"
  echo "Ex:  ./setup.sh joao joao.meubot.com.br 8003"
  exit 1
fi

echo "=== Configurando cliente: $NOME ==="

# 1. Cria estrutura de diretórios
mkdir -p "clientes/$NOME/data/logs"
mkdir -p "clientes/$NOME/data/downloads"

# 2. Gera config.json base se não existir
if [ ! -f "clientes/$NOME/config.json" ]; then
  cp ../config.example.json "clientes/$NOME/config.json"
  sed -i "s|/data/downloads|/data/downloads|g" "clientes/$NOME/config.json"
  echo "✅ config.json criado em clientes/$NOME/"
  echo "   Edite o arquivo e preencha as credenciais antes de subir o container."
fi

# 3. Adiciona serviço ao docker-compose.yml
if grep -q "container_name: bot_$NOME" docker-compose.yml; then
  echo "⚠️  Cliente $NOME já existe no docker-compose.yml"
else
  cat >> docker-compose.yml << EOF

  $NOME:
    build:
      context: ..
      dockerfile: Dockerfile
    container_name: bot_$NOME
    restart: unless-stopped
    volumes:
      - ./clientes/$NOME/config.json:/app/config.json:ro
      - ./clientes/$NOME/data:/data
    environment:
      - BOT_DATA_DIR=/data
    ports:
      - "$PORTA:8000"
    shm_size: '256mb'
EOF
  echo "✅ Serviço adicionado ao docker-compose.yml (porta $PORTA)"
fi

# 4. Configura nginx
NGINX_FILE="/etc/nginx/sites-available/bot-$NOME"
if [ ! -f "$NGINX_FILE" ]; then
  cat > "$NGINX_FILE" << EOF
server {
    listen 80;
    server_name $DOMINIO;
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl;
    server_name $DOMINIO;

    ssl_certificate     /etc/letsencrypt/live/$DOMINIO/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMINIO/privkey.pem;

    client_max_body_size 100M;

    location / {
        proxy_pass         http://127.0.0.1:$PORTA;
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
EOF
  ln -sf "$NGINX_FILE" "/etc/nginx/sites-enabled/bot-$NOME"
  echo "✅ Config nginx criada para $DOMINIO"
fi

# 5. Gera certificado SSL
echo ""
echo "=== Próximos passos ==="
echo "1. Edite clientes/$NOME/config.json com as credenciais do cliente"
echo "2. Aponte o DNS $DOMINIO → IP deste servidor"
echo "3. Execute: certbot --nginx -d $DOMINIO"
echo "4. Execute: docker-compose up -d $NOME"
echo "5. Acesse: https://$DOMINIO"
echo ""
echo "Deploy de $NOME concluído em menos de 30 minutos ✅"
