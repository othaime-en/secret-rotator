# Master Key Backup Architecture

## Overview

The Secret Rotator uses a **security-focused architecture** that separates immutable configuration from dynamic backup operations. This document explains the design decisions, security principles, and operational procedures.

---

## Architecture Principles

### 1. **Principle of Least Privilege**

- **Config directory** (`/app/config`): Read-only at runtime

  - Contains: `config.yaml`, `.master.key`
  - Cannot be modified by the running application
  - Prevents accidental or malicious modification

- **Data directory** (`/app/data`): Writable at runtime
  - Contains: secrets, backups, key backups
  - Allows normal operation of backup systems
  - Isolated from immutable configuration

### 2. **Separation of Concerns**

```
/app/
├── config/              # IMMUTABLE (read-only)
│   ├── config.yaml      # Application configuration
│   └── .master.key      # Master encryption key
│
├── data/                # MUTABLE (read-write)
│   ├── secrets.json     # Encrypted secrets storage
│   ├── backup/          # Secret rotation backups
│   └── key_backups/     # Master key backups (NEW LOCATION)
│
└── logs/                # MUTABLE (read-write)
    └── *.log
```

### 3. **Defense in Depth**

- Master key file: Read-only, cannot be modified
- Key backups: Separate location, can be encrypted
- External storage: Recommended for production deployments

---

## Backup Types

### 1. Encrypted Backup (Recommended)

**Use Case**: Most common, suitable for cloud storage

**Security Features**:

- PBKDF2 key derivation (600,000 iterations)
- Passphrase-protected encryption
- Safe to store in cloud (S3, Azure, Google Drive)

**Workflow**:

```bash
# Create encrypted backup
docker-compose exec secret-rotator secret-rotator-backup create-encrypted

# Backup is created at: /app/data/key_backups/master_key_backup_*.enc

# Copy to external storage (CRITICAL)
docker cp secret-rotator:/app/data/key_backups/backup.enc ./external-backup/

# Upload to cloud
aws s3 cp backup.enc s3://my-bucket/backups/
```

**Storage Recommendations**:

- ✅ AWS S3 with server-side encryption
- ✅ Azure Blob Storage with encryption
- ✅ Google Cloud Storage with encryption
- ✅ Password manager (1Password, LastPass)
- ✅ Encrypted USB drive
- ❌ Unencrypted network drives
- ❌ Email attachments

---

### 2. Split Key Backup (Shamir's Secret Sharing)

**Use Case**: Organizations with distributed trust model

**Security Features**:

- No single person/location has complete key
- Threshold scheme (e.g., 3 of 5 shares needed)
- Geographic distribution supported

**Workflow**:

```bash
# Create 5 shares (need any 3 to restore)
docker-compose exec secret-rotator secret-rotator-backup create-split \
  --shares 5 --threshold 3

# Shares created at: /app/data/key_backups/master_key_share_*_of_5_*.share

# Copy shares out of container
for i in {1..5}; do
  docker cp secret-rotator:/app/data/key_backups/master_key_share_${i}_*.share ./share${i}/
done
```

**Distribution Strategy Example**:

```
Share 1 → Company safe (HQ, New York)
Share 2 → Backup facility (Chicago)
Share 3 → CEO's personal safe (California)
Share 4 → CTO's personal safe (Texas)
Share 5 → AWS S3 (encrypted, us-west-2)
```

**Key Properties**:

- Any 3 shares = Full key recovery
- 2 shares or less = No information leaked
- Loss of 2 shares = Still recoverable with remaining 3

---

### 3. Plaintext Backup (Not Recommended)

**Use Case**: Immediate physical storage only

**Security Features**:

- None (unencrypted)
- Should NEVER be stored digitally
- Physical safe/vault only

**Workflow**:

```bash
# Create plaintext backup
docker-compose exec secret-rotator secret-rotator-backup create-plaintext

# IMMEDIATELY move to physical safe
# DO NOT store in cloud, on disk, in email, etc.
```

