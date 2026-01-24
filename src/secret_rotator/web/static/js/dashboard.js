/**
 * Secret Rotation Dashboard - Main JavaScript
 * Extracted and enhanced from web_interface.py
 */

// Global instances
let api;
let tabs;

/**
 * Initialize dashboard on page load
 */
document.addEventListener('DOMContentLoaded', () => {
    console.log('Dashboard initializing...');

    api = new APIClient();
    tabs = new TabManager('.tab-container');

    loadJobs();

    console.log('Dashboard initialized');
});

/**
 * Load and display rotation jobs
 */
async function loadJobs() {
    const jobsDiv = document.getElementById('jobs');

    if (!jobsDiv) {
        console.error('Jobs container not found');
        return;
    }

    jobsDiv.innerHTML = '<p class="loading">Loading jobs</p>';

    try {
        const data = await api.fetchJobs();

        if (data.jobs && data.jobs.length > 0) {
            jobsDiv.innerHTML = data.jobs.map(job => `
                <div class="job">
                    <strong>${escapeHtml(job.name)}</strong><br>
                    Provider: ${escapeHtml(job.provider)} | Rotator: ${escapeHtml(job.rotator)}<br>
                    Secret ID: <code>${escapeHtml(job.secret_id)}</code>
                </div>
            `).join('');
        } else {
            jobsDiv.innerHTML = '<div class="status info">No rotation jobs configured.</div>';
        }
    } catch (error) {
        console.error('Error loading jobs:', error);
        jobsDiv.innerHTML = `<div class="status error">Failed to load jobs: ${escapeHtml(error.message)}</div>`;
    }
}

/**
 * Rotate all secrets with confirmation
 */
async function rotateAll() {
    if (!confirm('Are you sure you want to rotate all secrets? This action cannot be undone.')) {
        return;
    }

    const statusDiv = document.getElementById('status');

    statusDiv.innerHTML = '<div class="status info">Rotation in progress...</div>';

    try {
        const data = await api.rotateAll();
        const results = data.results || {};
        const successful = Object.values(results).filter(r => r).length;
        const total = Object.keys(results).length;

        const statusClass = successful === total ? 'success' : 'error';

        statusDiv.innerHTML = `
            <div class="status ${statusClass}">
                Rotation complete: ${successful}/${total} successful
            </div>
        `;

        // Show detailed results in logs
        const logs = Object.entries(results)
            .map(([job, success]) => `[${new Date().toLocaleTimeString()}] ${job}: ${success ? 'SUCCESS' : 'FAILED'}`)
            .join('\n');
        addLog(logs);

        // Reload jobs after rotation
        setTimeout(loadJobs, 1000);

    } catch (error) {
        console.error('Rotation error:', error);
        statusDiv.innerHTML = `<div class="status error">Error during rotation: ${escapeHtml(error.message)}</div>`;
    }
}

/**
 * Load and display backups
 */
async function loadBackups() {
    const backupsDiv = document.getElementById('backups');
    const secretFilter = document.getElementById('secretFilter');
    const secretId = secretFilter ? secretFilter.value.trim() : null;

    if (!backupsDiv) {
        console.error('Backups container not found');
        return;
    }

    backupsDiv.innerHTML = '<p class="loading">Loading backups</p>';

    try {
        const data = await api.fetchBackups(secretId);

        if (data.backups && data.backups.length > 0) {
            backupsDiv.innerHTML = data.backups.map(backup => {
                const encodedPath = encodeURIComponent(backup.backup_file);
                const created = new Date(backup.backup_created).toLocaleString();
                const fileName = backup.backup_file.split('/').pop();

                return `
                    <div class="backup">
                        <div class="backup-item">
                            <div class="backup-info">
                                <strong>${escapeHtml(backup.secret_id)}</strong><br>
                                <small>Created: ${created}</small><br>
                                <small>File: ${escapeHtml(fileName)}</small>
                            </div>
                            <div class="backup-actions">
                                <button class="success" onclick="viewBackup('${encodedPath}')">View</button>
                                <button class="danger" onclick="confirmRestore('${escapeHtml(backup.backup_file)}', '${escapeHtml(backup.secret_id)}')">Restore</button>
                            </div>
                        </div>
                    </div>
                `;
            }).join('');
        } else {
            backupsDiv.innerHTML = '<div class="status info">No backups found.</div>';
        }
    } catch (error) {
        console.error('Error loading backups:', error);
        backupsDiv.innerHTML = `<div class="status error">Error loading backups: ${escapeHtml(error.message)}</div>`;
    }
}

/**
 * View backup details
 */
async function viewBackup(encodedBackupFile) {
    try {
        const data = await api.fetchBackupDetail(decodeURIComponent(encodedBackupFile));

        const details = `
Secret ID: ${data.secret_id}
Timestamp: ${new Date(data.backup_created).toLocaleString()}
Old Value: ${data.old_value || '(masked)'}
New Value: ${data.new_value || '(masked)'}
Encrypted: ${data.encrypted ? 'Yes' : 'No'}
        `.trim();

        alert('Backup Details:\n\n' + details);
    } catch (error) {
        console.error('Error viewing backup:', error);
        alert('Error viewing backup details: ' + error.message);
    }
}

/**
 * Confirm and restore backup
 */
function confirmRestore(backupFile, secretId) {
    if (confirm(`Are you sure you want to restore the backup for "${secretId}"?\n\nThis will replace the current secret value with the old value from the backup.`)) {
        restoreBackup(backupFile);
    }
}

/**
 * Restore backup
 */
