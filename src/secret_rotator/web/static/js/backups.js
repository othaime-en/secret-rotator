/*
 * Backups page: details drawer and restore.
 *
 * - [data-action="view-backup"] opens the drawer and loads the backup's
 *   metadata from GET /api/backups/<file>. Secret values arrive already
 *   masked and are inserted with textContent, never as HTML.
 * - [data-open="m-restore"] (row button or the drawer's button) opens the
 *   confirmation dialog. Confirming POSTs /api/restore with the backup's
 *   file name; the server applies the old value, the browser never sees it.
 * - The outcome is stashed with SR.flash and shown after the reload.
 */
(function () {
    "use strict";

    var selected = null; // {file, secret, created}
    var loadToken = 0;

    function el(id) {
        return document.getElementById(id);
    }

    function showNotice(message, failed) {
        var notice = el("notice");
        if (!notice) {
            return;
        }
        notice.hidden = false;
        notice.classList.toggle("is-error", !!failed);
        el("notice-text").textContent = message;
    }

    function setRestoreTarget(target) {
        selected = target;
        el("m-restore-secret").textContent = target.secret;
        el("m-restore-file").textContent = target.file;
        el("d-backup-restore").disabled = false;
    }

    function resetDrawer(target) {
        el("d-backup-secret").textContent = target.secret;
        el("d-backup-file").textContent = target.file;
        var created = el("d-backup-created");
        if (target.created && window.SRTime) {
            created.textContent = window.SRTime.format(new Date(target.created), "datetime");
        } else {
            created.textContent = "—";
        }
        el("d-backup-encrypted").textContent = "—";
        el("d-backup-old").textContent = "—";
        el("d-backup-new").textContent = "—";
        el("d-backup-error").hidden = true;
        el("d-backup-loading").hidden = false;
    }

    async function viewBackup(button) {
        var target = {
            file: button.getAttribute("data-file"),
            secret: button.getAttribute("data-secret"),
            created: button.getAttribute("data-created"),
        };
        setRestoreTarget(target);
        resetDrawer(target);
        window.SROverlay.open("d-backup", button);

        var token = ++loadToken;
        try {
            var detail = await SR.api.get("/api/backups/" + encodeURIComponent(target.file));
            if (token !== loadToken) {
                return; // a newer View click superseded this one
            }
            el("d-backup-encrypted").textContent = detail.encrypted ? "Yes" : "No";
            el("d-backup-old").textContent = detail.old_value;
            el("d-backup-new").textContent = detail.new_value;
        } catch (error) {
            if (token !== loadToken) {
                return;
            }
            var message = el("d-backup-error");
            message.textContent = "Could not load this backup: " + error.message;
            message.hidden = false;
        } finally {
            if (token === loadToken) {
                el("d-backup-loading").hidden = true;
            }
        }
    }

    async function confirmRestore(button) {
        if (!selected) {
            return;
        }
        var target = selected;
        button.disabled = true;
        try {
            await SR.api.post("/api/restore", { backup_file: target.file });
            SR.flash.stash("Restored " + target.secret + " from backup " + target.file, false);
            window.location.reload();
        } catch (error) {
            window.SROverlay.close(false);
            showNotice("Could not restore " + target.secret + ": " + error.message, true);
            button.disabled = false;
        }
    }

    document.addEventListener("click", function (event) {
        var view = event.target.closest('[data-action="view-backup"]');
        if (view) {
            viewBackup(view);
            return;
        }

        var restore = event.target.closest('[data-open="m-restore"]');
        if (restore && !restore.disabled) {
            if (restore.hasAttribute("data-file")) {
                setRestoreTarget({
                    file: restore.getAttribute("data-file"),
                    secret: restore.getAttribute("data-secret"),
                    created: "",
                });
            }
            return;
        }

        var confirm = event.target.closest('[data-action="confirm-restore"]');
        if (confirm) {
            confirmRestore(confirm);
        }
    });

    var flash = SR.flash.consume();
    if (flash) {
        showNotice(flash.message, flash.failed);
    }
})();