---

## Docker Deployment Guide

### Volume Configuration

**docker-compose.yml**:

```yaml
services:
  secret-rotator:
    volumes:
      # Config: READ-ONLY (immutable at runtime)
      - ./config:/app/config:ro

      # Data: READ-WRITE (backups, secrets)
      - secret-rotator-data:/app/data

      # Logs: READ-WRITE
      - secret-rotator-logs:/app/logs

volumes:
  secret-rotator-data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: ${DATA_DIR:-./data}
```

### Backup Operations in Docker

#### Creating Backups

```bash
# Create encrypted backup
docker-compose exec secret-rotator secret-rotator-backup create-encrypted

# Create split key backup
docker-compose exec secret-rotator secret-rotator-backup create-split \
  --shares 5 --threshold 3

# List available backups
docker-compose exec secret-rotator secret-rotator-backup list
```

#### Extracting Backups to External Storage

```bash
# Copy encrypted backup out of container
docker cp secret-rotator:/app/data/key_backups/backup_20250115.enc ./backup/

# For all backups
docker cp secret-rotator:/app/data/key_backups/ ./backup/

# Upload to S3
aws s3 sync ./backup/ s3://my-key-backups/secret-rotator/
```

#### Restoring Backups

```bash
# Copy backup into container (if needed)
docker cp ./backup/backup.enc secret-rotator:/app/data/key_backups/

# Restore from backup
docker-compose exec secret-rotator secret-rotator-backup restore \
  /app/data/key_backups/backup.enc

# Restart application
docker-compose restart secret-rotator
```

---

## Production Best Practices

### 1. **Automated External Backup**

#### Option A: Sidecar Container

```yaml
# docker-compose.yml
services:
  secret-rotator:
    # ... main service ...

  backup-sync:
    image: amazon/aws-cli
    volumes:
      - secret-rotator-data:/data:ro
    environment:
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
    command: >
      bash -c "
        while true; do
          aws s3 sync /data/key_backups/ s3://my-backups/secret-rotator/ --sse
          sleep 3600
        done
      "
```

#### Option B: Cron Job

```bash
#!/bin/bash
# /etc/cron.d/secret-rotator-backup

# Run hourly: Sync backups to S3
0 * * * * root docker cp secret-rotator:/app/data/key_backups /tmp/backups && \
  aws s3 sync /tmp/backups s3://my-key-backups/ --sse && \
  rm -rf /tmp/backups
```

### 2. **Backup Verification Schedule**

```bash
# Weekly verification (Sunday 2 AM)
0 2 * * 0 root docker-compose exec -T secret-rotator \
  secret-rotator-backup verify /app/data/key_backups/latest.enc
```

### 3. **Monitoring and Alerts**

Monitor these metrics:

- ✓ Backup creation timestamp (should be < 24 hours old)
- ✓ External storage sync status
- ✓ Backup verification results
- ✓ Number of available backups

Alert on:

- ✗ No backup created in 48 hours
- ✗ External sync failure
- ✗ Backup verification failure
- ✗ Split-key shares below threshold

---

## Security Checklist

### Initial Setup

- [ ] Create encrypted backup immediately after key generation
- [ ] Store passphrase in password manager
- [ ] Copy encrypted backup to external storage
- [ ] Test restoration in non-production environment
- [ ] Document backup locations securely

### Ongoing Operations

- [ ] Weekly: Verify backup integrity
- [ ] Monthly: Test restoration procedure
- [ ] Quarterly: Rotate master encryption key
- [ ] Annually: Review and update backup strategy

### Production Deployment

- [ ] Config directory mounted read-only
- [ ] Data directory on persistent volume
- [ ] Automated backup to external storage
- [ ] Monitoring and alerting configured
- [ ] Disaster recovery plan documented
- [ ] Key custodians identified and trained

---

## Disaster Recovery Scenarios

### Scenario 1: Container Deletion

