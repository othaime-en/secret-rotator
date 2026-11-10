/*
 * Backup health page: "Run verification now".
 *
 * POST /api/verification/run starts a background verification (409 means
 * one is already running, which is joined rather than restarted). The page
 * then polls GET /api/verification/status and reloads when the run ends so
 * the new result shows. The outcome is passed across the reload with
 * SR.flash. If the page loads while a run is in progress it resumes
 * polling.
 */
(function () {
    "use strict";

    var POLL_MS = 2000;
    var MAX_POLL_ERRORS = 5;
    var tracking = false;

    function button() {
        return document.querySelector('[data-action="run-verification"]');
    }

    function show(message, failed) {
        var notice = document.getElementById("notice");
        if (!notice) {
            return;
        }
        notice.hidden = false;
        notice.classList.toggle("is-error", !!failed);
        document.getElementById("notice-text").textContent = message;
    }

    function setBusy(busy) {
        var run = button();
        if (run) {
            run.disabled = busy;
        }
    }

    function sleep(ms) {
        return new Promise(function (resolve) {
            window.setTimeout(resolve, ms);
        });
    }

    function requestError(error) {
        if (error.status === 429) {
            return "Too many verification requests. Wait a minute and try again.";
        }
        return error.message;
    }

    async function track() {
        if (tracking) {
            return;
        }
        tracking = true;
        setBusy(true);
        show("Verifying backups. This can take a few minutes.", false);

        var errors = 0;
        while (true) {
            var run = null;
            try {
                run = (await SR.api.get("/api/verification/status")).run;
                errors = 0;
            } catch (error) {
                errors += 1;
                if (errors >= MAX_POLL_ERRORS) {
                    show("Lost contact with the verification: " + error.message, true);
                    tracking = false;
                    setBusy(false);
                    return;
                }
                await sleep(POLL_MS);
                continue;
            }

            if (!run) {
                // Not known to this server process (restarted, or another
                // instance). The finished report, if any, is in the log.
                show("Could not find the verification run. Reload to see the latest results.", true);
                tracking = false;
                setBusy(false);
                return;
            }

            if (run.status === "completed") {
                var report = run.report || { total: 0, verified: 0, failed: 0 };
                SR.flash.stash(
                    "Verification finished: " + report.verified + " of " + report.total + " backups verified",
                    report.failed > 0
                );
                window.location.reload();
                return;
            }
            if (run.status === "failed") {
                show(run.error || "Verification failed.", true);
                tracking = false;
                setBusy(false);
                return;
            }
            await sleep(POLL_MS);
        }
    }

    async function start() {
        setBusy(true);
        show("Starting verification", false);
        try {
            await SR.api.post("/api/verification/run");
        } catch (error) {
            if (error.status !== 409) {
                show("Could not start verification: " + requestError(error), true);
                setBusy(false);
                return;
            }
            // 409: already running; fall through and follow that run.
        }
        await track();
    }

    document.addEventListener("click", function (event) {
        var run = event.target.closest('[data-action="run-verification"]');
        if (run && !run.disabled) {
            start();
        }
    });

    var flash = SR.flash.consume();
    if (flash) {
        show(flash.message, flash.failed);
    }

    var state = document.getElementById("verify-state");
    if (state && state.getAttribute("data-running") === "true") {
        track();
    }
})();
