#!/usr/bin/env python3
"""
hy2.py — автоматическая установка и управление Hysteria2-обходом на сервере.

Пользователю нужно знать только: IP, SSH-логин, SSH-пароль и домен.
Всё остальное (certbot/TLS, hysteria, systemd, ufw, конфиг) ставится само.

Источник правды на сервере — /etc/hysteria/hy2meta.json (домен, порт, obfs-пароль,
пользователи). Файл /etc/hysteria/config.yaml каждый раз пересобирается из него.

Примеры:
  # установить (данные из .env: server 2 => Address2/User2/Password2/DNS2)
  python hy2.py install --server 2
  # или явно:
  python hy2.py install --host 1.2.3.4 --user root --password PW --domain example.com

  python hy2.py adduser --server 2 --name alice    # создать юзера -> печатает ссылку
  python hy2.py users   --server 2                 # список юзеров + ссылки
  python hy2.py link    --server 2 --name alice    # ссылка для юзера
  python hy2.py deluser --server 2 --name alice    # удалить юзера
  python hy2.py status  --server 2                 # состояние сервиса
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from urllib.parse import quote

try:
    import paramiko
except ImportError:
    sys.exit("Нужен paramiko: pip install paramiko")

PORT = 443
MASQUERADE_URL = "https://www.bing.com/"
META_PATH = "/etc/hysteria/hy2meta.json"
CONFIG_PATH = "/etc/hysteria/config.yaml"
HOOK_PATH = "/etc/letsencrypt/renewal-hooks/deploy/hysteria.sh"
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


# --------------------------- утилиты ---------------------------

def gen_password(n: int = 24) -> str:
    """Случайный алфавитно-цифровой пароль (URL-безопасный)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def load_env(path: str = ENV_PATH) -> dict:
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def resolve_creds(args) -> dict:
    """Возвращает {host,port,user,password,domain} из --server (.env) или явных флагов."""
    if args.server:
        env = load_env()
        sfx = "" if str(args.server) == "1" else str(args.server)
        host = env.get(f"Address{sfx}")
        if not host:
            sys.exit(f"В .env нет Address{sfx} для server {args.server}")
        return {
            "host": host,
            "port": int(env.get(f"Port{sfx}", 22)),
            "user": env.get(f"User{sfx}", "root"),
            "password": env.get(f"Password{sfx}", ""),
            "domain": env.get(f"DNS{sfx}", ""),
        }
    if not args.host:
        sys.exit("Укажите --server N или --host/--user/--password/--domain")
    return {
        "host": args.host,
        "port": args.port or 22,
        "user": args.user or "root",
        "password": args.password or "",
        "domain": args.domain or "",
    }


# --------------------------- SSH-обёртка ---------------------------

class SSH:
    def __init__(self, host, port, user, password):
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(
            host, port=port, username=user, password=password,
            timeout=30, banner_timeout=30, auth_timeout=30, look_for_keys=False,
        )

    def run(self, cmd: str, check: bool = False, stream: bool = False) -> tuple[int, str, str]:
        stdin, stdout, stderr = self.client.exec_command(cmd, get_pty=False)
        out_parts, err_parts = [], []
        if stream:
            stdout.channel.set_combine_stderr(True)
            for line in iter(stdout.readline, ""):
                if not line:
                    break
                out_parts.append(line)
                sys.stdout.write("    " + line)
                sys.stdout.flush()
        else:
            out_parts.append(stdout.read().decode("utf-8", "replace"))
        rc = stdout.channel.recv_exit_status()
        err_parts.append(stderr.read().decode("utf-8", "replace"))
        out, err = "".join(out_parts), "".join(err_parts)
        if check and rc != 0:
            raise RuntimeError(f"Команда упала (rc={rc}): {cmd}\nstderr:\n{err}")
        return rc, out, err

    def put(self, content: str, remote_path: str, mode: int | None = None):
        sftp = self.client.open_sftp()
        try:
            with sftp.file(remote_path, "w") as f:
                f.write(content)
            if mode is not None:
                sftp.chmod(remote_path, mode)
        finally:
            sftp.close()

    def read(self, remote_path: str) -> str | None:
        sftp = self.client.open_sftp()
        try:
            with sftp.file(remote_path, "r") as f:
                return f.read().decode("utf-8", "replace")
        except IOError:
            return None
        finally:
            sftp.close()

    def close(self):
        self.client.close()


