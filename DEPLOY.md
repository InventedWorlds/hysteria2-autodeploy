# Развёртывание Hysteria2-обхода на новом сервере (с нуля)

Пошаговая инструкция: как поднять такой же UDP/QUIC-обход (Hysteria2 + salamander)
на чистом VPS. Не требует 3x-ui/Xray — Hysteria2 ставится отдельным демоном.

Проверено на Ubuntu 22.04/24.04 (Debian аналогично). Все команды — под `root`
(или через `sudo`).

---

## Автоматический способ (рекомендуется): `hy2.py`

Скрипт `hy2.py` делает всё сам по SSH (через `paramiko`). Нужно знать только
**IP, SSH-логин, SSH-пароль и домен**. На выходе — готовая ссылка для v2rayN.

### Подготовка (один раз, на своей машине)

```bash
pip install -r requirements.txt   # ставит paramiko
```

Данные серверов удобно держать в `.env` (нумерация: `1` — без суффикса, `2`, `3`, ...):

```
Address2=YOUR_SERVER_IP
Port2=22
User2=root
Password2=YOUR_SSH_PASSWORD
DNS2=example.com
```

> Перед установкой убедитесь, что A-запись домена указывает на IP сервера —
> иначе Let's Encrypt не выпустит сертификат (скрипт об этом предупредит).

### Команды

```bash
# Полная установка (берёт данные из .env по номеру сервера)
python hy2.py install --server 2
# ...или без .env, явно:
python hy2.py install --host YOUR_SERVER_IP --user root --password YOUR_PW --domain example.com

python hy2.py adduser --server 2 --name alice   # создать юзера -> печатает ссылку
python hy2.py users   --server 2                # все юзеры + их ссылки
python hy2.py link    --server 2 --name alice   # ссылка конкретного юзера
python hy2.py deluser --server 2 --name alice   # удалить юзера
python hy2.py status  --server 2                # состояние сервиса
```

`install` ставит certbot+сертификат, hysteria, systemd-сервис, ufw, конфиг и создаёт
первого пользователя `main`. Многопользовательский режим (`auth: userpass`) включён
сразу: каждый юзер — свой пароль, своя ссылка; `obfs`-пароль общий на сервер.

Источник правды на сервере — `/etc/hysteria/hy2meta.json`; `config.yaml` пересобирается
из него при каждом изменении (поэтому правьте пользователей только через `hy2.py`).

Команды идемпотентны: повторный `install` не ломает существующую установку и сохраняет
пользователей.

---

## Ручной способ (что именно делает скрипт, по шагам)

Ниже — те же действия вручную, если нужен полный контроль или другой ОС/окружение.

## 0. Что понадобится

| Что | Зачем |
|---|---|
| VPS вне РФ (Ubuntu/Debian) | сам сервер обхода |
| Домен (или субдомен) | для валидного TLS-сертификата Let's Encrypt |
| Доступ root по SSH | установка и настройка |
| Открытый UDP-порт (по умолчанию 443) | транспорт Hysteria2 |

> **Важно про порт 443.** Hysteria2 слушает **UDP/443**. Это не конфликтует с
> веб-сервером на **TCP/443** (nginx/apache) — протоколы разные. Если на сервере
> уже есть сайт на 443 — всё совместимо.

---

## 1. DNS: привязать домен к серверу

В панели управления доменом создать **A-запись**:

```
example.com.   A   <IP_СЕРВЕРА>
```

Проверить (должен вернуться IP сервера):

```bash
dig +short example.com
```

Дальше в инструкции замените `example.com` на свой домен.

---

## 2. Получить TLS-сертификат (Let's Encrypt)

Ставим certbot и выпускаем сертификат. Вариант зависит от того, занят ли TCP/80.

**Вариант А — на сервере нет веб-сервера (порт 80 свободен):**

```bash
apt update && apt install -y certbot
certbot certonly --standalone -d example.com --non-interactive --agree-tos -m you@example.com
```

**Вариант Б — уже есть nginx:**

```bash
apt install -y certbot python3-certbot-nginx
certbot certonly --nginx -d example.com --non-interactive --agree-tos -m you@example.com
```

