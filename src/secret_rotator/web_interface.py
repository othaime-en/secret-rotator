import json
import threading
from urllib.parse import parse_qs, urlparse, unquote
from http.server import HTTPServer, BaseHTTPRequestHandler
from secret_rotator.utils.logger import logger


class RotationWebHandler(BaseHTTPRequestHandler):
    """Simple web interface for rotation system"""

    def __init__(self, rotation_engine, *args, **kwargs):
        self.rotation_engine = rotation_engine
        super().__init__(*args, **kwargs)

    def do_GET(self):
        """Handle GET requests"""
        if self.path == "/":
            self._serve_dashboard()
        elif self.path == "/api/status":
            self._serve_status()
        elif self.path == "/api/jobs":
            self._serve_jobs()
        elif self.path.startswith("/api/backups/"):
            self._serve_backup_detail()
        elif self.path.startswith("/api/backups"):
            self._serve_backups()
        # NEW: Backup health endpoints
        elif self.path == "/api/backup-health":
            self._serve_backup_health()
        elif self.path == "/api/verification-history":
            self._serve_verification_history()
        elif self.path == "/api/run-verification":
            self._run_verification_now()
        else:
            self._serve_404()

    def do_POST(self):
        """Handle POST requests"""
        if self.path == "/api/rotate":
            self._handle_rotation()
        elif self.path == "/api/restore":
            self._handle_restore()
        else:
            self._serve_404()

    def _serve_dashboard(self):
        """Serve main dashboard"""
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Secret Rotation Dashboard</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; background: #f8f9fa; }
                .container { max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
                .job { background: #f5f5f5; padding: 15px; margin: 10px 0; border-radius: 5px; border-left: 4px solid #007bff; }
                .backup { background: #fff3cd; padding: 15px; margin: 10px 0; border-radius: 5px; border-left: 4px solid #ffc107; }
                button { background: #007bff; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; margin: 5px; }
                button:hover { background: #0056b3; }
                button.danger { background: #dc3545; }
                button.danger:hover { background: #c82333; }
                button.success { background: #28a745; }
                button.success:hover { background: #218838; }
                .status { padding: 10px; margin: 10px 0; border-radius: 5px; }
                .success { background: #d4edda; color: #155724; }
                .error { background: #f8d7da; color: #721c24; }
                .info { background: #d1ecf1; color: #0c5460; }
                h1 { color: #333; border-bottom: 3px solid #007bff; padding-bottom: 10px; }
                h2 { color: #555; margin-top: 30px; }
                .tab-container { margin: 20px 0; }
                .tab { display: inline-block; padding: 10px 20px; cursor: pointer; background: #e9ecef; border-radius: 5px 5px 0 0; margin-right: 5px; }
                .tab.active { background: #007bff; color: white; }
                .tab-content { display: none; padding: 20px; border: 1px solid #dee2e6; border-radius: 0 5px 5px 5px; }
                .tab-content.active { display: block; }
                #logs { background: #2c3e50; color: #ecf0f1; padding: 15px; border-radius: 5px; font-family: 'Courier New', monospace; max-height: 300px; overflow-y: auto; font-size: 13px; }
                .backup-item { display: flex; justify-content: space-between; align-items: center; }
                .backup-info { flex-grow: 1; }
                .backup-actions { display: flex; gap: 10px; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Secret Rotation Dashboard</h1>

                <div id="status"></div>

                <div class="tab-container">
                    <div class="tab active" onclick="switchTab('jobs')">Rotation Jobs</div>
                    <div class="tab" onclick="switchTab('backups')">Backups</div>
                    <div class="tab" onclick="switchTab('health')">Backup Health</div>
                    <div class="tab" onclick="switchTab('logs')">Logs</div>
                </div>

                <div id="jobs-content" class="tab-content active">
                    <h2>Rotation Jobs</h2>
                    <div id="jobs"></div>
                    <button onclick="rotateAll()">Rotate All Secrets</button>
                </div>

                <div id="backups-content" class="tab-content">
                    <h2>Backup History</h2>
                    <div style="margin-bottom: 15px;">
                        <label for="secretFilter">Filter by Secret ID: </label>
                        <input type="text" id="secretFilter" placeholder="Enter secret ID..." style="padding: 8px; border-radius: 4px; border: 1px solid #ced4da;">
                        <button onclick="loadBackups()">Search</button>
                        <button onclick="document.getElementById('secretFilter').value=''; loadBackups();">Clear</button>
                    </div>
                    <div id="backups"></div>
                </div>

                <div id="health-content" class="tab-content">
                    <h2>Backup System Health</h2>

                    <div id="health-status"></div>

                    <div style="margin: 20px 0;">
                        <button onclick="runVerificationNow()">Run Verification Now</button>
                        <button onclick="loadVerificationHistory()">View History</button>
                    </div>

                    <h3>Recent Verification History</h3>
                    <div id="verification-history"></div>
                </div>

                <div id="logs-content" class="tab-content">
                    <h2>Recent Activity Logs</h2>
                    <div id="logs"></div>
                </div>
            </div>

            <script>
                function switchTab(tabName) {
                    document.querySelectorAll('.tab-content').forEach(content => {
                        content.classList.remove('active');
                    });
                    document.querySelectorAll('.tab').forEach(tab => {
                        tab.classList.remove('active');
                    });

                    document.getElementById(tabName + '-content').classList.add('active');
                    event.target.classList.add('active');

                    if (tabName === 'backups') {
                        loadBackups();
                    } else if (tabName === 'health') {
                        loadBackupHealth();
                        loadVerificationHistory();
                    }
                }

                function loadBackupHealth() {
                    fetch('/api/backup-health')
                        .then(response => response.json())
                        .then(data => {
                            const statusDiv = document.getElementById('health-status');

                            let statusClass = 'info';
                            if (data.status === 'healthy') statusClass = 'success';
                            if (data.status === 'warning') statusClass = 'error';
                            if (data.status === 'critical') statusClass = 'error';

                            statusDiv.innerHTML = `
                                <div class="status ${statusClass}">
                                    <h3>Status: ${data.status.toUpperCase()}</h3>
                                    <div style="margin-top: 10px;">
                                        <strong>Success Rate:</strong> ${data.success_rate}%<br>
                                        <strong>Total Backups:</strong> ${data.total_backups}<br>
                                        <strong>Verified:</strong> ${data.verified}<br>
                                        <strong>Failed:</strong> ${data.failed}<br>
                                        <strong>Last Verification:</strong> ${new Date(data.last_verification).toLocaleString()}
                                    </div>
                                </div>
                            `;
                        })
                        .catch(error => {
                            console.error('Error loading backup health:', error);
                            document.getElementById('health-status').innerHTML =
                                '<div class="status error">Error loading backup health</div>';
                        });
                }

                function runVerificationNow() {
                    if (!confirm('Run backup verification now? This may take a few minutes.')) {
                        return;
                    }

                    document.getElementById('health-status').innerHTML =
                        '<div class="status info">Running verification...</div>';

                    fetch('/api/run-verification')
                        .then(response => response.json())
                        .then(data => {
                            if (data.success) {
                                const report = data.report;
                                document.getElementById('health-status').innerHTML = `
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
                                loadBackupHealth();
                            } else {
                                document.getElementById('health-status').innerHTML =
                                    '<div class="status error">Verification failed</div>';
                            }
                        })
                        .catch(error => {
                            console.error('Error running verification:', error);
                            document.getElementById('health-status').innerHTML =
                                '<div class="status error">Error running verification</div>';
                        });
                }

                function loadVerificationHistory() {
                    fetch('/api/verification-history?days=7')
                        .then(response => response.json())
                        .then(data => {
                            const historyDiv = document.getElementById('verification-history');

                            if (data.history.length === 0) {
                                historyDiv.innerHTML = '<div class="status info">No verification history available</div>';
                                return;
                            }

                            let html = '<table style="width: 100%; border-collapse: collapse;">';
                            html += '<tr style="background: #f5f5f5;"><th>Date</th><th>Total</th><th>Verified</th><th>Failed</th><th>Success Rate</th></tr>';

                            data.history.forEach(report => {
                                const successRate = ((report.verified / report.total_backups) * 100).toFixed(1);
                                const statusColor = successRate >= 95 ? '#28a745' : '#dc3545';

                                html += `
                                    <tr style="border-bottom: 1px solid #ddd;">
                                        <td style="padding: 8px;">${new Date(report.timestamp).toLocaleString()}</td>
                                        <td style="padding: 8px;">${report.total_backups}</td>
                                        <td style="padding: 8px;">${report.verified}</td>
                                        <td style="padding: 8px;">${report.failed}</td>
                                        <td style="padding: 8px; color: ${statusColor}; font-weight: bold;">${successRate}%</td>
                                    </tr>
                                `;
                            });

                            html += '</table>';
                            historyDiv.innerHTML = html;
                        })
                        .catch(error => {
                            console.error('Error loading verification history:', error);
                            document.getElementById('verification-history').innerHTML =
                                '<div class="status error">Error loading history</div>';
                        });
                }

                function loadJobs() {
                    fetch('/api/jobs')
                        .then(response => response.json())
                        .then(data => {
                            const jobsDiv = document.getElementById('jobs');
                            jobsDiv.innerHTML = data.jobs.map(job =>
                                `<div class="job">
                                    <strong>${job.name}</strong><br>
                                    Provider: ${job.provider} | Rotator: ${job.rotator}<br>
                                    Secret ID: <code>${job.secret_id}</code>
                                </div>`
                            ).join('');
                        })
                        .catch(error => {
                            console.error('Error loading jobs:', error);
                        });
                }

                function loadBackups() {
                    const secretFilter = document.getElementById('secretFilter').value;
                    const url = secretFilter ? `/api/backups?secret_id=${encodeURIComponent(secretFilter)}` : '/api/backups';

                    fetch(url)
                        .then(response => response.json())
                        .then(data => {
                            const backupsDiv = document.getElementById('backups');
                            if (data.backups.length === 0) {
                                backupsDiv.innerHTML = '<div class="status info">No backups found.</div>';
                                return;
                            }

                            backupsDiv.innerHTML = data.backups.map(backup => {
                                const encodedPath = encodeURIComponent(backup.backup_file);
                                return `<div class="backup">
                                    <div class="backup-item">
                                        <div class="backup-info">
                                            <strong>${backup.secret_id}</strong><br>
                                            <small>Created: ${new Date(backup.backup_created).toLocaleString()}</small><br>
                                            <small>File: ${backup.backup_file.split('/').pop()}</small>
                                        </div>
                                        <div class="backup-actions">
                                            <button class="success" onclick="viewBackup('${encodedPath}')">View</button>
                                            <button class="danger" onclick="confirmRestore('${backup.backup_file}', '${backup.secret_id}')">Restore</button>
                                        </div>
                                    </div>
                                </div>`;
                            }).join('');
                        })
                        .catch(error => {
                            console.error('Error loading backups:', error);
                            document.getElementById('backups').innerHTML = '<div class="status error">Error loading backups</div>';
                        });
                }

                function viewBackup(encodedBackupFile) {
                    fetch(`/api/backups/${encodedBackupFile}`)
                        .then(response => response.json())
                        .then(data => {
                            const details = `
Secret ID: ${data.secret_id}
Timestamp: ${new Date(data.backup_created).toLocaleString()}
Old Value: ${data.old_value.substring(0, 20)}... (truncated)
New Value: ${data.new_value.substring(0, 20)}... (truncated)
                            `.trim();
                            alert('Backup Details:\\n\\n' + details);
                        })
                        .catch(error => {
                            alert('Error viewing backup details');
                            console.error(error);
                        });
                }

                function confirmRestore(backupFile, secretId) {
                    if (confirm(`Are you sure you want to restore the backup for "${secretId}"?\\n\\nThis will replace the current secret value with the old value from the backup.`)) {
                        restoreBackup(backupFile);
                    }
                }

                function restoreBackup(backupFile) {
                    document.getElementById('status').innerHTML = '<div class="status info">Restoring backup...</div>';

                    fetch('/api/restore', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ backup_file: backupFile })
                    })
                        .then(response => response.json())
                        .then(data => {
                            if (data.success) {
                                document.getElementById('status').innerHTML =
                                    `<div class="status success">Successfully restored backup for ${data.secret_id}</div>`;
                                addLog(`Restored backup for ${data.secret_id}`);
                                loadBackups();
                            } else {
                                document.getElementById('status').innerHTML =
                                    `<div class="status error">Failed to restore backup: ${data.error}</div>`;
                            }
                        })
                        .catch(error => {
                            document.getElementById('status').innerHTML =
                                '<div class="status error">Error during restoration</div>';
                            console.error(error);
                        });
                }

                function rotateAll() {
                    document.getElementById('status').innerHTML = '<div class="status info">Rotation in progress...</div>';

                    fetch('/api/rotate', { method: 'POST' })
                        .then(response => response.json())
                        .then(data => {
                            const successful = Object.values(data.results).filter(r => r).length;
                            const total = Object.keys(data.results).length;
                            const statusClass = successful === total ? 'success' : 'error';

                            document.getElementById('status').innerHTML =
                                `<div class="status ${statusClass}">Rotation complete: ${successful}/${total} successful</div>`;

                            const logs = Object.entries(data.results)
                                .map(([job, success]) => `[${new Date().toLocaleTimeString()}] ${job}: ${success ? 'SUCCESS' : 'FAILED'}`)
                                .join('\\n');
                            addLog(logs);
                        })
                        .catch(error => {
                            document.getElementById('status').innerHTML =
                                '<div class="status error">Error during rotation</div>';
                            console.error(error);
                        });
                }

                function addLog(message) {
                    const logsDiv = document.getElementById('logs');
                    const timestamp = new Date().toLocaleTimeString();
                    logsDiv.innerHTML = `[${timestamp}] ${message}\\n` + logsDiv.innerHTML;
                }

                loadJobs();
                addLog('Dashboard loaded');
            </script>
        </body>
        </html>
        """

        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

    def _serve_status(self):
        """Serve system status"""
        status = {
            "status": "running",
            "providers": len(self.rotation_engine.providers),
            "rotators": len(self.rotation_engine.rotators),
            "jobs": len(self.rotation_engine.rotation_jobs),
        }
        self._send_json(status)

    def _serve_jobs(self):
        """Serve job configurations"""
        jobs_data = {"jobs": self.rotation_engine.rotation_jobs}
        self._send_json(jobs_data)

    def _serve_backups(self):
        """Serve list of backups"""
        try:
            parsed_url = urlparse(self.path)
            params = parse_qs(parsed_url.query)
            secret_id = params.get("secret_id", [None])[0]

            backups = self.rotation_engine.backup_manager.list_backups(secret_id)
            self._send_json({"backups": backups})
        except Exception as e:
            logger.error(f"Error serving backups: {e}")
            self._send_json({"error": str(e)}, 500)

    def _serve_backup_detail(self):
        """Serve detailed backup information"""
        try:
            encoded_path = self.path.split("/api/backups/")[1]
            backup_file = unquote(encoded_path)

            logger.info(f"Attempting to load backup from: {backup_file}")
            backup_data = self.rotation_engine.backup_manager.restore_backup(backup_file)
            self._send_json(backup_data)
        except FileNotFoundError:
            logger.warning(
                f"Backup file not found: {backup_file if 'backup_file' in locals() else 'unknown'}"
            )
            self._send_json({"error": "Backup not found"}, 404)
        except Exception as e:
            logger.error(f"Error serving backup detail: {e}")
            self._send_json({"error": str(e)}, 500)

    def _serve_backup_health(self):
        """Serve backup health metrics"""
        try:
            if hasattr(self.rotation_engine, "scheduler") and self.rotation_engine.scheduler:
                health = self.rotation_engine.scheduler.get_backup_health()
                self._send_json(health)
            else:
                self._send_json({"error": "Scheduler not available"}, 503)
        except Exception as e:
            logger.error(f"Error serving backup health: {e}")
            self._send_json({"error": str(e)}, 500)

    def _serve_verification_history(self):
        """Serve backup verification history"""
        try:
            from urllib.parse import parse_qs, urlparse

            parsed_url = urlparse(self.path)
            params = parse_qs(parsed_url.query)
            days = int(params.get("days", ["7"])[0])

            if hasattr(self.rotation_engine, "scheduler") and self.rotation_engine.scheduler:
                history = self.rotation_engine.scheduler.get_verification_history(days)
                self._send_json({"history": history, "days": days})
            else:
                self._send_json({"error": "Scheduler not available"}, 503)
        except Exception as e:
            logger.error(f"Error serving verification history: {e}")
            self._send_json({"error": str(e)}, 500)

    def _run_verification_now(self):
        """Trigger manual backup verification"""
        try:
            if hasattr(self.rotation_engine, "scheduler") and self.rotation_engine.scheduler:
                logger.info("Manual backup verification triggered via web interface")
                report = self.rotation_engine.scheduler.run_verification_now()
                self._send_json({"success": True, "report": report})
            else:
                self._send_json({"error": "Scheduler not available"}, 503)
        except Exception as e:
            logger.error(f"Error running verification: {e}")
            self._send_json({"error": str(e)}, 500)

    def _handle_rotation(self):
        """Handle rotation request"""
        try:
            results = self.rotation_engine.rotate_all_secrets()
            self._send_json({"results": results})
        except Exception as e:
            logger.error(f"Error during rotation: {e}")
            self._send_json({"error": str(e)}, 500)

    def _handle_restore(self):
        """Handle backup restoration request"""
        try:
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode("utf-8"))

            backup_file = data.get("backup_file")
            if not backup_file:
                self._send_json({"success": False, "error": "backup_file required"}, 400)
                return

            logger.info(f"Restoring backup from: {backup_file}")

            backup_data = self.rotation_engine.backup_manager.restore_backup(backup_file)
            secret_id = backup_data["secret_id"]
            old_value = backup_data["old_value"]

            provider = list(self.rotation_engine.providers.values())[0]

            success = provider.update_secret(secret_id, old_value)

            if success:
                logger.info(f"Successfully restored backup for {secret_id} from {backup_file}")
                self._send_json(
                    {
                        "success": True,
                        "secret_id": secret_id,
                        "message": f"Restored backup for {secret_id}",
                    }
                )
            else:
                self._send_json({"success": False, "error": "Failed to update secret"}, 500)

        except FileNotFoundError:
            self._send_json({"success": False, "error": "Backup file not found"}, 404)
        except Exception as e:
            logger.error(f"Error during restoration: {e}")
            self._send_json({"success": False, "error": str(e)}, 500)

    def _send_json(self, data, status=200):
        """Send JSON response"""
        self.send_response(status)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _serve_404(self):
        """Serve 404 error"""
        self.send_response(404)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h1>404 Not Found</h1>")

    def log_message(self, format, *args):
        """Override to use our logger instead of printing"""
        logger.info(f"Web request: {format % args}")


class WebServer:
    """Main web server class to manage the HTTP server"""

    def __init__(self, rotation_engine, port=8080):
        self.rotation_engine = rotation_engine
        self.port = port
        self.server = None
        self.thread = None

    def start(self):
        """Start the web server in a separate thread"""
        handler = lambda *args, **kwargs: RotationWebHandler(self.rotation_engine, *args, **kwargs)
        self.server = HTTPServer(("localhost", self.port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logger.info(f"Web server started on http://localhost:{self.port}")

    def stop(self):
        """Stop the web server"""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        logger.info("Web server stopped")