# --------------------------- генерация конфигов ---------------------------

def render_config(meta: dict) -> str:
    users = meta.get("users", {})
    if users:
        auth_lines = ["auth:", "  type: userpass", "  userpass:"]
        for name, pw in users.items():
            auth_lines.append(f"    {name}: {pw}")
    else:
        auth_lines = ["auth:", "  type: password", "  password: " + gen_password()]
    auth_block = "\n".join(auth_lines)
    return f"""listen: :{meta['port']}

tls:
  cert: /etc/hysteria/certs/fullchain.pem
  key: /etc/hysteria/certs/privkey.pem

obfs:
  type: salamander
  salamander:
    password: {meta['obfs_password']}

{auth_block}

masquerade:
  type: proxy
  proxy:
    url: {meta.get('masquerade', MASQUERADE_URL)}
    rewriteHost: true

quic:
  initStreamReceiveWindow: 8388608
  maxStreamReceiveWindow: 8388608
  initConnReceiveWindow: 20971520
  maxConnReceiveWindow: 20971520
"""


def render_hook(domain: str) -> str:
    return f"""#!/bin/bash
set -e
SRC=/etc/letsencrypt/live/{domain}
DST=/etc/hysteria/certs
mkdir -p "$DST"
cp -L "$SRC/fullchain.pem" "$DST/fullchain.pem"
cp -L "$SRC/privkey.pem" "$DST/privkey.pem"
chown -R hysteria:hysteria "$DST"
chmod 644 "$DST/fullchain.pem"
chmod 600 "$DST/privkey.pem"
systemctl restart hysteria-server.service 2>/dev/null || true
"""


def build_link(meta: dict, name: str) -> str:
    domain, port = meta["domain"], meta["port"]
    obfs = meta["obfs_password"]
    users = meta.get("users", {})
    if name not in users:
        sys.exit(f"Пользователь '{name}' не найден")
    pw = users[name]
    userinfo = f"{quote(name)}:{quote(pw)}"
    q = f"obfs=salamander&obfs-password={quote(obfs)}&sni={quote(domain)}"
    frag = quote(f"{domain}-{name}")
    return f"hysteria2://{userinfo}@{domain}:{port}/?{q}#{frag}"


# --------------------------- meta на сервере ---------------------------

def load_meta(ssh: SSH) -> dict | None:
    raw = ssh.read(META_PATH)
    return json.loads(raw) if raw else None


def save_meta_and_config(ssh: SSH, meta: dict):
    ssh.put(json.dumps(meta, indent=2, ensure_ascii=False), META_PATH, mode=0o600)
    ssh.put(render_config(meta), CONFIG_PATH, mode=0o644)
    ssh.run("systemctl restart hysteria-server.service", check=True)


# --------------------------- bootstrap (apt/cert/hysteria/ufw) ---------------------------