**Impact**: Low (if backups exist externally)

**Recovery**:

```bash
# 1. Recreate container
docker-compose up -d

# 2. Copy backup into new container
docker cp ./backup/backup.enc secret-rotator:/app/data/key_backups/

# 3. Restore master key
docker-compose exec secret-rotator secret-rotator-backup restore \
  /app/data/key_backups/backup.enc

# 4. Verify application
docker-compose exec secret-rotator secret-rotator --mode verify
```

### Scenario 2: Complete Server Loss

**Impact**: Medium (requires external backup)

**Recovery**:

```bash
# 1. Provision new server
# 2. Install Docker and clone repository
git clone https://github.com/your-org/secret-rotator-deployment.git

# 3. Download backup from external storage
aws s3 cp s3://my-backups/secret-rotator/backup.enc ./backup/

# 4. Start services
docker-compose up -d

# 5. Restore from backup
docker cp ./backup/backup.enc secret-rotator:/app/data/key_backups/
docker-compose exec secret-rotator secret-rotator-backup restore \
  /app/data/key_backups/backup.enc

# 6. Restart and verify
docker-compose restart secret-rotator
```

### Scenario 3: Master Key Compromised

**Impact**: Critical (requires key rotation)

**Recovery**:

```bash
# 1. Generate new master key
docker-compose exec secret-rotator secret-rotator --mode rotate-master-key

# 2. Create new backups
docker-compose exec secret-rotator secret-rotator-backup create-encrypted

# 3. Copy to external storage
docker cp secret-rotator:/app/data/key_backups/new_backup.enc ./backup/
aws s3 cp ./backup/new_backup.enc s3://my-backups/secret-rotator/

# 4. Securely delete old backups
# ... (follow your organization's secure deletion procedures)
```

---

## Future Enhancements

### Planned Features (v2.0)

1. **Automatic External Storage Integration**

   ```yaml
   # config.yaml
   backup:
     key_backups:
       local_path: "data/key_backups"
       external_storage:
         enabled: true
         type: "s3"
         bucket: "my-key-backups"
         region: "us-east-1"
         encryption: "AES256"
         sync_interval: "1h"
   ```

2. **Multi-Region Replication**

   ```yaml
   backup:
     external_storage:
       primary:
         type: "s3"
         bucket: "backups-us-east-1"
         region: "us-east-1"
       replicas:
         - type: "s3"
           bucket: "backups-us-west-2"
           region: "us-west-2"
         - type: "azure_blob"
           container: "backups"
           region: "westus"
   ```

3. **Backup Encryption with Hardware Security Modules (HSM)**

   - AWS CloudHSM integration
   - Azure Key Vault integration
   - On-premises HSM support

4. **Compliance and Audit**
   - Backup access logging
   - Compliance report generation (SOC 2, ISO 27001)
   - Automated compliance checks

---

## FAQ

**Q: Why store backups separately from config?**
A: Security isolation. Read-only config prevents runtime modification, while backups need write access.

**Q: Can I use the old `config/key_backups` location?**
A: Yes, but not recommended. You'll need to make the entire config directory writable, reducing security.

**Q: How often should I create backups?**
A: After any key rotation or configuration change. Automated daily backups recommended for production.

**Q: Where should I store encrypted backups?**
A: Multiple locations: Local safe + Cloud storage (S3/Azure) + Password manager. Geographic distribution recommended.

**Q: What happens if I lose my passphrase?**
A: The encrypted backup is unrecoverable. This is why split-key backups or multiple backup strategies are recommended.

**Q: Should I backup the entire `/app/data` directory?**
A: Yes, but separately. `/app/data/key_backups` is specifically for master key. `/app/data/backup` contains secret rotation backups.

---

## Contact and Support

- **Documentation**: https://github.com/othaime-en/secret-rotator
- **Issues**: https://github.com/othaime-en/secret-rotator/issues

---

**Last Updated**: January 2025  
**Version**: 1.1.0
