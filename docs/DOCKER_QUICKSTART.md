# Docker Quick Start Guide

## Fresh Installation (Simplest Method)

### 1. Clone and Prepare

```bash
git clone https://github.com/othaime-en/secret-rotator.git
cd secret-rotator

# Create local directories for volumes
mkdir -p data logs

# Copy environment file
cp .env.example .env
```

### 2. Start the Container

```bash
docker-compose up -d
```

That's it! The container will:

- Create all required directories
- Generate default configuration
- Generate master encryption key
- Start the application

### 3. Access the Web Interface

Open your browser to: http://localhost:8080

### 4. **CRITICAL: Backup the Master Key**

```bash
# Copy master key to safe location
docker cp secret-rotator:/app/data/.master.key ./backup/

# Create encrypted backup
docker exec -it secret-rotator secret-rotator-backup create-encrypted

# Copy encrypted backup out
docker cp secret-rotator:/app/data/key_backups/ ./backup/
```

**Store the backup passphrase in a password manager!**

---

## Production Deployment with Custom Config

### 1. Prepare Custom Configuration

```bash
mkdir -p data logs config

# Copy and customize config
cp config/config.example.yaml config/config.yaml
# Edit config/config.yaml with your settings
```

### 2. Update docker-compose.yml

Uncomment the config volume mount:

```yaml
volumes:
  # Uncomment for custom config:
  - ./config:/app/config:ro # ← Uncomment this line
  - secret-rotator-data:/app/data
  - secret-rotator-logs:/app/logs
```

### 3. Deploy

```bash
docker-compose up -d
```

---

## Architecture Overview

```
Host Machine              Docker Container
./config/                 → /app/config/ (read-only, optional)
  config.yaml                 config.yaml (your custom config)
  config.example.yaml         config.example.yaml

./data/                   → /app/data/ (read-write, required)
  .master.key                 .master.key (auto-generated)
  secrets.json                secrets.json
  backup/                     backup/
  key_backups/                key_backups/

./logs/                   → /app/logs/ (read-write, required)
  rotation.log                rotation.log
```

**Key Principle**: Config is optional and read-only. Runtime data lives in writable data volume.

---

## Common Operations

### View Logs

```bash
# Real-time logs
docker-compose logs -f secret-rotator

# Inside container
docker exec secret-rotator tail -f /app/logs/rotation.log
```

### Manual Secret Rotation

```bash
docker exec secret-rotator secret-rotator --mode once
```

### Backup Operations

```bash
# Create encrypted backup
docker exec -it secret-rotator secret-rotator-backup create-encrypted

# List backups
docker exec secret-rotator secret-rotator-backup list

# Verify backup
docker exec secret-rotator secret-rotator-backup verify /app/data/key_backups/backup.enc
```

### Access Shell

```bash
docker exec -it secret-rotator bash
```

### Update Application

```bash
# Pull latest code
git pull

# Rebuild image
docker-compose build

# Restart with new image
docker-compose up -d
```

---

## Troubleshooting

### Container Won't Start

```bash
# Check logs
docker-compose logs secret-rotator

# Check if directories exist
ls -la data/ logs/

# Verify permissions
docker exec secret-rotator ls -la /app/data /app/logs
```

### "Permission Denied" Errors

```bash
# Ensure directories are writable
chmod -R 755 data/ logs/

# If using named user (UID 1000)
sudo chown -R 1000:1000 data/ logs/
```

### Master Key Not Found

```bash
# Check if key was generated
docker exec secret-rotator ls -la /app/data/.master.key

# Manually generate (if needed)
docker exec secret-rotator secret-rotator --mode verify
```

### Configuration Not Loading

```bash
# Check config location
docker exec secret-rotator echo $SECRET_ROTATOR_CONFIG

# Verify config file is valid
docker exec secret-rotator python -c "import yaml; yaml.safe_load(open('/app/data/config.yaml'))"
```

---

## Migration from v1.0.x

If you're upgrading from v1.0.x with existing deployments:

### Automatic Migration

The entrypoint script automatically migrates keys from old location:

```bash
docker-compose up -d
# Check logs for migration messages
docker-compose logs | grep -i migrate
```

### Manual Migration (if needed)

```bash
# Copy old key to new location
docker cp secret-rotator:/app/config/.master.key ./backup/old_master.key

# Restart container (will auto-migrate)
docker-compose restart secret-rotator
```

---

## Security Checklist

- [ ] Master key backed up to external location
- [ ] Encrypted backup created with strong passphrase
- [ ] Passphrase stored in password manager
- [ ] Config directory mounted read-only (production)
- [ ] Data directory has restrictive permissions
- [ ] Web interface accessible only from trusted networks
- [ ] Container running as non-root user
- [ ] Regular backup verification scheduled

---

## Advanced Configuration

### Using External Secrets

```yaml
# docker-compose.yml
services:
  secret-rotator:
    environment:
      - DB_PASSWORD=${DB_PASSWORD} # From external source
    secrets:
      - db_password

secrets:
  db_password:
    external: true
```

### Custom Network

```yaml
networks:
  secret-rotator-network:
    driver: bridge
    ipam:
      config:
        - subnet: 172.28.0.0/16
```

### Resource Limits

```yaml
deploy:
  resources:
    limits:
      cpus: "2.0"
      memory: 1G
    reservations:
      cpus: "1.0"
      memory: 512M
```

---

## Support

- **Documentation**: [README.md](README.md)
- **Issues**: https://github.com/othaime-en/secret-rotator/issues
- **Backup Guide**: [docs/BACKUP_ARCHITECTURE.md](docs/BACKUP_ARCHITECTURE.md)