BOOTSTRAP = r"""
set -e
export DEBIAN_FRONTEND=noninteractive
DOMAIN="__DOMAIN__"; EMAIL="__EMAIL__"

echo "[1/4] base packages"
apt-get update -y >/dev/null 2>&1 || apt-get update -y
apt-get install -y curl ca-certificates certbot >/dev/null

echo "[2/4] TLS certificate for $DOMAIN"
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then
  if ss -tlnH 'sport = :80' 2>/dev/null | grep -q LISTEN; then
    apt-get install -y python3-certbot-nginx >/dev/null 2>&1 || true
    certbot certonly --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" \
      || certbot certonly --standalone -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --http-01-port 80 || true
  else
    certbot certonly --standalone -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --http-01-port 80 || true
  fi
fi
if [ ! -d "/etc/letsencrypt/live/$DOMAIN" ]; then echo "CERT_FAILED"; exit 2; fi

echo "[3/4] hysteria2 server"
if ! command -v hysteria >/dev/null 2>&1; then
  bash <(curl -fsSL https://get.hy2.sh/) >/dev/null 2>&1 || bash <(curl -fsSL https://get.hy2.sh/)
fi

echo "[4/4] firewall (ufw)"
if command -v ufw >/dev/null 2>&1; then
  ufw allow 22/tcp >/dev/null 2>&1 || true
  ufw allow 443/udp >/dev/null 2>&1 || true
  ufw --force enable >/dev/null 2>&1 || true
fi
echo "BOOTSTRAP_OK"
"""


def cmd_install(args):
    creds = resolve_creds(args)
    if not creds["domain"]:
        sys.exit("Не задан домен (--domain или DNS в .env)")
    domain = creds["domain"]
    email = args.email or f"admin@{domain}"
    print(f"==> Установка Hysteria2 на {creds['host']} (домен {domain})")
    ssh = SSH(creds["host"], creds["port"], creds["user"], creds["password"])
    try:
        # 0. предупредить, если домен не указывает на сервер
        rc, out, _ = ssh.run(f"getent hosts {domain} | awk '{{print $1}}' | head -n1")
        resolved = out.strip()
        if resolved and resolved != creds["host"]:
            print(f"  ВНИМАНИЕ: {domain} резолвится в {resolved}, а сервер {creds['host']}. "
                  f"Сертификат может не выпуститься. Проверьте A-запись.")

        print("--> bootstrap (apt / certbot / hysteria / ufw)")
        script = BOOTSTRAP.replace("__DOMAIN__", domain).replace("__EMAIL__", email)
        rc, out, err = ssh.run(f"bash -s <<'__HY2_EOF__'\n{script}\n__HY2_EOF__", stream=True)
        if "BOOTSTRAP_OK" not in out:
            print(err)
            sys.exit("Bootstrap не завершился. Частая причина — домен не указывает на сервер "
                     "(сертификат не выпустился). Проверьте DNS и повторите.")

        print("--> сертификаты: deploy-hook + копия для пользователя hysteria")
        ssh.run("mkdir -p /etc/letsencrypt/renewal-hooks/deploy", check=True)
        ssh.put(render_hook(domain), HOOK_PATH, mode=0o755)
        ssh.run(f"bash {HOOK_PATH}", check=True)

        # meta: сохранить obfs/первого юзера, если уже есть — не перетирать
        meta = load_meta(ssh)
        if meta is None:
            meta = {
                "domain": domain,
                "port": PORT,
                "obfs_password": gen_password(),
                "masquerade": MASQUERADE_URL,
                "users": {"main": gen_password()},
            }
        else:
            meta["domain"] = domain  # на случай смены домена
        print("--> конфиг + запуск сервиса")
        ssh.run("systemctl enable hysteria-server.service >/dev/null 2>&1 || true")
        save_meta_and_config(ssh, meta)

        _verify(ssh)
        print("\n==> ГОТОВО. Ссылки для v2rayN:")
        for name in meta["users"]:
            print(f"  [{name}]\n  {build_link(meta, name)}")
    finally:
        ssh.close()


def _verify(ssh: SSH):
    rc, out, _ = ssh.run("systemctl is-active hysteria-server.service")
    print(f"--> сервис: {out.strip()}")
    rc, out, _ = ssh.run("ss -ulnp | grep ':443' || echo NO_UDP443")
    print(f"--> UDP 443: {'OK' if 'hysteria' in out else out.strip()}")


