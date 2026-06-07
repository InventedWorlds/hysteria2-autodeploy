# hysteria2-autodeploy

Автоматическая установка и управление **Hysteria2** (UDP/QUIC + salamander obfs) на
вашем VPS одной командой. Нужно знать только **IP, SSH-логин, SSH-пароль и домен** —
скрипт сам поставит TLS-сертификат, hysteria, systemd-сервис, firewall и сгенерирует
готовую ссылку для клиента **v2rayN** (и совместимых: Hiddify, NekoBox, sing-box).

Hysteria2 — UDP-протокол поверх QUIC с обфускацией. В отличие от TCP/TLS-протоколов
(VLESS/Reality/XHTTP и т.п.), он устойчив к поведенческому анализу DPI: handshake
спрятан обфускацией salamander, а активное зондирование видит «мёртвый» UDP-порт.

> Инструмент предназначен для законного обеспечения приватности и доступа к
> собственным ресурсам. Используйте в соответствии с законами вашей юрисдикции.

---

## Возможности

- `install` — полная установка на чистый сервер: certbot + сертификат Let's Encrypt,
  `apernet/hysteria`, `systemd`-сервис, `ufw`, конфиг, deploy-hook для автопродления серта.
- Многопользовательский режим (`auth: userpass`) из коробки: у каждого свой пароль и
  своя ссылка, общий obfs-пароль на сервер.
- `adduser` / `deluser` / `users` / `link` — управление пользователями, на выходе —
  готовая ссылка `hysteria2://...`.
- Идемпотентность: повторный `install` не ломает установку и сохраняет пользователей.
- Кросс-платформенно (Windows/macOS/Linux), работа по SSH через `paramiko`.

## Как это работает

```
            UDP/443
client ───────────────▶ hysteria-server (демон apernet/hysteria)
        QUIC + salamander    └─ egress → интернет
```

Источник правды на сервере — `/etc/hysteria/hy2meta.json` (домен, порт, obfs-пароль,
список пользователей). Файл `/etc/hysteria/config.yaml` пересобирается из него при каждом
изменении, поэтому управляйте пользователями только через `hy2.py`.

> **Почему отдельный демон, а не через Xray/3x-ui:** Xray-core не реализует серверный
> Hysteria2 — он молча открывает TCP-сокет вместо UDP/QUIC. Настоящий Hysteria2 — это
> отдельный демон `apernet/hysteria`, который ставит этот скрипт.

## Требования

- Python 3.9+ на вашей машине.
- VPS на Ubuntu/Debian с root-доступом по SSH (паролю).
- Домен с **A-записью на IP сервера** (нужно для TLS-сертификата Let's Encrypt).
- Открытый входящий **UDP-порт** (по умолчанию 443) — в т.ч. в облачном firewall провайдера.

## Установка инструмента

```bash
git clone https://github.com/InventedWorlds/hysteria2-autodeploy.git
cd hysteria2-autodeploy
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Linux/macOS:  source .venv/bin/activate
pip install -r requirements.txt
```

## Настройка доступа

Данные серверов можно передавать флагами или хранить в `.env`. Скопируйте шаблон:

```bash
cp .env.example .env
```

`.env` (нумерация: `1` — без суффикса, `2`, `3`, ...):

```
Address=YOUR_SERVER_IP
Port=22
User=root
Password=YOUR_SSH_PASSWORD
DNS=example.com
```

> `.env` уже в `.gitignore` — секреты не попадут в репозиторий.

## Использование

```bash
# Полная установка (данные из .env по номеру сервера)
python hy2.py install --server 1
# ...или без .env, явно:
python hy2.py install --host YOUR_SERVER_IP --user root --password YOUR_PW --domain example.com

python hy2.py adduser --server 1 --name alice   # создать юзера -> печатает ссылку
python hy2.py users   --server 1                # все юзеры + их ссылки
python hy2.py link    --server 1 --name alice   # ссылка конкретного юзера
python hy2.py deluser --server 1 --name alice   # удалить юзера
python hy2.py status  --server 1                # состояние сервиса
```

`install` создаёт первого пользователя `main` и печатает его ссылку. Пример вывода:

```
==> ГОТОВО. Ссылки для v2rayN:
  [main]
  hysteria2://main:<password>@example.com:443/?obfs=salamander&obfs-password=<obfs>&sni=example.com#example.com-main
```

## Клиент v2rayN

1. v2rayN 7.x поддерживает Hysteria2 (встроенное ядро / отдельный бинарь hysteria).
2. Серверы → импорт из буфера обмена → вставить ссылку `hysteria2://...`.
3. Выбрать сервер, включить системный прокси/TUN.

## Если домен ещё не указывает на сервер

Сертификат Let's Encrypt выпускается только если A-запись домена уже резолвится в IP
сервера. Сначала пропишите DNS (`dig +short ВАШ_ДОМЕН` должен вернуть IP сервера),
затем запускайте `install`. Скрипт предупредит о несоответствии.

## Ручная установка

Если нужен полный контроль или другое окружение — в [`DEPLOY.md`](DEPLOY.md) расписаны
те же шаги вручную (certbot, hysteria, config.yaml, systemd, ufw, port hopping и т.д.).

## Безопасность

- Секреты (`.env`) и виртуальное окружение (`.venv/`) исключены через `.gitignore`.
- Пароли auth/obfs генерируются случайно (`secrets`) для каждого сервера.
- Сервис hysteria работает под непривилегированным пользователем; доступ к сертификатам
  выдаётся через копию в `/etc/hysteria/certs` + deploy-hook certbot (автопродление).

## Лицензия

MIT — см. [`LICENSE`](LICENSE).
