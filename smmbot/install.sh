#!/usr/bin/env bash
# Установка бота как службы systemd. Запуск: sudo bash install.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if command -v apt-get >/dev/null; then
  apt-get update -qq && apt-get install -y -qq python3 python3-venv python3-pip >/dev/null
fi

python3 -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt
chmod 600 .env

cat > /etc/systemd/system/smmbot.service <<EOF
[Unit]
Description=izi.market x smmway Telegram bot
After=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
echo
echo "Проверка ключей:"
./venv/bin/python check.py
echo
if grep -q '^BOT_TOKEN=.\+' .env && grep -q '^ADMIN_IDS=.\+' .env; then
  systemctl enable --now smmbot
  systemctl restart smmbot
  echo "Бот запущен. Логи: journalctl -u smmbot -f"
else
  echo "Впишите BOT_TOKEN и ADMIN_IDS в .env и запустите: sudo systemctl enable --now smmbot"
fi