def cmd_adduser(args):
    creds = resolve_creds(args)
    ssh = SSH(creds["host"], creds["port"], creds["user"], creds["password"])
    try:
        meta = load_meta(ssh)
        if meta is None:
            sys.exit("Hysteria2 не установлена на этом сервере. Сначала: install")
        name = args.name
        if name in meta.get("users", {}):
            sys.exit(f"Пользователь '{name}' уже существует. Ссылка: \n{build_link(meta, name)}")
        meta.setdefault("users", {})[name] = gen_password()
        save_meta_and_config(ssh, meta)
        print(f"Создан пользователь '{name}'. Ссылка для v2rayN:\n{build_link(meta, name)}")
    finally:
        ssh.close()


def cmd_deluser(args):
    creds = resolve_creds(args)
    ssh = SSH(creds["host"], creds["port"], creds["user"], creds["password"])
    try:
        meta = load_meta(ssh)
        if meta is None:
            sys.exit("Hysteria2 не установлена.")
        if args.name not in meta.get("users", {}):
            sys.exit(f"Пользователя '{args.name}' нет.")
        del meta["users"][args.name]
        save_meta_and_config(ssh, meta)
        print(f"Пользователь '{args.name}' удалён.")
    finally:
        ssh.close()


def cmd_users(args):
    creds = resolve_creds(args)
    ssh = SSH(creds["host"], creds["port"], creds["user"], creds["password"])
    try:
        meta = load_meta(ssh)
        if meta is None:
            sys.exit("Hysteria2 не установлена.")
        users = meta.get("users", {})
        if not users:
            print("Пользователей нет.")
            return
        print(f"Пользователи на {meta['domain']} ({len(users)}):")
        for name in users:
            print(f"  [{name}]\n  {build_link(meta, name)}")
    finally:
        ssh.close()


def cmd_link(args):
    creds = resolve_creds(args)
    ssh = SSH(creds["host"], creds["port"], creds["user"], creds["password"])
    try:
        meta = load_meta(ssh)
        if meta is None:
            sys.exit("Hysteria2 не установлена.")
        print(build_link(meta, args.name))
    finally:
        ssh.close()


def cmd_status(args):
    creds = resolve_creds(args)
    ssh = SSH(creds["host"], creds["port"], creds["user"], creds["password"])
    try:
        _verify(ssh)
        rc, out, _ = ssh.run("journalctl -u hysteria-server -n 5 --no-pager 2>/dev/null | tail -n 5")
        print("--> последние логи:\n" + "\n".join("    " + l for l in out.splitlines()))
    finally:
        ssh.close()


# --------------------------- CLI ---------------------------

def main():
    p = argparse.ArgumentParser(description="Установка и управление Hysteria2-обходом")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--server", help="номер сервера из .env (1, 2, ...)")
        sp.add_argument("--host"); sp.add_argument("--port", type=int)
        sp.add_argument("--user"); sp.add_argument("--password"); sp.add_argument("--domain")

    sp = sub.add_parser("install", help="полная установка на сервер")
    add_common(sp); sp.add_argument("--email", help="email для Let's Encrypt")
    sp.set_defaults(func=cmd_install)

    sp = sub.add_parser("adduser", help="создать пользователя -> ссылка")
    add_common(sp); sp.add_argument("--name", required=True)
    sp.set_defaults(func=cmd_adduser)

    sp = sub.add_parser("deluser", help="удалить пользователя")
    add_common(sp); sp.add_argument("--name", required=True)
    sp.set_defaults(func=cmd_deluser)

    sp = sub.add_parser("users", help="список пользователей + ссылки")
    add_common(sp); sp.set_defaults(func=cmd_users)

    sp = sub.add_parser("link", help="ссылка для пользователя")
    add_common(sp); sp.add_argument("--name", required=True)
    sp.set_defaults(func=cmd_link)

    sp = sub.add_parser("status", help="состояние сервиса")
    add_common(sp); sp.set_defaults(func=cmd_status)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