Сертификаты появятся в `/etc/letsencrypt/live/example.com/`.
Автопродление уже настроено через `certbot.timer` (проверить: `systemctl status certbot.timer`).

---

## 3. Установить сервер Hysteria2

Официальный установщик (ставит бинарь `/usr/local/bin/hysteria` и
`systemd`-сервис `hysteria-server.service`, создаёт системного пользователя `hysteria`):

```bash
bash <(curl -fsSL https://get.hy2.sh/)
hysteria version    # проверка
```

---

## 4. Сгенерировать пароли

Каждому новому серверу — **новые** случайные пароли (auth + obfs):

```bash
AUTH_PW=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-24)
OBFS_PW=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-24)
echo "AUTH_PW=$AUTH_PW"
echo "OBFS_PW=$OBFS_PW"
```

Запишите оба значения — они нужны в конфиге и в клиентской ссылке.

---

## 5. Доступ к сертификатам для пользователя `hysteria`

Сервис работает не от root, а каталог `/etc/letsencrypt` доступен только root.
Делаем копию сертификатов для `hysteria` + хук, который обновляет её при каждом
продлении сертификата.

Создать хук `/etc/letsencrypt/renewal-hooks/deploy/hysteria.sh`:

```bash
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/hysteria.sh <<'EOF'
#!/bin/bash
set -e
SRC=/etc/letsencrypt/live/example.com
DST=/etc/hysteria/certs
mkdir -p "$DST"
cp -L "$SRC/fullchain.pem" "$DST/fullchain.pem"
cp -L "$SRC/privkey.pem" "$DST/privkey.pem"
chown -R hysteria:hysteria "$DST"
chmod 644 "$DST/fullchain.pem"
chmod 600 "$DST/privkey.pem"
systemctl restart hysteria-server.service 2>/dev/null || true
EOF
chmod +x /etc/letsencrypt/renewal-hooks/deploy/hysteria.sh
```

> Замените `example.com` в хуке на свой домен.

Прогнать хук один раз (создаст копию сейчас):

```bash
bash /etc/letsencrypt/renewal-hooks/deploy/hysteria.sh
ls -l /etc/hysteria/certs/    # должны быть fullchain.pem (644) и privkey.pem (600), владелец hysteria
```

> Если файл писали в Windows — почистить переводы строк: `sed -i 's/\r$//' файл`.

---

## 6. Конфиг сервера `/etc/hysteria/config.yaml`

```bash
cat > /etc/hysteria/config.yaml <<EOF
listen: :443

tls:
  cert: /etc/hysteria/certs/fullchain.pem
  key: /etc/hysteria/certs/privkey.pem

obfs:
  type: salamander
  salamander:
    password: $OBFS_PW

auth:
  type: password
  password: $AUTH_PW

masquerade:
  type: proxy
  proxy:
    url: https://www.bing.com/
    rewriteHost: true

quic:
  initStreamReceiveWindow: 8388608
  maxStreamReceiveWindow: 8388608
  initConnReceiveWindow: 20971520
  maxConnReceiveWindow: 20971520
EOF
```

> `$AUTH_PW` / `$OBFS_PW` подставятся, если конфиг создаёте в той же сессии, где
> генерировали пароли (раздел 4). Иначе впишите значения вручную.
>
> Назначение полей:
> - `listen: :443` — слушать UDP/443.
> - `obfs.salamander` — обфускация: без этого пароля сервер не отвечает (анти-зондирование).
> - `auth.password` — пароль клиента.
> - `masquerade` — на «левый» HTTP-запрос притворяется зеркалом сайта (защита от пробинга).
> - `quic.*` — увеличенные окна для высокой скорости.

---

## 7. Firewall (ufw)

```bash
ufw allow 22/tcp        # не потерять SSH!
ufw allow 443/udp       # Hysteria2
ufw --force enable
ufw status
```

> Если используете облачный firewall провайдера (AWS/GCP/Hetzner Cloud и т.п.) —
> там тоже разрешите **UDP 443** входящим.

---

## 8. Запуск и автозагрузка

```bash
systemctl enable --now hysteria-server.service
systemctl status hysteria-server.service --no-pager
```

В логе должно быть `server up and running {"listen": ":443"}`.

---

