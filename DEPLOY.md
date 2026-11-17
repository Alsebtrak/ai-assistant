# Деплой

## 1. ВМ

```bash
yc compute instance create assistant \
  --zone ru-central1-d \
  --network-interface subnet-name=default-ru-central1-d,nat-ip-version=ipv4,security-group-ids=<sg-id> \
  --create-boot-disk image-family=ubuntu-2404-lts-oslogin,image-folder-id=standard-images,size=50,type=network-ssd \
  --cores 2 --core-fraction 20 --memory 4 \
  --metadata-from-file user-data=infra/cloud-init.yaml,ssh-keys=.secrets/ssh_keys.txt
```

cloud-init ставит Docker, compose, ufw (22/80/443), fail2ban, rclone и
настраивает приоритет IPv4 (в YC нет IPv6, иначе apt/curl уходят в таймаут).

**Обязательно:** у security group должно быть egress-правило, иначе у ВМ нет
интернета вообще:

```bash
yc vpc security-group update-rules assistant \
  --add-rule "direction=egress,port=any,protocol=any,v4-cidrs=[0.0.0.0/0]"
```

## 2. Проверить Telegram с сервера

```bash
ssh -i .secrets/ssh_key ubuntu@<ip> \
  "curl -s -o /dev/null -w '%{http_code}\n' --max-time 10 https://api.telegram.org"
```

`000` с основного IP — норма: `149.154.166.110` из РФ блокирован.
cloud-init прописывает в `/etc/hosts` рабочий `149.154.167.220`, после этого
ответ `302` и бот работает без прокси. Если IP перестанет отвечать — заменить
на другой DC Telegram или включить `TELEGRAM_PROXY_URL`.

## 3. Код

```powershell
.\infra\deploy.ps1 -HostName <ip>
```

Скрипт собирает архив (без `.git`, `.venv`, `.secrets`, `credits.yml`), заливает его
и `.env`, запускает `docker compose up -d --build` и проверяет `/health`.

## 4. Бэкапы

```bash
ssh -i .secrets/ssh_key ubuntu@<ip> \
  "chmod +x ~/ai_assistant/infra/backup.sh && \
   (crontab -l 2>/dev/null; echo '0 3 * * * /home/ubuntu/ai_assistant/infra/backup.sh >> /var/log/assistant-backup.log 2>&1') | crontab -"
```

## 5. Домен и TLS

Пока домена нет: API доступен по `http://<ip>/health`. Когда появится домен —
certbot на хосте или Caddy вместо nginx, порт 80 остаётся для ACME.

## Проверки после деплоя

```bash
curl http://<ip>/health
docker compose ps
docker compose logs -f bot
```

Бот должен ответить в Telegram на `/start`. Если молчит — смотреть, что
`TELEGRAM_PROXY_URL` не задан при недоступном `api.telegram.org`.