async function restoreBackup(backupFile) {
    const statusDiv = document.getElementById('status');

    statusDiv.innerHTML = '<div class="status info">Restoring backup...</div>';

    try {
        const data = await api.restoreBackup(backupFile);

        if (data.success) {
            statusDiv.innerHTML = `<div class="status success">Successfully restored backup for ${escapeHtml(data.secret_id)}</div>`;
            addLog(`Restored backup for ${data.secret_id}`);

            // Reload backups
            setTimeout(loadBackups, 1000);
        } else {
            statusDiv.innerHTML = `<div class="status error">Failed to restore backup: ${escapeHtml(data.error)}</div>`;
        }
    } catch (error) {
        console.error('Error during restoration:', error);
        statusDiv.innerHTML = `<div class="status error">Error during restoration: ${escapeHtml(error.message)}</div>`;
    }
}

/**
 * Load backup health metrics
 */
async function loadBackupHealth() {
    const healthDiv = document.getElementById('health-status');

    if (!healthDiv) {
        console.error('Health status container not found');
        return;
    }

    healthDiv.innerHTML = '<p class="loading">Loading health metrics</p>';

    try {
        const data = await api.fetchBackupHealth();

        let statusClass = 'info';
        if (data.status === 'healthy') statusClass = 'success';
        if (data.status === 'warning' || data.status === 'critical') statusClass = 'error';

        healthDiv.innerHTML = `
            <div class="status ${statusClass}">
                <h3>Status: ${escapeHtml(data.status || 'Unknown').toUpperCase()}</h3>
                <div style="margin-top: 10px;">
                    <strong>Success Rate:</strong> ${data.success_rate || 0}%<br>
                    <strong>Total Backups:</strong> ${data.total_backups || 0}<br>
                    <strong>Verified:</strong> ${data.verified || 0}<br>
                    <strong>Failed:</strong> ${data.failed || 0}<br>
                    ${data.last_verification ? `<strong>Last Verification:</strong> ${new Date(data.last_verification).toLocaleString()}<br>` : ''}
                </div>
            </div>
        `;
    } catch (error) {
        console.error('Error loading backup health:', error);
        healthDiv.innerHTML = `<div class="status error">Error loading backup health: ${escapeHtml(error.message)}</div>`;
    }
}

/**
 * Run verification now
 */
async function runVerificationNow() {
    if (!confirm('Run backup verification now? This may take a few minutes.')) {
        return;
    }

    const healthDiv = document.getElementById('health-status');
    healthDiv.innerHTML = '<div class="status info">Running verification...</div>';

    try {
        const data = await api.runVerification();

        if (data.success) {
            const report = data.report;
            healthDiv.innerHTML = `
                <div class="status success">
                    <h3>Verification Complete</h3>
                    <div style="margin-top: 10px;">
                        <strong>Total Backups:</strong> ${report.total_backups}<br>
                        <strong>Verified:</strong> ${report.verified}<br>
                        <strong>Failed:</strong> ${report.failed}<br>
                        ${report.failed > 0 ? '<br><strong style="color: red;">⚠️ Some backups failed verification!</strong>' : ''}
                    </div>
                </div>
            `;

            // Reload health metrics
            setTimeout(loadBackupHealth, 2000);
        } else {
            healthDiv.innerHTML = '<div class="status error">Verification failed</div>';
        }
    } catch (error) {
        console.error('Error running verification:', error);
        healthDiv.innerHTML = `<div class="status error">Error running verification: ${escapeHtml(error.message)}</div>`;
    }
}

/**
 * Load verification history
 */
async function loadVerificationHistory() {
    const historyDiv = document.getElementById('verification-history');

    if (!historyDiv) {
        console.error('Verification history container not found');
        return;
    }

    historyDiv.innerHTML = '<p class="loading">Loading verification history</p>';

    try {
        const data = await api.fetchVerificationHistory(7);

        if (data.history && data.history.length > 0) {
            let html = '<table><thead><tr><th>Date</th><th>Total</th><th>Verified</th><th>Failed</th><th>Success Rate</th></tr></thead><tbody>';

            data.history.forEach(report => {
                const successRate = ((report.verified / report.total_backups) * 100).toFixed(1);
                const statusColor = successRate >= 95 ? '#28a745' : '#dc3545';
                const timestamp = new Date(report.timestamp).toLocaleString();

                html += `
                    <tr>
                        <td>${timestamp}</td>
                        <td>${report.total_backups}</td>
                        <td>${report.verified}</td>
                        <td>${report.failed}</td>
                        <td style="color: ${statusColor}; font-weight: bold;">${successRate}%</td>
                    </tr>
                `;
            });

            html += '</tbody></table>';
            historyDiv.innerHTML = html;
        } else {
            historyDiv.innerHTML = '<div class="status info">No verification history available</div>';
        }
    } catch (error) {
        console.error('Error loading verification history:', error);
        historyDiv.innerHTML = `<div class="status error">Error loading history: ${escapeHtml(error.message)}</div>`;
    }
}

/**
 * Add log entry
 */
function addLog(message) {
    const logsDiv = document.getElementById('logs');

    if (!logsDiv) {
        console.error('Logs container not found');
        return;
    }

    const timestamp = new Date().toLocaleTimeString();
    logsDiv.innerHTML = `[${timestamp}] ${escapeHtml(message)}\n` + logsDiv.innerHTML;
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    if (typeof text !== 'string') {
        return text;
    }

    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };

    return text.replace(/[&<>"']/g, m => map[m]);
}

window.loadJobs = loadJobs;
window.rotateAll = rotateAll;
window.loadBackups = loadBackups;
window.viewBackup = viewBackup;
window.confirmRestore = confirmRestore;
window.restoreBackup = restoreBackup;
window.loadBackupHealth = loadBackupHealth;
window.runVerificationNow = runVerificationNow;
window.loadVerificationHistory = loadVerificationHistory;
window.addLog = addLog;