## 9. Проверки

```bash
# UDP-слушатель есть?
ss -ulnp | grep ':443'        # ожидаем UNCONN *:443 ... users:(("hysteria",...))

# Логи
journalctl -u hysteria-server -n 30 --no-pager
```

Сквозной тест прямо с сервера (поднимет временный клиент → SOCKS5 → проверит выход):

```bash
cat > /tmp/hy2_test.yaml <<EOF
server: example.com:443
auth: $AUTH_PW
obfs:
  type: salamander
  salamander:
    password: $OBFS_PW
tls:
  sni: example.com
socks5:
  listen: 127.0.0.1:11080
EOF
/usr/local/bin/hysteria client -c /tmp/hy2_test.yaml >/tmp/hy2_test.log 2>&1 &
sleep 4
curl -s --max-time 12 --socks5-hostname 127.0.0.1:11080 https://api.ipify.org; echo
pkill -f hy2_test.yaml; rm -f /tmp/hy2_test.yaml /tmp/hy2_test.log
```

`curl` должен вернуть **IP сервера** — значит туннель работает.

---

## 10. Клиентская ссылка (v2rayN / Hiddify / NekoBox / sing-box)

Собрать ссылку, подставив свои значения:

```
hysteria2://<AUTH_PW>@<ДОМЕН>:443/?obfs=salamander&obfs-password=<OBFS_PW>&sni=<ДОМЕН>#Hysteria2-<имя>
```

Пример:

```
hysteria2://<AUTH_PW>@example.com:443/?obfs=salamander&obfs-password=<OBFS_PW>&sni=example.com#Hysteria2-example.com
```

**v2rayN:** Серверы → импорт из буфера обмена → выбрать сервер → включить
системный прокси/TUN.

---

## 11. Несколько пользователей (опционально)

В `/etc/hysteria/config.yaml` вместо одного пароля:

```yaml
auth:
  type: userpass
  userpass:
    alice: парольAlice
    bob: парольBob
```

Ссылка каждому со своим паролем в части `<AUTH_PW>`. После правки:
`systemctl restart hysteria-server`.

---

## 12. Если порт начнут резать — port hopping (опционально)

Hysteria2 умеет слушать диапазон UDP-портов (усложняет блокировку):

1. В конфиге: `listen: :443` оставить как основной.
2. Открыть диапазон в ufw: `ufw allow 20000:30000/udp`.
3. Пробросить диапазон на 443 через iptables/nftables:
   ```bash
   iptables -t nat -A PREROUTING -p udp --dport 20000:30000 -j REDIRECT --to-ports 443
   ```
4. В клиенте указать диапазон: `server: example.com:20000-30000` и параметр
   `mport`/«port hopping» (зависит от клиента).

---

## 13. Шпаргалка по управлению

```bash
systemctl status hysteria-server      # состояние
systemctl restart hysteria-server     # перезапуск (после правки конфига)
journalctl -u hysteria-server -f      # живые логи
nano /etc/hysteria/config.yaml        # правка конфига
ss -ulnp | grep ':443'                # проверить UDP-слушатель
```

Удаление:

```bash
systemctl disable --now hysteria-server
ufw delete allow 443/udp
bash <(curl -fsSL https://get.hy2.sh/) --remove
```

---

## 14. Чек-лист «всё ли сделано»

- [ ] A-запись домена → IP сервера (`dig +short ДОМЕН`)
- [ ] Сертификат выпущен (`/etc/letsencrypt/live/ДОМЕН/`)
- [ ] Hysteria2 установлен (`hysteria version`)
- [ ] Сгенерированы новые `AUTH_PW` и `OBFS_PW`
- [ ] Deploy-hook создан и прогнан, копия сертов в `/etc/hysteria/certs` (владелец hysteria)
- [ ] `config.yaml` заполнен своими паролями и доменом
- [ ] ufw: 22/tcp + 443/udp разрешены, firewall провайдера тоже
- [ ] Сервис `enabled --now`, в логе `server up and running`
- [ ] `ss -ulnp | grep :443` показывает hysteria на UDP
- [ ] Сквозной тест вернул IP сервера
- [ ] Клиентская ссылка собрана и импортирована в v2rayN
