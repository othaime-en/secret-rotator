# Master Key Backup - Quick Reference

## 🚨 Emergency Commands

### Lost Master Key - Recovery

```bash
# 1. Download backup from external storage
aws s3 cp s3://my-backups/backup.enc ./

# 2. Restore in Docker
docker cp ./backup.enc secret-rotator:/app/data/key_backups/
docker-compose exec secret-rotator secret-rotator-backup restore /app/data/key_backups/backup.enc
docker-compose restart secret-rotator

# 3. Verify recovery
docker-compose exec secret-rotator secret-rotator --mode verify
```

### Key Compromised - Immediate Actions

```bash
# 1. Rotate master key immediately
docker-compose exec secret-rotator secret-rotator --mode rotate-master-key

# 2. Create new backup
docker-compose exec secret-rotator secret-rotator-backup create-encrypted

# 3. Extract and store externally
docker cp secret-rotator:/app/data/key_backups/new_backup.enc ./
aws s3 cp ./new_backup.enc s3://my-backups/secret-rotator/

# 4. Revoke access to old backups
```

---

## 📋 Common Operations

### Initial Setup (First Time)

```bash
# 1. Create encrypted backup
docker-compose exec secret-rotator secret-rotator-backup create-encrypted
# Enter strong passphrase (20+ chars)
# Store passphrase in 1Password/LastPass

# 2. Extract backup from container
docker cp secret-rotator:/app/data/key_backups/backup_*.enc ./backup/

# 3. Upload to cloud
aws s3 cp ./backup/backup_*.enc s3://my-backups/secret-rotator/
```

### Weekly Verification

```bash
# Verify encrypted backup
docker-compose exec secret-rotator secret-rotator-backup verify \
  /app/data/key_backups/backup_latest.enc

# Check backup health
docker-compose exec secret-rotator secret-rotator-backup list
```

### Monthly Test Restoration

```bash
# In TEST environment only
docker-compose exec secret-rotator secret-rotator-backup restore \
  /app/data/key_backups/backup_test.enc
docker-compose restart secret-rotator
docker-compose exec secret-rotator secret-rotator --mode verify
```

---

## 📊 Backup Types Comparison

| Type          | Security   | Complexity | Use Case                           |
| ------------- | ---------- | ---------- | ---------------------------------- |
| **Encrypted** | ⭐⭐⭐⭐⭐ | ⭐⭐       | Production (single admin)          |
| **Split Key** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐   | Organizations (distributed trust)  |
| **Plaintext** | ⭐         | ⭐         | Physical safe only (NOT for cloud) |

---

## 🔐 Security Checklist

### Daily

- [ ] Verify application is running
- [ ] Check logs for errors

### Weekly

- [ ] Verify backup integrity
- [ ] Check external storage sync
- [ ] Review access logs

### Monthly

- [ ] Test backup restoration (in test environment)
- [ ] Verify all backup copies accessible
- [ ] Review backup custodian access

### Quarterly

- [ ] Rotate master encryption key
- [ ] Create new backups after rotation
- [ ] Update external storage with new backups
- [ ] Review disaster recovery procedures

### Annually

- [ ] Full disaster recovery drill
- [ ] Review and update backup strategy
- [ ] Update backup custodian documentation
- [ ] Audit backup access controls

---

## 🗂️ File Locations

### Docker Deployment

```
Container:
  /app/config/.master.key          # Master key (read-only)
  /app/data/key_backups/           # Key backups (writable)
  /app/data/backup/                # Secret rotation backups

Host (mounted volumes):
  ./config/.master.key             # Master key
  ./data/key_backups/              # Key backups
  ./data/backup/                   # Secret rotation backups
```

### Local Installation

```
~/.config/secret-rotator/
  .master.key                      # Master key
  config.yaml                      # Configuration

~/.local/share/secret-rotator/
  secrets.json                     # Encrypted secrets
  key_backups/                     # Master key backups
  backup/                          # Secret rotation backups

~/.local/state/secret-rotator/logs/
  rotation.log                     # Application logs
```

---

## 🚀 Command Cheatsheet

### Backup Commands

