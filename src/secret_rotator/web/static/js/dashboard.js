/**
 * Main Dashboard JavaScript
 * 
 * Coordinates API calls, tab management, and UI updates
 */

// Global instances
let api;
let tabs;

// Initialize on page load
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
    const jobsList = document.getElementById('jobs-list');
    const statusBox = document.getElementById('status');

    jobsList.innerHTML = '<p class="loading">Loading jobs</p>';

    try {
        const [statusData, jobsData] = await Promise.all([
            api.fetchStatus(),
            api.fetchJobs()
        ]);

        // Update status
        statusBox.innerHTML = `
            <h2>System Status</h2>
            <p><strong>Status:</strong> ${statusData.status}</p>
            <p><strong>Providers:</strong> ${statusData.providers}</p>
            <p><strong>Rotators:</strong> ${statusData.rotators}</p>
            <p><strong>Jobs:</strong> ${statusData.jobs}</p>
        `;

        // Update stats cards
        document.getElementById('total-jobs').textContent = statusData.jobs;
        document.getElementById('total-providers').textContent = statusData.providers;
        document.getElementById('total-rotators').textContent = statusData.rotators;

        // Display jobs
        if (jobsData.jobs && jobsData.jobs.length > 0) {
            jobsList.innerHTML = `
                <table style="width: 100%; border-collapse: collapse;">
                    <thead>
                        <tr style="border-bottom: 2px solid #e1e8ed;">
                            <th style="text-align: left; padding: 10px;">Secret ID</th>
                            <th style="text-align: left; padding: 10px;">Provider</th>
                            <th style="text-align: left; padding: 10px;">Rotator</th>
                            <th style="text-align: left; padding: 10px;">Schedule</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${jobsData.jobs.map(job => `
                            <tr style="border-bottom: 1px solid #e1e8ed;">
                                <td style="padding: 10px;">${job.secret_id || 'N/A'}</td>
                                <td style="padding: 10px;">${job.provider || 'N/A'}</td>
                                <td style="padding: 10px;">${job.rotator || 'N/A'}</td>
                                <td style="padding: 10px;">${job.schedule || 'N/A'}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
        } else {
            jobsList.innerHTML = '<p>No rotation jobs configured.</p>';
        }

    } catch (error) {
        console.error('Failed to load jobs:', error);
        jobsList.innerHTML = `<div class="status error">Failed to load jobs: ${error.message}</div>`;
    }
}

/**
 * Trigger rotation of all secrets
 */
async function rotateAll() {
    const statusBox = document.getElementById('status');
    const button = document.getElementById('rotate-all-btn');

    button.disabled = true;
    button.textContent = 'Rotating...';
    statusBox.innerHTML = '<div class="status info">Rotation in progress...</div>';

    try {
        const data = await api.rotateAll();

        const results = data.results || {};
        const total = Object.keys(results).length;
        const successful = Object.values(results).filter(r => r).length;

        statusBox.innerHTML = `
            <div class="status success">
                <strong>Rotation Complete:</strong> ${successful}/${total} secrets rotated successfully
            </div>
        `;

        setTimeout(loadJobs, 1000);

    } catch (error) {
        console.error('Rotation failed:', error);
        statusBox.innerHTML = `<div class="status error">Rotation failed: ${error.message}</div>`;
    } finally {
        button.disabled = false;
        button.textContent = 'Rotate All Secrets';
    }
}

/**
 * Load and display backups
 */
async function loadBackups() {
    const backupsList = document.getElementById('backups-list');
    backupsList.innerHTML = '<p class="loading">Loading backups</p>';

    try {
        const data = await api.fetchBackups();

        if (data.backups && data.backups.length > 0) {
            backupsList.innerHTML = `
                <p><strong>Total Backups:</strong> ${data.backups.length}</p>
                <table style="width: 100%; border-collapse: collapse; margin-top: 15px;">
                    <thead>
                        <tr style="border-bottom: 2px solid #e1e8ed;">
                            <th style="text-align: left; padding: 10px;">Secret ID</th>
                            <th style="text-align: left; padding: 10px;">Timestamp</th>
                            <th style="text-align: left; padding: 10px;">Encrypted</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${data.backups.map(backup => `
                            <tr style="border-bottom: 1px solid #e1e8ed;">
                                <td style="padding: 10px;">${backup.secret_id || 'N/A'}</td>
                                <td style="padding: 10px;">${backup.timestamp || 'N/A'}</td>
                                <td style="padding: 10px;">${backup.encrypted ? 'Yes' : 'No'}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
        } else {
            backupsList.innerHTML = '<p>No backups found.</p>';
        }

    } catch (error) {
        console.error('Failed to load backups:', error);
        backupsList.innerHTML = `<div class="status error">Failed to load backups: ${error.message}</div>`;
    }
}

/**
 * Load and display backup health
 */
async function loadBackupHealth() {
    const healthInfo = document.getElementById('health-info');
    healthInfo.innerHTML = '<p class="loading">Loading health metrics</p>';

    try {
        const data = await api.fetchBackupHealth();

        const statusClass = data.status === 'healthy' ? 'success' : 'error';

        healthInfo.innerHTML = `
            <div class="status ${statusClass}">
                <h3>Health Status: ${data.status || 'Unknown'}</h3>
                <p><strong>Success Rate:</strong> ${data.success_rate || 0}%</p>
                <p><strong>Total Backups:</strong> ${data.total_backups || 0}</p>
                <p><strong>Verified:</strong> ${data.verified || 0}</p>
                <p><strong>Failed:</strong> ${data.failed || 0}</p>
                ${data.last_verification ? `<p><strong>Last Verification:</strong> ${data.last_verification}</p>` : ''}
            </div>
        `;

    } catch (error) {
        console.error('Failed to load backup health:', error);
        healthInfo.innerHTML = `<div class="status error">Failed to load health metrics: ${error.message}</div>`;
    }
}

window.loadJobs = loadJobs;
window.rotateAll = rotateAll;
window.loadBackups = loadBackups;
window.loadBackupHealth = loadBackupHealth;