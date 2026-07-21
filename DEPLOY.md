# Deploying spotiosu

Local development is unchanged: `docker compose up -d` (Postgres only) + `.\run_web.ps1`.
Pushing to `master` runs the checks and ships the code to the server automatically.

```
push to master ──► GitHub Actions
                     ├── ci     : compile · JS syntax · config-from-env ·
                     │            imports · storage tests on a real Postgres
                     └── deploy : rsync code ──► pip install ──► systemctl restart
                                  ──► verify https://spotiosu.ru
```

> **Why no Docker in production?** The server is an **OpenVZ** container: the kernel
> is shared with the host, so `conntrack` and network namespaces are unavailable and
> the Docker daemon cannot run. Everything is therefore installed natively. If you
> ever move to a KVM VPS, a container-based setup becomes possible again.

---

## 1. DNS

| Type | Name  | Value        |
|------|-------|--------------|
| A    | `@`   | `<server IP>` |
| A    | `www` | `<server IP>` |

Must resolve **before** the first deploy — Caddy needs working DNS to issue the
Let's Encrypt certificate.

## 2. SSH access for deployments

On your machine:

```powershell
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\spotiosu_deploy -C "github-actions"
```

Install the public half **as root**. `ssh-copy-id` cannot be used: the `deploy`
account has no password, so nothing can authenticate as it yet.

```powershell
$pub = Get-Content "$env:USERPROFILE\.ssh\spotiosu_deploy.pub" -Raw
ssh root@<server IP> "id -u deploy >/dev/null 2>&1 || adduser --disabled-password --gecos '' deploy; install -d -m 700 -o deploy -g deploy /home/deploy/.ssh; echo '$pub' >> /home/deploy/.ssh/authorized_keys; chown deploy:deploy /home/deploy/.ssh/authorized_keys; chmod 600 /home/deploy/.ssh/authorized_keys"
```

Verify:

```powershell
ssh -i $env:USERPROFILE\.ssh\spotiosu_deploy deploy@<server IP> "whoami"
```

## 3. Server bootstrap (once)

Copy the `deploy/` folder over and run the setup script as root:

```powershell
scp -r deploy root@<server IP>:/root/
ssh root@<server IP> "bash /root/deploy/setup-server.sh"
```

It is idempotent, and installs:

- **Python 3.12** from the deadsnakes PPA — Ubuntu 22.04 ships 3.10, but
  `rosu-pp-py` (the pp calculator) requires ≥ 3.11.
- **PostgreSQL**, plus the `spotiosu` role and database. The generated password is
  printed once and written into `/opt/spotiosu/.env`.
- **Caddy** with the site config for automatic HTTPS.
- `/opt/spotiosu/{app,venv,.env}`, the `spotiosu` systemd service, and a narrow
  sudoers rule letting `deploy` restart just that one service.

Then fill in the one value it cannot know:

```bash
nano /opt/spotiosu/.env      # SPOTIOSU_CLIENT_SECRET=...
```

> **No `ufw`.** OpenVZ has no `conntrack`, so ufw cannot load its rules at all.
> Use your provider's firewall panel if you want one. Only SSH, 80 and 443 are
> listening; the app binds to `127.0.0.1` and Postgres to its local socket, so
> neither is reachable from the internet.

## 4. GitHub secrets

Repository → **Settings → Secrets and variables → Actions**:

| Secret     | Value |
|------------|-------|
| `SSH_HOST` | server IP |
| `SSH_USER` | `deploy` |
| `SSH_KEY`  | full contents of the **private** key `spotiosu_deploy`, including the `BEGIN`/`END` lines |
| `SSH_PORT` | only if SSH is not on port 22 |

## 5. osu! OAuth callback

Add the production callback at <https://osu.ppy.sh/home/account/edit>:

```
https://spotiosu.ru/auth/callback
```

Keep `http://localhost:8000/auth/callback` too, so local development keeps working.

## 6. First deploy

```bash
git push origin master
```

Follow it in the **Actions** tab. The final step polls
`https://spotiosu.ru/api/state` and fails the job if the site does not come up.

---

## Operations

**Logs**
```bash
sudo journalctl -u spotiosu -f          # application
sudo journalctl -u caddy -f             # TLS / proxy
systemctl status spotiosu
```

**Restart / roll back**
```bash
sudo systemctl restart spotiosu
git revert <bad commit> && git push     # rollback = ship the previous code
```

**Back up the database**
```bash
sudo -u postgres pg_dump spotiosu | gzip > ~/backup-$(date +%F).sql.gz
```

**Restore**
```bash
gunzip -c backup-2026-07-21.sql.gz | sudo -u postgres psql -d spotiosu
```

## Notes

- The app listens on `127.0.0.1:8000`; Caddy is the only public entry point.
- `SPOTIOSU_SESSION_SECRET` lives in `/opt/spotiosu/.env` and must stay stable —
  changing it logs every user out.
- The systemd unit deliberately omits `PrivateTmp`/`ProtectSystem`/`ProtectHome`:
  those need mount namespaces, which OpenVZ does not permit, and the unit would
  fail to start.
- Only `bot/`, `webapp/` and `requirements.txt` are synced. `.env` and the database
  are never touched by a deploy.