```bash
# Create backups
secret-rotator-backup create-encrypted                    # Encrypted (recommended)
secret-rotator-backup create-split --shares 5 --threshold 3  # Split key
secret-rotator-backup create-plaintext                    # Plaintext (not recommended)

# List and verify
secret-rotator-backup list                                # List all backups
secret-rotator-backup verify <backup-file>                # Verify integrity

# Restore
secret-rotator-backup restore <backup-file>               # From encrypted
secret-rotator-backup restore-split share1 share2 share3  # From split key

# Documentation
secret-rotator-backup export-instructions                 # Generate instructions
```

### Docker Commands

```bash
# Execute in container
docker-compose exec secret-rotator <command>

# Copy files in/out
docker cp secret-rotator:<container-path> <host-path>    # Copy out
docker cp <host-path> secret-rotator:<container-path>    # Copy in

# Restart
docker-compose restart secret-rotator
docker-compose logs -f secret-rotator                     # View logs
```

### Application Commands

```bash
# Operations
secret-rotator                                            # Start daemon
secret-rotator --mode once                                # One-time rotation
secret-rotator --mode verify                              # Verify encryption
secret-rotator --mode status                              # Show status

# Backup operations
secret-rotator --mode verify-backups                      # Verify all backups
secret-rotator --mode rotate-master-key                   # Rotate key
secret-rotator --mode cleanup-backups                     # Remove old backups
```

---

## 📍 External Storage Commands

### AWS S3

```bash
# Upload
aws s3 cp ./backup/backup.enc s3://my-backups/secret-rotator/
aws s3 sync ./backup/ s3://my-backups/secret-rotator/

# Download
aws s3 cp s3://my-backups/secret-rotator/backup.enc ./backup/
aws s3 sync s3://my-backups/secret-rotator/ ./backup/

# List
aws s3 ls s3://my-backups/secret-rotator/
```

### Azure Blob Storage

```bash
# Upload
az storage blob upload --account-name mystorageaccount \
  --container-name backups --name backup.enc --file ./backup/backup.enc

# Download
az storage blob download --account-name mystorageaccount \
  --container-name backups --name backup.enc --file ./backup/backup.enc

# List
az storage blob list --account-name mystorageaccount --container-name backups
```

### Google Cloud Storage

```bash
# Upload
gsutil cp ./backup/backup.enc gs://my-backups/secret-rotator/

# Download
gsutil cp gs://my-backups/secret-rotator/backup.enc ./backup/

# List
gsutil ls gs://my-backups/secret-rotator/
```

---

## ⚠️ Common Errors

### `OSError: [Errno 30] Read-only file system`

**Problem**: Trying to create backups in read-only config directory  
**Solution**: Upgrade to v1.1.0+ (uses `/app/data/key_backups/`)

### `FileNotFoundError: Master key not found`

**Problem**: Master key missing from config directory  
**Solution**: Generate new key or restore from backup

### `Backup checksum verification failed`

**Problem**: Backup file corrupted  
**Solution**: Use different backup copy, verify integrity before restoration

### `Passphrase incorrect`

**Problem**: Wrong passphrase for encrypted backup  
**Solution**: Retrieve correct passphrase from password manager

---

## 📞 Emergency Contacts

| Role            | Name     | Contact         | Backup Access               |
| --------------- | -------- | --------------- | --------------------------- |
| Primary Admin   | `[NAME]` | `[EMAIL/PHONE]` | Encrypted backup passphrase |
| Secondary Admin | `[NAME]` | `[EMAIL/PHONE]` | Split key shares 1-2        |
| CEO             | `[NAME]` | `[EMAIL/PHONE]` | Split key share 3           |
| CTO             | `[NAME]` | `[EMAIL/PHONE]` | Split key share 4           |
| Cloud Admin     | `[NAME]` | `[EMAIL/PHONE]` | S3 bucket access            |

---

## 🔗 Resources

- **Full Documentation**: [BACKUP_ARCHITECTURE.md](BACKUP_ARCHITECTURE.md)
- **Main README**: [README.md](README.md)
- **GitHub Issues**: https://github.com/othaime-en/secret-rotator/issues
- **Security Issues**: security@your-org.com (private reporting)

---

**Last Updated**: January 2025  
**Version**: 1.1.0  
**Document Owner**: [YOUR NAME/TEAM]
